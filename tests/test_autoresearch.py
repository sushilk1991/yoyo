import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_yoyo import ROOT


class AutoResearchTests(unittest.TestCase):
    def _module(self):
        # Selection and resource gates must work without loading MLX.
        spec = importlib.util.spec_from_file_location('advice_research', ROOT / 'extras/autoresearch/run.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_missing_rows_fail(self):
        module = self._module()
        cases = [{'id': 'a', 'kind': 'failure', 'expected': 'account'}]
        with self.assertRaises(ValueError):
            module._metrics(cases, [])

    def test_abstentions_not_accuracy(self):
        module = self._module()
        cases = [{'id': 'a', 'kind': 'failure', 'expected': 'account'}]
        rows = [{'id': 'a', 'candidate': 'environment', 'choice': 'unclear', 'seconds': 0.01}]
        metrics = module._metrics(cases, rows)
        self.assertEqual(metrics['correct'], 0)
        self.assertEqual(metrics['issued'], 0)
        self.assertEqual(metrics['correct_issued'], 0)

    def test_kind_regression_rejected(self):
        module = self._module()
        base = {'correct': 20, 'by_kind': {'repeat': 6, 'failure': 7, 'duplicate': 7}, 'wrong_issued': 0, 'correct_issued': 5}
        candidate = {**base, 'correct': 23, 'by_kind': {'repeat': 5, 'failure': 9, 'duplicate': 9}}
        self.assertFalse(module._improves(base, candidate))

    def test_false_advice_rejected(self):
        module = self._module()
        base = {'correct': 20, 'by_kind': {'failure': 20}, 'wrong_issued': 0, 'correct_issued': 5}
        candidate = {**base, 'correct': 21, 'by_kind': {'failure': 21}, 'wrong_issued': 1}
        self.assertFalse(module._improves(base, candidate))

    def test_abstain_all_cannot_win(self):
        module = self._module()
        base = {'correct': 20, 'by_kind': {'failure': 20}, 'wrong_issued': 1, 'correct_issued': 5}
        candidate = {**base, 'correct': 21, 'by_kind': {'failure': 21}, 'wrong_issued': 0, 'correct_issued': 0}
        self.assertFalse(module._improves(base, candidate))

    def test_measured_gain_wins(self):
        module = self._module()
        base = {'correct': 20, 'by_kind': {'failure': 20}, 'wrong_issued': 1, 'correct_issued': 5}
        candidate = {**base, 'correct': 21, 'by_kind': {'failure': 21}, 'wrong_issued': 0}
        self.assertTrue(module._improves(base, candidate))

    def test_holdout_is_separate(self):
        # Exact duplicates cannot cross the development/holdout boundary.
        files = [ROOT / 'extras/autoresearch' / f'{split}.json' for split in ('dev', 'holdout')]
        sets = [{json.dumps(c['state'], sort_keys=True) for c in json.loads(path.read_text())['cases']} for path in files]
        self.assertEqual(len(sets[0]), 30)
        self.assertEqual(len(sets[1]), 60)
        self.assertFalse(sets[0] & sets[1])

    def test_pressure_fails_closed(self):
        module = self._module()
        with mock.patch.object(module.subprocess, 'check_output', return_value='2\n'):
            self.assertFalse(module._pressure_allowed())
        with mock.patch.object(module.subprocess, 'check_output', side_effect=OSError('unavailable')):
            self.assertFalse(module._pressure_allowed())

    def test_warning_is_explicit(self):
        module = self._module()
        with mock.patch.object(module.subprocess, 'check_output', return_value='2\n'):
            self.assertTrue(module._pressure_allowed('warning'))
            self.assertFalse(module._pressure_allowed('normal'))
        for value in ('4\n', 'unknown\n'):
            with mock.patch.object(module.subprocess, 'check_output', return_value=value):
                self.assertFalse(module._pressure_allowed('warning'))

    def test_stop_reaps_child(self):
        # Resource pressure after spawn must leave no running inference child.
        module = self._module()
        spawned = []
        popen = module.subprocess.Popen

        def _spawn(command, **kwargs):
            proc = popen([module.sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            spawned.append(proc)
            return proc

        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(out=Path(tmp), model=Path('/unused'), pressure='normal')
            with mock.patch.object(module, '_pressure_allowed', side_effect=[True, False]):
                with mock.patch.object(module.subprocess, 'Popen', side_effect=_spawn):
                    with self.assertRaisesRegex(RuntimeError, 'pressure rose'):
                        module._trial(args, 'test', {}, Path('/unused'), module.time.monotonic() + 30)
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].returncode)
