#!/usr/bin/env python3
"""Bounded, local prompt search; holdout gates promotion and models stay fixed."""
import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import statistics
import subprocess
import sys
import time

# The parent enforces process/time bounds; MLX's memory setting is only a
# guideline. All sizes are bytes, except the RSS value returned by macOS ps.
_RSS_LIMIT = 1024 * 1024 * 1024
_MLX_LIMIT = 768 * 1024 * 1024
_CACHE_LIMIT = 32 * 1024 * 1024
_TRIAL_SECONDS = 30
_SEARCH_SECONDS = 180
_POLL_SECONDS = 0.25
_NORMAL_PRESSURE = '1'
_KINDS = ('repeat', 'failure', 'duplicate')
_HOME = Path.home() / '.local/share/yoyo/decider'
_ROOT = Path(__file__).resolve().parent


def _load_module(path):
    # Freeze and reuse the evaluated helper, without importing MLX in the
    # supervising process or changing the installed helper during search.
    spec = importlib.util.spec_from_file_location('frozen_helper', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(path):
    # Content hashes make dataset or source edits visible in the ledger.
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pressure_allowed(policy='normal'):
    # Missing system telemetry fails closed; research must not add a model
    # load when this Mac is already reporting memory pressure.
    try:
        value = subprocess.check_output(['sysctl', '-n', 'kern.memorystatus_vm_pressure_level'],
                                        text=True, timeout=2).strip()
        allowed = (_NORMAL_PRESSURE, '2') if policy == 'warning' else (_NORMAL_PRESSURE,)
        return value in allowed
    except (OSError, subprocess.SubprocessError):
        return False


def _metrics(cases, rows):
    # Raw classification accuracy, advice errors and advice coverage are
    # separate. Returning unclear for everything cannot improve raw accuracy.
    if len(rows) != len(cases) or [r['id'] for r in rows] != [c['id'] for c in cases]:
        raise ValueError('missing, reordered or duplicate result rows')
    result = {'correct': 0, 'total': len(cases), 'by_kind': {}, 'issued': 0,
              'wrong_issued': 0, 'correct_issued': 0}
    for case, row in zip(cases, rows):
        kind = case['kind']
        correct = row['candidate'] == case['expected']
        result['correct'] += int(correct)
        result['by_kind'][kind] = result['by_kind'].get(kind, 0) + int(correct)
        if row['choice'] == 'unclear':
            continue
        result['issued'] += 1
        accepted = row['choice'] == case['expected']
        result['correct_issued'] += int(accepted)
        result['wrong_issued'] += int(not accepted)
    result['median_seconds'] = statistics.median(r['seconds'] for r in rows)
    return result


def _improves(base, candidate):
    # Require a real accuracy gain without sacrificing a task kind or useful
    # correct advice. No score-threshold optimization is allowed in this run.
    return (candidate['correct'] > base['correct']
            and all(candidate['by_kind'].get(k, -1) >= v for k, v in base['by_kind'].items())
            and candidate['wrong_issued'] <= base['wrong_issued']
            and candidate['correct_issued'] >= base['correct_issued'])


def _variants(helper):
    # Candidate proposals use only the previous development failures and
    # published findings about instruction clarity and option-order bias.
    base = {'name': 'baseline', 'schemas': helper._SCHEMAS, 'render': 'json', 'labels': 'keys', 'order': 'forward'}
    concise = {
        'repeat': {'question': 'Compare the two attempts. Which description is supported by the concrete actions and results?',
                   'criteria': {'repeated': 'Same failed approach again, without new evidence.',
                                'new_evidence': 'A different attempted fix, a new diagnostic result, or a passing test.',
                                'unclear': 'The reports do not describe concrete actions and results.'}},
        'failure': {'question': 'What does the error directly show is blocking the task?',
                    'criteria': {'account': 'Provider login, subscription, account quota, or service access.',
                                 'environment': 'Missing software, local setup, disk space, network, or unavailable dependency.',
                                 'implementation': 'A defect in the changed code, demonstrated by a compiler, test, or reproduction.',
                                 'unclear': 'No failure was shown, or there is insufficient evidence to identify a cause.'}},
        'duplicate': {'question': 'Compare the cause and code location of the two findings. What is supported?',
                      'criteria': {'same': 'The same specific defect at the same location.',
                                   'different': 'Different defects, different locations, or contradictory claims.',
                                   'unclear': 'The cause or location is missing or too vague to compare.'}}
    }
    explicit = copy.deepcopy(concise)
    explicit['repeat']['question'] = ('Classify the attempt comparison. Rewording a status or varying the same unsuccessful workaround is repetition. '
                                      'A new concrete diagnosis or different fix is new evidence even if the bug remains. '
                                      'Generic progress claims without actions and results are insufficient. Which category fits?')
    explicit['duplicate']['question'] = ('Are these the same review issue? Require both the same specific causal defect and the same affected location. '
                                         'Shared words or a shared line number alone do not prove duplication. '
                                         'Contradictions are different issues. Missing cause or location is insufficient. Which category fits?')
    output = [base]
    for name, schemas, render, labels, order in (
        ('descriptions', helper._SCHEMAS, 'json', 'descriptions', 'forward'),
        ('plain_evidence', helper._SCHEMAS, 'plain', 'keys', 'forward'),
        ('concise', concise, 'json', 'descriptions', 'forward'),
        ('explicit', explicit, 'json', 'descriptions', 'forward'),
        ('explicit_plain', explicit, 'plain', 'descriptions', 'forward'),
        ('explicit_reverse', explicit, 'plain', 'descriptions', 'reverse'),
    ):
        output.append({'name': name, 'schemas': schemas, 'render': render, 'labels': labels, 'order': order})

    # Round three keeps the previous winner as a reference and tests whether
    # baseline repeat wording preserves useful warnings at the same cutoff.
    previous = {'name': 'previous_best', 'per_kind': {
        'repeat': output[2], 'failure': output[3], 'duplicate': output[2]}}
    preserve = copy.deepcopy(previous)
    preserve['name'] = 'preserve_repeat'
    preserve['per_kind']['repeat'] = base
    output.extend((previous, preserve))

    # Vague reports caused prior errors. Offer insufficient evidence as an
    # explicit category, without changing confidence or abstention rules.
    grounded = {
        'repeat': {
            'question': 'Is the attempt comparison supported by concrete actions and results, and what changed?',
            'criteria': {
                'repeated': 'Both reports describe the same failed action or workaround without new evidence; changing its retry count or timeout alone is repetition.',
                'new_evidence': 'The latest report describes a different fix, a specific new diagnostic observation, or a verified result.',
                'unclear': 'An attempt gives only vague status or omits what was tried and observed, so the comparison is unsupported.'}},
        'failure': {
            'question': 'Which blocker is established by the reported error, rather than merely mentioned?',
            'criteria': {
                'account': 'The remote provider rejects the login, account entitlement, subscription allowance, or service access.',
                'environment': 'A local tool, file permission, disk, network, configuration, or required service is unavailable or incorrectly set up.',
                'implementation': 'A compiler, test, or reproduction identifies incorrect behavior in the changed source code.',
                'unclear': 'The report lacks diagnostic evidence, describes only a guess, or shows a successful run without a blocker.'}},
        'duplicate': {
            'question': 'Which comparison of the two review findings is supported by their stated defects and code locations?',
            'criteria': {
                'same': 'Both reports identify the same concrete causal defect at the same identifiable code location.',
                'different': 'Both reports identify concrete defects, but the defects or code locations differ, or their claims contradict.',
                'unclear': 'A report lacks a specific defect or identifiable code location; vague shared wording cannot establish sameness or difference.'}}
    }
    for name, render, order in (
        ('grounded_json', 'json', 'forward'),
        ('grounded_plain', 'plain', 'forward'),
        ('grounded_reverse', 'plain', 'reverse'),
    ):
        output.append({'name': name, 'schemas': grounded, 'render': render,
                       'labels': 'descriptions', 'order': order})
    return output


def _prompt(driver, check, variant):
    # Preserve the publisher's state-first answer-slot format while varying
    # only human-readable evidence and the offered option descriptions.
    config = variant.get('per_kind', {}).get(check['kind'], variant)
    schema = config['schemas'][check['kind']]
    state = check['state']
    context = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    if config['render'] == 'plain' and isinstance(state, dict):
        context = '\n\n'.join(f'{key.upper()}:\n{value}' for key, value in state.items())
    keys = list(schema['criteria'])
    if config['order'] == 'reverse':
        keys.reverse()
    text = '\n\nQuestion: ' + schema['question'] + '\nOptions:'
    for index, key in enumerate(keys):
        option = schema['criteria'][key]
        if config['labels'] == 'keys':
            option = key + ': ' + option
        text += f'\n({chr(ord("A") + index)}) {option}'
    text += '\nAnswer: ('
    return driver._encode('Context:\n' + context) + driver._encode(text), keys


def _score(driver, helper, check, variant):
    # Forward passes are sequential and do not retain KV state. Scores map
    # back to canonical label IDs before the unchanged abstention policy.
    mx = driver._mx
    ids, keys = _prompt(driver, check, variant)
    if len(ids) > helper._MAX_TOKENS:
        return {'candidate': 'invalid', 'choice': 'unclear', 'reason': 'input_too_long', 'tokens': len(ids)}
    model = driver._model.language_model.model
    hidden = model(mx.array([ids]))[:, -1:, :]
    logits = model.embed_tokens.as_linear(hidden)[0, 0]
    labels = mx.array([tokens[0] for tokens in driver._letters[:len(keys)]])
    scores = mx.softmax(logits[labels].astype(mx.float32) / helper._TEMPERATURE)
    mx.eval(scores)
    mapped = dict(zip(keys, scores.tolist()))
    canonical = list(helper._SCHEMAS[check['kind']]['criteria'])
    result = helper._choose(check['kind'], [mapped[k] for k in canonical])
    reason = helper._insufficient(check)
    if reason:
        result.update(choice='unclear', reason=reason)
    result['tokens'] = len(ids)
    if mx.get_peak_memory() > _MLX_LIMIT:
        raise MemoryError('MLX allocation budget exceeded')
    return result


def _child(args):
    # The only process that imports MLX is disposable and runs at lower CPU
    # priority. Parent -> one child -> local weights -> scored rows -> exit.
    os.nice(10)
    helper = _load_module(args.helper)
    helper._CACHE_BYTES = _CACHE_LIMIT
    driver = helper._MlxDriver(args.model)
    driver._mx.set_memory_limit(_MLX_LIMIT)
    variant = json.loads(args.variant.read_text())
    cases = json.loads(args.data.read_text())['cases']
    rows = []
    for case in cases:
        start = time.monotonic()
        result = _score(driver, helper, case, variant)
        rows.append({'id': case['id'], **result, 'seconds': time.monotonic() - start})
    output = {'rows': rows, 'metrics': _metrics(cases, rows), 'mlx_peak_bytes': driver._mx.get_peak_memory(),
              'rss_peak_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    args.result.write_text(json.dumps(output, indent=2) + '\n')


def _trial(args, name, variant, data, deadline):
    # Fail closed on pressure and budgets; reap the process group on every
    # exception. Temporary failure never becomes a successful candidate.
    if time.monotonic() >= deadline:
        raise RuntimeError('search deadline reached; no model loaded')
    if not _pressure_allowed(args.pressure):
        raise RuntimeError('macOS memory pressure exceeds the selected policy; no model loaded')
    variant_path = args.out / f'{name}.variant.json'
    variant_path.write_text(json.dumps(variant, indent=2) + '\n')
    result_path = args.out / f'{name}.result.json'
    command = [sys.executable, str(args.out / 'runner.py'), '--child', '--helper', str(args.out / 'helper.py'),
               '--model', str(args.model), '--variant', str(variant_path), '--data', str(data), '--result', str(result_path)]
    started = time.monotonic()
    peak = 0
    with (args.out / f'{name}.log').open('w') as log:
        proc = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
        try:
            while proc.poll() is None:
                if time.monotonic() > min(deadline, started + _TRIAL_SECONDS):
                    raise RuntimeError('trial/search deadline exceeded')
                if not _pressure_allowed(args.pressure):
                    raise RuntimeError('memory pressure rose; stopped the trial')
                rss = subprocess.run(['ps', '-o', 'rss=', '-p', str(proc.pid)], capture_output=True, text=True, timeout=2)
                if rss.stdout.strip():
                    peak = max(peak, int(rss.stdout.strip()) * 1024)
                if peak > _RSS_LIMIT:
                    raise RuntimeError('process RSS exceeded 1 GiB')
                time.sleep(_POLL_SECONDS)
            if proc.returncode != 0:
                raise RuntimeError(f'trial failed with exit {proc.returncode}; inspect {name}.log')
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait()
    result = json.loads(result_path.read_text())
    if result['rss_peak_bytes'] > _RSS_LIMIT or result['mlx_peak_bytes'] > _MLX_LIMIT:
        raise RuntimeError('completed trial exceeded its memory budget')
    result.update(name=name, variant=variant, wall_seconds=time.monotonic() - started,
                  sampled_rss_bytes=peak)
    return result


def _record(path, result):
    # Append every completed trial before selection so failed attempts and
    # rejected candidates remain inspectable across fresh agent contexts.
    with path.open('a') as handle:
        handle.write(json.dumps(result) + '\n')
    print(json.dumps({k: result[k] for k in ('name', 'metrics', 'wall_seconds', 'rss_peak_bytes') if k in result}), flush=True)


def _search(args):
    # Freeze code and both datasets before inference. Holdout is not opened
    # by selection, and only the final selected configuration sees its scores.
    args.out.mkdir(parents=True, exist_ok=False)
    helper_path = _ROOT.parent / 'decider_mlx.py'
    (args.out / 'helper.py').write_bytes(helper_path.read_bytes())
    (args.out / 'runner.py').write_bytes(Path(__file__).read_bytes())
    for split in ('dev', 'holdout'):
        (args.out / f'{split}.json').write_bytes((args.dataset_dir / f'{split}.json').read_bytes())
    manifest = {name: _digest(args.out / name) for name in ('helper.py', 'runner.py', 'dev.json', 'holdout.json')}
    manifest.update(rss_limit=_RSS_LIMIT, mlx_limit=_MLX_LIMIT, seconds=_SEARCH_SECONDS,
                    pressure=args.pressure, supervisor_pid=os.getpid())
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    deadline = time.monotonic() + _SEARCH_SECONDS
    helper = _load_module(args.out / 'helper.py')
    variants = _variants(helper)
    results = []
    ledger = args.out / 'ledger.jsonl'
    for variant in variants:
        result = _trial(args, 'dev-' + variant['name'], variant, args.out / 'dev.json', deadline)
        results.append(result)
        _record(ledger, result)

    # Recombine the best non-regressing instruction for each independent task.
    # This uses development results only and adds one bounded adaptive trial.
    per_kind = {}
    for kind in _KINDS:
        best = max(results, key=lambda r: r['metrics']['by_kind'][kind])
        per_kind[kind] = best['variant']
    mixed = {'name': 'combined', 'per_kind': per_kind}
    result = _trial(args, 'dev-combined', mixed, args.out / 'dev.json', deadline)
    results.append(result)
    _record(ledger, result)
    base = results[0]
    eligible = [r for r in results[1:] if _improves(base['metrics'], r['metrics'])]
    selected = max(eligible, key=lambda r: (r['metrics']['correct'], r['metrics']['correct_issued'], -r['metrics']['median_seconds']), default=base)
    (args.out / 'selected.json').write_text(json.dumps(selected['variant'], indent=2) + '\n')
    for name in ('helper.py', 'runner.py', 'dev.json', 'holdout.json'):
        if _digest(args.out / name) != manifest[name]:
            raise RuntimeError(f'frozen file changed during search: {name}')

    hold_base = _trial(args, 'holdout-baseline', base['variant'], args.out / 'holdout.json', deadline)
    _record(ledger, hold_base)
    hold_selected = hold_base
    if selected is not base:
        hold_selected = _trial(args, 'holdout-selected', selected['variant'], args.out / 'holdout.json', deadline)
        _record(ledger, hold_selected)
    summary = {'selected': selected['variant']['name'], 'dev_baseline': base['metrics'], 'dev_selected': selected['metrics'],
               'holdout_baseline': hold_base['metrics'], 'holdout_selected': hold_selected['metrics'],
               'promotion_passed': selected is not base and _improves(hold_base['metrics'], hold_selected['metrics']),
               'trials': len(results) + 1 + int(selected is not base)}
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)


def _interrupted(signum, frame):
    # Turn termination into stack unwinding so the trial's finally block
    # reaps its inference process before the supervisor exits.
    raise KeyboardInterrupt


def _main():
    # Search is an explicit developer action and shares the installed advice
    # lock. Ordinary Yoyo calls never enter this research path.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--dataset-dir', type=Path, default=_ROOT,
                        help='Directory with frozen dev.json and holdout.json; new rounds require a new holdout')
    parser.add_argument('--model', type=Path, default=_HOME / 'model')
    parser.add_argument('--pressure', choices=['normal', 'warning'], default='normal',
                        help='Default requires normal memory pressure; warning permits warning but stops at critical')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    for name in ('helper', 'variant', 'data', 'result'):
        parser.add_argument('--' + name, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.child:
            _child(args)
            return 0
        if args.out is None:
            parser.error('--out is required')
        signal.signal(signal.SIGTERM, _interrupted)
        with (_HOME / 'advice.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _search(args)
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        if args.out and args.out.is_dir():
            (args.out / 'stopped.txt').write_text(str(exc) + '\n')
        print(f'autoresearch: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(_main())
