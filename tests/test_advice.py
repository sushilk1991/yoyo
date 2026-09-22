import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest import mock

from test_yoyo import CliTestCase, ROOT, yoyo


class AdviceTests(CliTestCase):
    def _fixture(self, body):
        # A local fake runner exercises process boundaries without MLX or
        # model downloads in the regular, dependency-free test suite.
        root = Path(self._state_guard.name)
        home = root / 'decider'
        (home / 'venv/bin').mkdir(parents=True)
        (home / 'venv/bin/python').symlink_to(sys.executable)
        (home / 'model').mkdir()
        source = root / 'source'
        (source / 'extras').mkdir(parents=True)
        (source / 'install.sh').touch()
        (source / 'extras/decider_mlx.py').write_text(body)
        evidence = root / 'evidence.json'
        evidence.write_text(json.dumps({'checks': [{'id': 'failure-1', 'kind': 'failure', 'state': 'session expired'}]}))
        env = {'YOYO_DECIDER_HOME': str(home), 'YOYO_SOURCE_ROOT': str(source)}
        return home, evidence, env

    def test_advice_keeps_evidence(self):
        home, evidence, env = self._fixture(
            "import json,sys\nrequest=json.load(sys.stdin)\n"
            "print(json.dumps({'advisory':True,'decisions':[{'id':request['checks'][0]['id'],'choice':'account'}]}))\n")
        original = evidence.read_bytes()
        code, stdout, stderr = self.run_cli(['advise', '--file', str(evidence)], env=env)
        self.assertEqual(code, 0, stderr)
        self.assertTrue(json.loads(stdout)['advisory'])
        self.assertEqual(evidence.read_bytes(), original)

    def test_missing_backend(self):
        _, evidence, env = self._fixture('raise RuntimeError()')
        env['YOYO_DECIDER_HOME'] += '/missing'
        code, stdout, stderr = self.run_cli(['advise', '--file', str(evidence)], env=env)
        self.assertEqual(code, 2)
        self.assertIn('extras/README.md', stderr)
        self.assertEqual(stdout, '')

    def test_timeout_stops_runner(self):
        # Capture the PID at spawn: a busy machine can reach the deadline
        # before the child interpreter starts executing its fixture.
        _, evidence, env = self._fixture('import time\ntime.sleep(30)\n')
        spawned = []
        popen = yoyo.subprocess.Popen

        def _spawn(*args, **kwargs):
            proc = popen(*args, **kwargs)
            spawned.append(proc)
            return proc

        with mock.patch.object(yoyo.subprocess, 'Popen', side_effect=_spawn):
            code, stdout, stderr = self.run_cli(['advise', '--file', str(evidence), '--timeout', '0.2'], env=env)
        self.assertEqual(code, 2)
        self.assertIn('timed out', stderr)
        self.assertEqual(stdout, '')
        self.assertEqual(len(spawned), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(spawned[0].pid, 0)

    def test_busy_model_does_not_load(self):
        home, evidence, env = self._fixture('raise RuntimeError("must not load")\n')
        with (home / 'advice.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, stdout, stderr = self.run_cli(['advise', '--file', str(evidence)], env=env)
        self.assertEqual(code, 2)
        self.assertIn('busy', stderr)
        self.assertEqual(stdout, '')

    def test_invalid_json_never_runs(self):
        _, evidence, env = self._fixture('raise RuntimeError("must not load")\n')
        evidence.write_text('not JSON')
        code, stdout, stderr = self.run_cli(['advise', '--file', str(evidence)], env=env)
        self.assertEqual(code, 2)
        self.assertIn('Invalid advice input', stderr)

    def test_backend_failure(self):
        _, evidence, env = self._fixture('import sys\nprint("model unavailable",file=sys.stderr)\nsys.exit(1)\n')
        code, stdout, stderr = self.run_cli(['advise', '--file', str(evidence)], env=env)
        self.assertEqual(code, 2)
        self.assertIn('model unavailable', stderr)
        self.assertEqual(stdout, '')

    def test_no_non_advisory_output(self):
        _, evidence, env = self._fixture('print(\'{"advisory":false,"decisions":[]}\')\n')
        code, stdout, stderr = self.run_cli(['advise', '--file', str(evidence)], env=env)
        self.assertEqual(code, 2)
        self.assertIn('invalid advisory result', stderr)

    def _helper(self):
        # Importing the optional helper must not import its heavy runtime;
        # validation and abstention policy are testable with stdlib alone.
        spec = importlib.util.spec_from_file_location('advice_helper', ROOT / 'extras/decider_mlx.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_low_score_abstains(self):
        helper = self._helper()
        result = helper._choose('duplicate', [0.75, 0.20, 0.05])
        self.assertEqual(result['choice'], 'unclear')
        self.assertEqual(result['reason'], 'low_score')
        self.assertEqual(result['candidate'], 'same')

    def test_invalid_scores_fail(self):
        helper = self._helper()
        for scores in ([float('nan'), 0, 1], [1.1, -0.1, 0], [1, 1, 1], [1]):
            with self.assertRaises(ValueError):
                helper._choose('duplicate', scores)

    def test_bad_checks_before_load(self):
        helper = self._helper()
        for checks in ([], [{'id': 'a', 'kind': 'route', 'state': 'x'}],
                       [{'id': 'a', 'kind': [], 'state': 'x'}],
                       [{'id': 'a', 'kind': 'repeat', 'state': 'no comparison'}],
                       [{'id': 'a', 'kind': 'failure', 'state': 'x'}] * 33):
            with self.assertRaises(ValueError):
                helper._checks({'checks': checks})

    def test_repeated_ids_fail(self):
        helper = self._helper()
        with self.assertRaises(ValueError):
            helper._checks({'checks': [{'id': 'a', 'kind': 'failure', 'state': 'x'}] * 2})

    def test_missing_history_abstains(self):
        helper = self._helper()
        check = {'id': 'a', 'kind': 'repeat', 'state': {'previous': '', 'latest': 'working'}}
        self.assertEqual(helper._insufficient(check), 'missing_evidence')

    def test_empty_skips_model(self):
        helper = self._helper()
        payload = {'checks': [{'id': 'a', 'kind': 'failure', 'state': ''}]}
        with mock.patch.object(helper, '_MlxDriver', side_effect=AssertionError('must not load')):
            result = helper._run(Path('/missing'), payload)
        self.assertEqual(result['decisions'][0]['choice'], 'unclear')

    def test_no_progress_certificate(self):
        helper = self._helper()
        result = helper._choose('repeat', [0.001, 0.998, 0.001])
        self.assertEqual(result['choice'], 'unclear')
        self.assertEqual(result['reason'], 'no_repeat_signal')

    def test_batch_loads_once(self):
        helper = self._helper()
        payload = {'checks': [{'id': str(i), 'kind': 'failure', 'state': 'failure'} for i in range(3)]}
        with mock.patch.object(helper, '_MlxDriver') as driver:
            driver.return_value._score.return_value = {'choice': 'unclear'}
            result = helper._run(Path('/model'), payload)
        self.assertEqual(driver.call_count, 1)
        self.assertEqual(len(result['decisions']), 3)

    def test_oversize_input(self):
        _, evidence, env = self._fixture('raise RuntimeError("must not load")\n')
        evidence.write_text(json.dumps({'checks': [], 'padding': 'x' * 65536}))
        code, stdout, stderr = self.run_cli(['advise', '--file', str(evidence)], env=env)
        self.assertEqual(code, 2)
        self.assertIn('Invalid advice input', stderr)

    def test_bad_timeout(self):
        _, evidence, env = self._fixture('raise RuntimeError("must not load")\n')
        for value in ('0', '-1', 'nan', 'inf', '31'):
            code, stdout, stderr = self.run_cli(['advise', '--file', str(evidence), '--timeout', value], env=env)
            self.assertEqual(code, 2)
            self.assertIn('--timeout', stderr)
