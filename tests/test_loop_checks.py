import json
import shlex
import tempfile
from pathlib import Path

from test_yoyo import CliTestCase


class LoopCheckTests(CliTestCase):
    def _fixture(self, body):
        # Execute real child processes in an isolated workspace, so these
        # tests cover prompts, state files, shell checks and the run ledger.
        root = Path(self._state_guard.name) / "workspace"
        root.mkdir()
        worker = root / "worker.py"
        worker.write_text("import sys\nfrom pathlib import Path\nsys.stdin.read()\n" + body)
        env = {"YOYO_AGENT_STUB": "python3 " + shlex.quote(str(worker))}
        argv = ["loop", "stub", "--cwd", str(root), "--state", "state.md", "--json"]
        return root, env, argv

    def test_failed_done_is_bounded(self):
        root, env, argv = self._fixture("Path('state.md').write_text('STATUS: DONE\\n')\n")
        code, stdout, stderr = self.run_cli(
            argv + ["--max-iter", "8", "--max-fail", "2", "--verify", "echo 'STATUS: DONE'; exit 7", "finish"], env=env)

        self.assertEqual(code, 1, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["iterations"], 2)
        self.assertEqual(result["end_reason"], "max-fail")
        self.assertEqual(result["verification"]["exit_code"], 7)
        self.assertNotIn("STATUS: DONE", [line.strip() for line in (root / "state.md").read_text().splitlines()])

    def test_no_done_runs_no_check(self):
        root, env, argv = self._fixture("print('more work remains')\n")
        code, stdout, stderr = self.run_cli(argv + ["--max-iter", "1", "--verify", "touch checked", "finish"], env=env)

        self.assertEqual(code, 1, stderr)
        self.assertIsNone(json.loads(stdout)["verification"])
        self.assertFalse((root / "checked").exists())

    def test_queue_precedes_check(self):
        root, env, argv = self._fixture("Path('state.md').write_text('STATUS: DONE\\n')\n")
        (root / "queue.md").write_text("- [ ] unfinished\n")
        code, stdout, stderr = self.run_cli(
            argv + ["--max-iter", "1", "--queue", "queue.md", "--verify", "touch checked", "finish"], env=env)

        self.assertEqual(code, 1, stderr)
        self.assertEqual(json.loads(stdout)["queue_rejections"], 1)
        self.assertFalse((root / "checked").exists())

    def test_check_timeout_fails(self):
        _, env, argv = self._fixture("Path('state.md').write_text('STATUS: DONE\\n')\n")
        code, stdout, stderr = self.run_cli(
            argv + ["--max-iter", "1", "--timeout", "0.3", "--verify", "sleep 5", "finish"], env=env)

        self.assertEqual(code, 1, stderr)
        self.assertEqual(json.loads(stdout)["verification"]["exit_code"], 124)

    def test_check_keeps_failure_tail(self):
        root, env, argv = self._fixture("Path('state.md').write_text('STATUS: DONE\\n')\n")
        check = "python3 -c " + shlex.quote("print('x' * 10000); print('actionable failure'); raise SystemExit(1)")
        code, stdout, stderr = self.run_cli(argv + ["--max-iter", "1", "--verify", check, "finish"], env=env)

        self.assertEqual(code, 1, stderr)
        self.assertIn("actionable failure", json.loads(stdout)["verification"]["stdout"])
        self.assertIn("actionable failure", (root / "state.md").read_text())

    def test_queue_progress_resets(self):
        root, env, argv = self._fixture(
            "counter = Path('calls')\n"
            "calls = int(counter.read_text()) + 1 if counter.exists() else 1\n"
            "counter.write_text(str(calls))\n"
            "if calls == 2: Path('queue.md').write_text('- [x] first\\n- [ ] second\\n')\n")
        (root / "queue.md").write_text("- [ ] first\n- [ ] second\n")
        code, stdout, stderr = self.run_cli(
            argv + ["--max-iter", "8", "--max-stall", "2", "--queue", "queue.md", "finish"], env=env)

        self.assertEqual(code, 1, stderr)
        self.assertEqual(json.loads(stdout)["iterations"], 4)

    def test_dry_run_has_no_effects(self):
        root, env, argv = self._fixture("raise RuntimeError('must not execute')\n")
        code, stdout, stderr = self.run_cli(
            argv + ["--dry-run", "--max-stall", "2", "--verify", "touch checked", "finish"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertIn("Completion check", json.loads(stdout)["prompt"])
        self.assertFalse((root / "checked").exists())
        self.assertFalse((root / "state.md").exists())

    def test_background_keeps_check(self):
        _, env, argv = self._fixture("Path('state.md').write_text('STATUS: DONE\\n')\n")
        code, stdout, stderr = self.run_cli(
            argv + ["--background", "--max-iter", "1", "--verify", "exit 9", "finish"], env=env)
        self.assertEqual(code, 0, stderr)
        run_id = json.loads(stdout)["run_id"]
        code, stdout, stderr = self.run_cli(["wait", run_id, "--timeout", "5", "--json"], env=env)

        self.assertEqual(code, 1, stderr)
        self.assertEqual(json.loads(stdout)["verification"]["exit_code"], 9)

    def test_stall_stops_unchanged(self):
        # Successful process exits are not progress: repeated unchanged state
        # must stop only when the caller opts into that limit.
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "5", "--max-stall", "2", "--json", "make progress"],
                env={"YOYO_AGENT_STUB": "python3 -c 'import sys; sys.stdin.read()'"},
            )

        self.assertEqual(code, 1, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["iterations"], 2)
        self.assertEqual(result["end_reason"], "state-unchanged")

    def test_failed_check_repairs(self):
        # A zero-exit worker can falsely claim DONE. The failed command must
        # reach the next fresh context, which repairs the artifact and retries.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = root / "worker.py"
            state = root / "state.md"
            worker.write_text(
                "import sys\nfrom pathlib import Path\n"
                "sys.stdin.read()\n"
                "state = Path('state.md')\n"
                "previous = state.read_text()\n"
                "if 'artifact missing' in previous:\n"
                "    Path('artifact').touch()\n"
                "state.write_text('STATUS: DONE\\n')\n"
            )
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--state", str(state), "--max-iter", "3",
                 "--verify", "test -f artifact || { echo 'artifact missing'; exit 1; }", "--json", "create artifact"],
                env={"YOYO_AGENT_STUB": "python3 " + shlex.quote(str(worker))},
            )

        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["iterations"], 2)
        self.assertEqual(result["end_reason"], "done")
        self.assertEqual(result["verification_rejections"], 1)
        self.assertEqual(result["verification"]["exit_code"], 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
