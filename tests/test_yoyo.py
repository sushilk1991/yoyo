import argparse
import importlib.util
import importlib.machinery
import errno
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
YOYO_PATH = ROOT / "bin" / "yoyo"


loader = importlib.machinery.SourceFileLoader("yoyo_cli", str(YOYO_PATH))
spec = importlib.util.spec_from_loader("yoyo_cli", loader)
yoyo = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = yoyo
spec.loader.exec_module(yoyo)


class CliTestCase(unittest.TestCase):
    def setUp(self):
        # Isolate the runs ledger: agent-calling tests journal every call,
        # and must never write into the developer's real state dir.
        self._state_guard = tempfile.TemporaryDirectory(prefix="yoyo-test-state-")
        self.addCleanup(self._state_guard.cleanup)

    def run_cli(self, argv, *, stdin="", env=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        merged_env = os.environ.copy()
        merged_env.setdefault("YOYO_STATE_DIR", self._state_guard.name)
        # Deterministic default-skill behavior regardless of the host shell:
        # tests opt into default injection explicitly. A value of None removes
        # the variable entirely (exercising the built-in default).
        merged_env["YOYO_DEFAULT_SKILLS"] = ""
        if env:
            merged_env.update(env)
        merged_env = {key: value for key, value in merged_env.items() if value is not None}
        with mock.patch.dict(os.environ, merged_env, clear=True):
            with mock.patch("sys.stdin", io.StringIO(stdin)):
                with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                    code = yoyo.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()


class YoyoTests(CliTestCase):
    def test_version_outputs_current_release(self):
        code, stdout, stderr = self.run_cli(["--version"])

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout.strip(), "yoyo 0.28.0")

    def test_custom_agent_receives_rendered_prompt_on_stdin(self):
        env = {"YOYO_AGENT_ECHO": "python3 -c \"import sys; print(sys.stdin.read())\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--role", "opinion", "Check this."],
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("independent second-opinion agent", stdout)
        self.assertIn("Task:\nCheck this.", stdout)

    def test_inbuilt_fable_mode_injected_by_default(self):
        # With YOYO_DEFAULT_SKILLS unset entirely, the bundled yoyo-fable-mode
        # skill resolves from the repo's own skills dir and rides every call.
        env = {
            "YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\"",
            "YOYO_DEFAULT_SKILLS": None,
        }
        code, stdout, stderr = self.run_cli(["ask", "echo", "hello"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertIn('<skill name="yoyo-fable-mode">', stdout)
        self.assertIn("one-shot", stdout)
        self.assertIn("failure trajectory", stdout)
        self.assertIn("Task:\nhello", stdout)

    def test_default_skills_empty_string_disables_injection(self):
        env = {
            "YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\"",
            "YOYO_DEFAULT_SKILLS": "",
        }
        code, stdout, stderr = self.run_cli(["ask", "echo", "hello"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertNotIn("<skill", stdout)

    def test_personal_fable_mode_skill_cannot_shadow_the_bundled_one(self):
        # Regression guard. The default used to be the bare name "fable-mode",
        # which user skill roots win — so every delegate got the caller's own
        # interactive-session harness (it told them they were "running as Opus")
        # instead of the delegate one yoyo ships. The yoyo- prefix closes that.
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "fable-mode"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# My Own Harness\ncustom-override-marker\n", encoding="utf-8")
            env = {
                "YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\"",
                "YOYO_DEFAULT_SKILLS": None,
                "YOYO_SKILL_PATH": tmp,
            }
            code, stdout, stderr = self.run_cli(["ask", "echo", "hello"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertNotIn("custom-override-marker", stdout)
        self.assertIn('<skill name="yoyo-fable-mode">', stdout)

    def test_named_skill_still_resolves_from_a_user_skill_root(self):
        # The shadowing fix must not break ordinary --skill overrides.
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "house-rules"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# House Rules\ncustom-override-marker\n", encoding="utf-8")
            env = {
                "YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\"",
                "YOYO_DEFAULT_SKILLS": "",
                "YOYO_SKILL_PATH": tmp,
            }
            code, stdout, stderr = self.run_cli(["ask", "echo", "--skill", "house-rules", "hello"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertIn("custom-override-marker", stdout)

    def test_raw_mode_skips_inbuilt_default_skill(self):
        env = {
            "YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\"",
            "YOYO_DEFAULT_SKILLS": None,
        }
        code, stdout, stderr = self.run_cli(["ask", "echo", "--raw", "/cmd verbatim"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertNotIn("<skill", stdout)
        self.assertTrue(stdout.startswith("/cmd verbatim"), stdout)

    def test_json_output_wraps_agent_result(self):
        env = {"YOYO_AGENT_ECHO": "python3 -c \"import sys; print('ok:' + sys.stdin.read().splitlines()[-1])\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--json", "hello"],
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["agent"], "echo")
        self.assertEqual(payload["exit_code"], 0)
        self.assertIn("ok:", payload["stdout"])

    def test_json_output_includes_trace_id_and_truncation_flags(self):
        env = {"YOYO_AGENT_ECHO": "python3 -c \"print('ok')\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--json", "--trace-id", "trace-123", "hello"],
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["trace_id"], "trace-123")
        self.assertFalse(payload["stdout_truncated"])
        self.assertFalse(payload["stderr_truncated"])

    def test_background_ask_wait_and_runs_show_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_AGENT_ECHO": "python3 -c \"import sys; print('agent-output:' + sys.stdin.read())\"",
            }
            code, stdout, stderr = self.run_cli(["ask", "echo", "--background", "hello"], env=env)

            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()
            self.assertRegex(run_id, r"^\d{8}T\d{6}-[0-9a-f]{8}$")
            self.assertIn("run dir:", stderr)

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "3", "--poll", "0.01"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("agent-output:", stdout)
            self.assertIn("hello", stdout)

            code, stdout, stderr = self.run_cli(["runs", "show", run_id, "--json"], env=env)
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["run_id"], run_id)
            self.assertEqual(payload["status"], "done")
            self.assertEqual(payload["agent"], "echo")
            self.assertEqual(payload["exit_code"], 0)
            self.assertIn("hello", payload["stdout"])

    def test_foreground_call_journals_the_prompt(self):
        # The journal recorded the delegate command but not what was asked, so
        # a killed foreground call left no way to see the actual prompt.
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_AGENT_ECHO": "python3 -c \"print('ok')\"",
            }
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--json", "review the substring delivery proof"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)

            runs = sorted((Path(tmp) / "runs").iterdir())
            self.assertEqual(len(runs), 1)
            prompt = (runs[0] / "prompt.txt").read_text(encoding="utf-8")
            self.assertIn("review the substring delivery proof", prompt)
            meta = json.loads((runs[0] / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["prompt_bytes"], len(prompt.encode("utf-8")))

    def test_background_run_writes_meta_and_result_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = "héllo"
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_AGENT_ECHO": "python3 -c \"print('ok')\"",
            }
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--background", "--trace-id", "trace-bg", prompt],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()
            code, _, stderr = self.run_cli(["wait", run_id, "--timeout", "3", "--poll", "0.01"], env=env)
            self.assertEqual(code, 0, stderr)

            run_dir = Path(tmp) / "runs" / run_id
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["agent"], "echo")
            self.assertIsInstance(meta["pid"], int)
            self.assertEqual(meta["trace_id"], "trace-bg")
            self.assertEqual(meta["prompt_bytes"], len(prompt.encode("utf-8")))
            self.assertEqual(result["agent"], "echo")
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("duration_s", result)

    def test_background_prompt_journal_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_NO_CALL_JOURNAL": "1",
                "YOYO_AGENT_ECHO": "python3 -c \"print('ok')\"",
            }
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--background", "private background prompt"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()
            code, _, stderr = self.run_cli(["wait", run_id, "--timeout", "3", "--poll", "0.01"], env=env)
            self.assertEqual(code, 0, stderr)

            run_dir = Path(tmp) / "runs" / run_id
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertFalse((run_dir / "prompt.txt").exists())
            self.assertNotIn("prompt_bytes", meta)

    def test_journalled_prompt_is_capped_but_reports_its_true_size(self):
        # A review prompt carries the whole diff and runs tens of KB, while the
        # captures beside it average a few. Keep a bounded head on disk; the
        # real size stays in meta.
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdin.read(); print('ok')\"",
            }
            code, _, stderr = self.run_cli(["ask", "echo", "--json", "x" * 200_000], env=env)

            self.assertEqual(code, 0, stderr)
            run_dir = next((Path(tmp) / "runs").iterdir())
            written = (run_dir / "prompt.txt").read_bytes()
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertLess(len(written), 70_000)
            self.assertIn(b"prompt truncated by yoyo", written)
            self.assertGreater(meta["prompt_bytes"], 200_000)

    def test_background_autopsy_shows_the_prompt_not_the_trace_id(self):
        # The child argv ends with --trace-id <id>, so reading the question off
        # the tail of argv reported the trace id as what was asked.
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_AGENT_ECHO": "python3 -c \"print('ok')\"",
            }
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--background", "--trace-id", "trace-bg", "why is the cache cold"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()
            code, _, stderr = self.run_cli(["wait", run_id, "--timeout", "3", "--poll", "0.01"], env=env)
            self.assertEqual(code, 0, stderr)

            code, stdout, stderr = self.run_cli(["runs", "autopsy", run_id], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("asked:   why is the cache cold", stdout)
            self.assertNotIn("asked:   trace-bg", stdout)

    def test_background_ask_passes_piped_stdin_to_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_AGENT_ECHO": "python3 -c \"import sys; print(sys.stdin.read())\"",
            }
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--background", "hello"],
                stdin="PIPE-CONTEXT",
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "3", "--poll", "0.01"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("<stdin>\nPIPE-CONTEXT\n</stdin>", stdout)

    def test_runs_list_json_reports_done_runs_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_AGENT_ECHO": "python3 -c \"print('ok')\"",
            }
            run_ids = []
            for prompt in ("one", "two"):
                code, stdout, stderr = self.run_cli(["ask", "echo", "--background", prompt], env=env)
                self.assertEqual(code, 0, stderr)
                run_id = stdout.strip()
                run_ids.append(run_id)
                code, _, stderr = self.run_cli(["wait", run_id, "--timeout", "3", "--poll", "0.01"], env=env)
                self.assertEqual(code, 0, stderr)

            code, stdout, stderr = self.run_cli(["runs", "list", "--json"], env=env)
            self.assertEqual(code, 0, stderr)
            rows = json.loads(stdout)
            self.assertTrue(any(row["run_id"] == run_ids[0] and row["status"] == "done" for row in rows))

            code, stdout, stderr = self.run_cli(["runs", "list", "--json", "--limit", "1"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(len(json.loads(stdout)), 1)

    def test_runs_show_rejects_unknown_and_path_separator_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp}
            code, stdout, stderr = self.run_cli(["runs", "show", "missing-run"], env=env)
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("Unknown run_id", stderr)

            code, stdout, stderr = self.run_cli(["runs", "show", "../escape"], env=env)
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("Invalid run_id", stderr)

    def test_dead_run_show_and_wait_exit_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp}
            run_id = "20000101T000000-deadbeef"
            self._write_run_meta(tmp, run_id, pid=99999999)

            code, stdout, stderr = self.run_cli(["runs", "show", run_id], env=env)
            self.assertEqual(code, 4)
            self.assertEqual(stdout, "")
            self.assertIn("status: dead", stderr)

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "1", "--poll", "0.01"],
                env=env,
            )
            self.assertEqual(code, 4)
            self.assertEqual(stdout, "")
            self.assertIn("status: dead", stderr)

    def test_wait_timeout_for_running_run_exits_124(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp}
            run_id = "20000101T000000-00000001"
            run_dir = self._write_run_meta(tmp, run_id, pid=os.getpid())
            (run_dir / "stdout.txt").write_text("partial", encoding="utf-8")

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "0.05", "--poll", "0.01"],
                env=env,
            )
            self.assertEqual(code, 124)
            self.assertEqual(stdout, "")
            self.assertIn("poll expired", stderr)
            self.assertIn(run_id, stderr)
            self.assertIn("7 bytes stdout", stderr)

    def test_wait_timeout_reports_proof_of_life(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp}
            run_id = "20000101T000000-00000002"
            run_dir = self._write_run_meta(tmp, run_id, pid=os.getpid())
            (run_dir / "log.txt").write_text(
                "yoyo: still running, 20s elapsed, 1024 bytes captured\n"
                "yoyo: still running, 60s elapsed, 255886 bytes captured\n",
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "0.05", "--poll", "0.01"],
                env=env,
            )
            self.assertEqual(code, 124)
            self.assertEqual(stdout, "")
            self.assertIn("poll expired", stderr)
            self.assertNotIn("timed out waiting", stderr)
            self.assertIn("still running, 60s elapsed, 255886 bytes captured", stderr)
            self.assertNotIn("20s elapsed", stderr)
            self.assertRegex(stderr, r"\d+(\.\d+)?s elapsed")

    def test_wait_timeout_json_adds_progress_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp}
            run_id = "20000101T000000-00000003"
            run_dir = self._write_run_meta(tmp, run_id, pid=os.getpid())
            (run_dir / "log.txt").write_text(
                "yoyo: still running, 60s elapsed, 255886 bytes captured\n", encoding="utf-8"
            )

            code, stdout, _ = self.run_cli(
                ["wait", run_id, "--timeout", "0.05", "--poll", "0.01", "--json"],
                env=env,
            )
            self.assertEqual(code, 124)
            payload = json.loads(stdout)
            self.assertEqual(payload["run_id"], run_id)
            self.assertEqual(payload["status"], "timeout")
            self.assertEqual(payload["run_status"], "running")
            self.assertGreater(payload["elapsed_s"], 0)
            self.assertIn("255886 bytes captured", payload["last_progress"])
            self.assertEqual(payload["stdout_bytes"], 0)
            self.assertGreater(payload["stderr_bytes"], 0)

    def test_runs_prune_dry_run_lists_old_run_and_prune_deletes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp}
            run_id = "20000101T000000-feedface"
            run_dir = self._write_run_meta(tmp, run_id, pid=99999999, started_at="2000-01-01T00:00:00Z")
            os.utime(run_dir, (946684800, 946684800))

            code, stdout, stderr = self.run_cli(["runs", "prune", "--dry-run", "--days", "7"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn(run_id, stdout)
            self.assertTrue(run_dir.exists())

            code, stdout, stderr = self.run_cli(["runs", "prune", "--days", "7"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("1 run dirs deleted", stdout)
            self.assertFalse(run_dir.exists())

    def test_background_dry_run_stays_foreground_and_creates_no_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp}
            code, stdout, stderr = self.run_cli(
                ["ask", "codex", "--background", "--dry-run", "hello"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("codex --ask-for-approval never exec", stdout)
            self.assertIn("Task:\nhello", stdout)
            self.assertFalse((Path(tmp) / "runs").exists())

    def test_claude_session_first_call_records_uuid_and_followup_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp, "YOYO_CONFIG": str(Path(tmp) / "missing.json")}
            code, stdout, stderr = self.run_cli(
                ["ask", "claude", "--session", "foo", "--dry-run", "--json", "hello"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            command = payload["command"]
            self.assertIn("--session-id", command)
            self.assertNotIn("--no-session-persistence", command)
            backend_id = command[command.index("--session-id") + 1]
            self.assertRegex(backend_id, r"^[0-9a-f-]{36}$")

            sessions = json.loads((Path(tmp) / "sessions.json").read_text(encoding="utf-8"))["sessions"]
            self.assertEqual(sessions["claude:foo"]["backend_id"], backend_id)

            code, stdout, stderr = self.run_cli(
                ["ask", "claude", "--session", "foo", "--dry-run", "--json", "again"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            command = json.loads(stdout)["command"]
            self.assertIn("--resume", command)
            self.assertEqual(command[command.index("--resume") + 1], backend_id)
            self.assertNotIn("--no-session-persistence", command)

    def test_pi_session_uses_same_session_id_for_first_and_followup(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp, "YOYO_CONFIG": str(Path(tmp) / "missing.json")}
            code, stdout, stderr = self.run_cli(
                ["ask", "pi", "--session", "foo", "--dry-run", "--json", "hello"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            first_command = json.loads(stdout)["command"]
            self.assertIn("--session-id", first_command)
            self.assertNotIn("--no-session", first_command)
            backend_id = first_command[first_command.index("--session-id") + 1]

            code, stdout, stderr = self.run_cli(
                ["ask", "pi", "--session", "foo", "--dry-run", "--json", "again"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            second_command = json.loads(stdout)["command"]
            self.assertIn("--session-id", second_command)
            self.assertEqual(second_command[second_command.index("--session-id") + 1], backend_id)
            self.assertNotIn("--no-session", second_command)

    def test_codex_session_dry_run_omits_ephemeral_and_followup_inserts_resume(self):
        fixed_id = "11111111-1111-4111-8111-111111111111"
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp, "YOYO_CONFIG": str(Path(tmp) / "missing.json")}
            code, stdout, stderr = self.run_cli(
                ["ask", "codex", "--session", "foo", "--dry-run", "--json", "hello"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            first_command = json.loads(stdout)["command"]
            self.assertNotIn("--ephemeral", first_command)

            self._write_session_record(tmp, "codex", "foo", fixed_id)
            code, stdout, stderr = self.run_cli(
                ["ask", "codex", "--session", "foo", "--dry-run", "--json", "again"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            command = json.loads(stdout)["command"]
            self.assertNotIn("--ephemeral", command)
            self.assertEqual(command[command.index("exec") + 1], "resume")
            self.assertEqual(command[command.index("resume") + 1], fixed_id)
            self.assertNotIn("-C", command)
            self.assertNotIn("--color", command)
            self.assertNotIn("--sandbox", command)
            self.assertIn('sandbox_mode="danger-full-access"', command)
            self.assertIn("--skip-git-repo-check", command)

    def test_codex_first_session_call_parses_stderr_and_records_session(self):
        fixed_id = "22222222-2222-4222-8222-222222222222"
        script = f"import sys; print('session id: {fixed_id}', file=sys.stderr); print('OK')"
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps({"agents": {"codex-kind": {"kind": "codex", "command": ["python3", "-c", script]}}}),
                encoding="utf-8",
            )
            env = {"YOYO_STATE_DIR": tmp, "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["ask", "codex-kind", "--session", "foo", "--json", "hello"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["stdout"], "OK\n")
            self.assertEqual(payload["session"], {"name": "foo", "backend_id": fixed_id})
            sessions = json.loads((Path(tmp) / "sessions.json").read_text(encoding="utf-8"))["sessions"]
            self.assertEqual(sessions["codex-kind:foo"]["backend_id"], fixed_id)

    def test_custom_agent_session_fails_loudly(self):
        env = {"YOYO_AGENT_ECHO": "cat"}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--session", "foo", "--dry-run", "hello"],
            env=env,
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("agent echo does not support --session", stderr)

    def test_invalid_session_name_fails_loudly(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "claude", "--session", "foo/bar", "--dry-run", "hello"],
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Invalid session name", stderr)

    def test_sessions_list_json_and_rm(self):
        fixed_id = "33333333-3333-4333-8333-333333333333"
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session_record(tmp, "claude", "foo", fixed_id)
            env = {"YOYO_STATE_DIR": tmp}

            code, stdout, stderr = self.run_cli(["sessions", "list", "--json"], env=env)
            self.assertEqual(code, 0, stderr)
            rows = json.loads(stdout)
            self.assertEqual(rows[0]["key"], "claude:foo")
            self.assertEqual(rows[0]["backend_id"], fixed_id)

            code, stdout, stderr = self.run_cli(["sessions", "rm", "claude:foo"], env=env)
            self.assertEqual(code, 0, stderr)
            sessions = json.loads((Path(tmp) / "sessions.json").read_text(encoding="utf-8"))["sessions"]
            self.assertEqual(sessions, {})

            code, stdout, stderr = self.run_cli(["sessions", "rm", "claude:missing"], env=env)
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("Unknown session", stderr)

    def test_output_is_truncated_at_configured_limit(self):
        env = {"YOYO_AGENT_BIG": "python3 -c \"print('abcdef')\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "big", "--json", "--max-output-bytes", "3", "hello"],
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["stdout_truncated"])
        self.assertIn("abc", payload["stdout"])
        self.assertIn("truncated after 3 bytes", payload["stdout"])

    def test_invalid_output_limit_fails_loudly(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "codex", "--max-output-bytes", "0", "hello"],
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--max-output-bytes must be at least 1", stderr)

    def test_invalid_input_limit_fails_loudly(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "codex", "--max-input-bytes", "0", "hello"],
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--max-input-bytes must be at least 1", stderr)

    def test_invalid_timeout_fails_loudly(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "codex", "--timeout", "0", "hello"],
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--timeout must be greater than 0", stderr)


    def test_stdin_is_truncated_at_configured_limit(self):
        env = {"YOYO_AGENT_ECHO": "python3 -c \"import sys; print(sys.stdin.read())\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--max-input-bytes", "3", "hello"],
            stdin="abcdef",
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("<stdin>\nabc", stdout)
        self.assertIn("stdin truncated after 3 bytes", stdout)

    def test_full_access_with_stdin_warns(self):
        env = {"YOYO_AGENT_ECHO": "cat"}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "hello"],
            stdin="context",
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("warning: full-access delegation includes stdin/--file context", stderr)


    def test_dry_run_includes_context_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("important context", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["ask", "codex", "--dry-run", "--cwd", tmp, "--file", "note.txt", "Use the file."],
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("codex --ask-for-approval never exec", stdout)
        self.assertIn("<file path=\"note.txt\">", stdout)
        self.assertIn("important context", stdout)

    def test_ask_defaults_to_full_access_for_codex(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "codex", "--dry-run", "--cwd", str(ROOT), "Do it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("codex --ask-for-approval never exec", stdout)
        self.assertIn("--sandbox danger-full-access", stdout)
        self.assertIn("--ask-for-approval never", stdout)
        self.assertIn("mode=full-access delegation", stdout)

    def test_ask_defaults_to_full_access_for_claude(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "claude", "--dry-run", "Do it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("--permission-mode bypassPermissions", stdout)
        self.assertIn("mode=full-access delegation", stdout)

    def test_ask_defaults_to_full_access_for_pi(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "pi", "--dry-run", "Do it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("--tools read,grep,find,ls,bash,edit,write", stdout)
        self.assertIn("mode=full-access delegation", stdout)

    def test_ask_defaults_to_full_access_for_cursor(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "cursor", "--dry-run", "Do it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("cursor-agent -p --output-format stream-json --trust --force", stdout)
        self.assertIn("mode=full-access delegation", stdout)

    def test_ask_read_only_constrains_cursor(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "cursor", "--dry-run", "--read-only", "Review it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("cursor-agent -p --output-format stream-json --trust --mode plan", stdout)
        self.assertNotIn("--force", stdout)
        self.assertIn("mode=read-only delegation", stdout)

    def test_ask_defaults_to_full_access_for_agy(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "agy", "--dry-run", "Do it."],
        )

        self.assertEqual(code, 0, stderr)
        # agy carries the prompt as the -p value and runs full-access by default.
        self.assertIn("agy -p", stdout)
        self.assertIn("--add-dir", stdout)
        self.assertIn("--print-timeout", stdout)
        self.assertIn("--dangerously-skip-permissions", stdout)
        self.assertNotIn("--mode plan", stdout)
        self.assertNotIn("--sandbox", stdout)
        self.assertIn("mode=full-access delegation", stdout)

    def test_ask_read_only_constrains_agy(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "agy", "--dry-run", "--read-only", "Review it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("agy -p", stdout)
        self.assertIn("--add-dir", stdout)
        self.assertIn("--mode plan", stdout)
        self.assertIn("--sandbox", stdout)
        self.assertIn("--print-timeout", stdout)
        # Measured on agy 1.1.27: with --dangerously-skip-permissions, plan
        # mode still let write_to_file land a file. Headless auto-denial is
        # the enforcement, and it only exists while the skip flag is absent.
        self.assertNotIn("--dangerously-skip-permissions", stdout)
        self.assertIn("mode=read-only delegation", stdout)

    def test_format_go_duration_matches_parse_duration(self):
        self.assertEqual(yoyo.format_go_duration(14400.0), "14400s")
        self.assertEqual(yoyo.format_go_duration(1800), "1800s")
        self.assertEqual(yoyo.format_go_duration(90.5), "90.5s")

    def test_ask_agy_forwards_timeout_as_print_timeout(self):
        # agy's print mode defaults --print-timeout to 5m. A caller who omits
        # --timeout still gets yoyo's four-hour deadman, which must reach agy
        # or a long review dies on agy's clock. The forwarded value sits a
        # margin above yoyo's own clock so that yoyo's deadman fires first
        # and the ledger records a timeout (exit 124), not agy's own expiry
        # (exit 1, "timeout waiting for response", empty answer). Fractional
        # seconds must survive (Go ParseDuration).
        margin = yoyo.AGY_PRINT_TIMEOUT_MARGIN_SECONDS
        self.assertGreater(margin, 0)

        code, stdout, stderr = self.run_cli(
            ["ask", "agy", "--dry-run", "--timeout", "1800", "Do it."],
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"--print-timeout {yoyo.format_go_duration(1800 + margin)}", stdout)

        code, stdout, stderr = self.run_cli(
            ["ask", "agy", "--dry-run", "--timeout", "90.5", "Do it."],
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"--print-timeout {yoyo.format_go_duration(90.5 + margin)}", stdout)

        code, stdout, stderr = self.run_cli(
            ["ask", "agy", "--dry-run", "Do it."],
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn(
            f"--print-timeout {yoyo.format_go_duration(yoyo.default_timeout() + margin)}",
            stdout,
        )

    def _fake_agy_config(self, tmp, body, name="fakeagy"):
        """An agy-flavored agent backed by a shell script.

        The agy branch of command_for_agent keeps only command[0] and rebuilds
        the rest of argv itself, so the fake has to be a single executable.
        """
        script = Path(tmp) / f"{name}.sh"
        script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        script.chmod(0o755)
        config = Path(tmp) / "agents.json"
        config.write_text(json.dumps({"agents": {name: {"command": [str(script)], "kind": "agy"}}}), encoding="utf-8")
        return config

    def test_ask_agy_exit_zero_with_no_answer_is_a_failure(self):
        # Measured on agy 1.1.27: a headless auto-denial ends the run with
        # exit 0, an empty stdout, and a diagnosis on stderr. An empty answer
        # is not a success; yoyo must say so instead of printing nothing.
        diagnosis = (
            'jetski: no output produced — a tool required the "command" permission that '
            "headless mode cannot prompt for, so it was auto-denied."
        )
        with tempfile.TemporaryDirectory() as tmp:
            config = self._fake_agy_config(tmp, f"printf '%s\\n' {shlex.quote(diagnosis)} >&2\nexit 0\n")
            code, stdout, stderr = self.run_cli(
                ["ask", "fakeagy", "--read-only", "--cwd", tmp, "--json", "Review it."],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 1, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["exit_code"], 1)
        self.assertEqual(payload["stdout"], "")
        self.assertIn("exited 0 without producing an answer", payload["stderr"])
        self.assertIn("auto-denied", payload["stderr"])
        self.assertIn("allowlist", payload["stderr"])

    def test_ask_agy_real_answer_with_exit_zero_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            # $2 is the prompt: the fake receives `-p <prompt> --add-dir ...`.
            config = self._fake_agy_config(tmp, 'printf \'\\nanswer to: %s\\n\' "$2"\n')
            code, stdout, stderr = self.run_cli(
                ["ask", "fakeagy", "--raw", "--cwd", tmp, "--json", "Say hi."],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["exit_code"], 0)
        self.assertIn("answer to: Say hi.", payload["stdout"])
        self.assertNotIn("without producing an answer", payload["stderr"])

    def test_ask_agy_prompt_over_argv_limit_fails_loudly(self):
        # agy is the one agent that carries the prompt in argv, so it is the
        # one that can hit the kernel's argument-size limit (1 MiB total on
        # macOS, 128 KiB per argument on Linux). The failure must name the
        # cause, not leak "Argument list too long".
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp) / "big.txt"
            big.write_text("x" * 1_200_000, encoding="utf-8")
            config = self._fake_agy_config(tmp, "echo OK\n")
            # The input cap must sit above the file, or the prompt is clipped
            # before it can exceed ARG_MAX and the fake simply answers.
            code, stdout, stderr = self.run_cli(
                [
                    "ask", "fakeagy", "--raw", "--cwd", tmp, "--max-input-bytes", "5000000",
                    "--file", str(big), "Review it.",
                ],
                env={"YOYO_CONFIG": str(config), "YOYO_MAX_INPUT_BYTES": None},
            )

        self.assertEqual(code, 2, stderr)
        self.assertIn("command-line argument", stderr)
        self.assertIn("argv", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_argv_limit_on_a_stdin_agent_is_not_blamed_on_the_prompt(self):
        # Every other flavor reads the prompt from stdin, so an E2BIG there
        # came from the command line itself (--agent-arg, a config), and the
        # message must not tell the caller to shrink the prompt.
        def raise_e2big(*args, **kwargs):
            raise OSError(errno.E2BIG, "Argument list too long")

        with mock.patch.object(yoyo, "run_to_files", raise_e2big):
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--raw", "Do it."],
                env={"YOYO_AGENT_ECHO": "cat"},
            )

        self.assertEqual(code, 2, stderr)
        self.assertIn("could not start echo", stderr)
        self.assertIn("--agent-arg", stderr)
        self.assertNotIn("command-line argument", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_ask_defaults_to_full_access_for_grok(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "grok", "--dry-run", "Do it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("grok --prompt-file /dev/stdin --output-format plain --permission-mode bypassPermissions", stdout)
        self.assertIn("mode=full-access delegation", stdout)

    def test_ask_read_only_constrains_grok(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "grok", "--dry-run", "--read-only", "Review it."],
        )

        self.assertEqual(code, 0, stderr)
        # Measured on grok 1.0.13: plan mode blocks writes but cancels the
        # turn on the first denied call (truncated answer, exit 0), and
        # --sandbox read-only let the Write tool land a file. The headless
        # tool allowlist is the mechanism that held.
        self.assertIn(
            "grok --prompt-file /dev/stdin --output-format plain "
            f"--tools {yoyo.GROK_READ_ONLY_TOOLS} "
            f"--disallowed-tools {yoyo.GROK_READ_ONLY_DISALLOWED_TOOLS} "
            "--permission-mode bypassPermissions",
            stdout,
        )
        self.assertNotIn("--permission-mode plan", stdout)
        self.assertNotIn("--sandbox", stdout)
        self.assertIn("mode=read-only delegation", stdout)

    def test_ask_full_access_grok_keeps_its_tools(self):
        code, stdout, stderr = self.run_cli(["ask", "grok", "--dry-run", "Do it."])

        self.assertEqual(code, 0, stderr)
        self.assertNotIn("--tools", stdout)
        self.assertNotIn("--disallowed-tools", stdout)

    def test_grok_read_only_allowlist_has_no_write_shell_or_mcp_tool(self):
        tools = set(yoyo.GROK_READ_ONLY_TOOLS.split(","))
        self.assertEqual(tools, {"read_file", "grep", "list_dir"})
        for forbidden in ("search_replace", "run_terminal_cmd", "web_fetch", "web_search", "task"):
            self.assertNotIn(forbidden, tools)
        # `--tools` leaves grok's MCP meta-tools in place (its docs: "MCP
        # meta-tools remain available unless denied"); a configured MCP server
        # can write, so they must be removed by name, with subagent spawning.
        disallowed = set(yoyo.GROK_READ_ONLY_DISALLOWED_TOOLS.split(","))
        self.assertEqual(disallowed, {"search_tool", "use_tool", "Agent"})

    def test_on_demand_agents_pass_model_flag(self):
        for agent, model in (("cursor", "composer-2.5"), ("agy", "gemini-3.1-pro"), ("grok", "grok-4")):
            code, stdout, stderr = self.run_cli(
                ["ask", agent, "--dry-run", "--model", model, "Do it."],
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn(f"--model {model}", stdout)

    def test_on_demand_agents_reject_session(self):
        for agent in ("cursor", "agy", "grok"):
            code, stdout, stderr = self.run_cli(
                ["ask", agent, "--dry-run", "--session", "s1", "Do it."],
            )

            self.assertEqual(code, 2)
            self.assertIn("does not support --session", stderr)


    def test_ask_read_only_constrains_codex(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "codex", "--dry-run", "--read-only", "--cwd", str(ROOT), "Review it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("--sandbox read-only", stdout)
        self.assertNotIn("--ask-for-approval never", stdout)
        self.assertIn("mode=read-only delegation", stdout)

    def test_custom_agent_read_only_without_configured_args_fails_loudly(self):
        env = {"YOYO_AGENT_ECHO": "cat"}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--dry-run", "--read-only", "Review it."],
            env=env,
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("read_only_args", stderr)

    def test_configured_custom_agent_can_define_read_only_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps({"agents": {"echo": {"command": ["cat"], "read_only_args": ["--safe"]}}}),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--dry-run", "--read-only", "Review it."],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("cat --safe", stdout)
        self.assertIn("mode=read-only delegation", stdout)

    def test_configured_builtin_kind_can_override_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps({"agents": {"codex": {"kind": "codex", "command": ["/custom/codex", "exec"]}}}),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["ask", "codex", "--dry-run", "Do it."],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("/custom/codex --ask-for-approval never exec", stdout)

    def test_prompt_after_options_is_collected_for_ask_and_dash_extras_still_error(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "claude", "--dry-run", "Review", "this", "diff"],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Task:\nReview this diff", stdout)

        code, stdout, stderr = self.run_cli(
            ["ask", "claude", "--dry-run", "--unknown-flag", "hello"],
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("unrecognized arguments", stderr)








    def test_missing_context_file_fails_loudly(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "codex", "--file", "does-not-exist.txt", "Use the file."],
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Context file not found", stderr)

    def test_invalid_cwd_fails_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "missing")
            code, stdout, stderr = self.run_cli(["ask", "codex", "--cwd", missing, "hello"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Working directory does not exist", stderr)

    def test_timeout_returns_124_json(self):
        env = {"YOYO_AGENT_SLEEP": "python3 -c \"import time; time.sleep(5)\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "sleep", "--json", "--timeout", "0.1", "hello"],
            env=env,
        )

        self.assertEqual(code, 124)
        payload = json.loads(stdout)
        self.assertEqual(payload["exit_code"], 124)
        self.assertIn("Timed out after 0.1s", payload["stderr"])
        self.assertNotIn("stderr_plain", payload)

    def test_custom_agent_full_access_args_are_appended(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps({"agents": {"echo": {"command": ["cat"], "full_access_args": ["--write"]}}}),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--dry-run", "Do it."],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("cat --write", stdout)

    def test_codex_last_message_replaces_stdout_and_drops_transcript_stderr_in_json(self):
        # The emitted envelope carries one stderr field — the informative one.
        # On codex success the raw capture is the reasoning transcript, which
        # must never ride into the calling agent's context.
        def fake_run(cmd, prompt, cwd, stdout_path, stderr_path, timeout, **kwargs):
            output_index = cmd.index("--output-last-message") + 1
            Path(cmd[output_index]).write_text("final answer", encoding="utf-8")
            stdout_path.write_text("codex transcript", encoding="utf-8")
            stderr_path.write_text("codex stderr", encoding="utf-8")
            return yoyo.subprocess.CompletedProcess(cmd, 0)

        with mock.patch.object(yoyo, "run_to_files", side_effect=fake_run):
            code, stdout, stderr = self.run_cli(
                ["ask", "codex", "--json", "--trace-id", "codex-final", "hello"],
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["stdout"], "final answer")
        self.assertEqual(payload["stderr"], "")
        self.assertNotIn("stderr_plain", payload)

    def test_timeout_salvages_codex_final_message(self):
        # codex writes its answer to --output-last-message, not stdout. A run
        # killed one second before it would have returned still has the
        # finished answer on disk; reporting empty stdout discards it.
        def fake_run(cmd, prompt, cwd, stdout_path, stderr_path, timeout, **kwargs):
            output_index = cmd.index("--output-last-message") + 1
            Path(cmd[output_index]).write_text("salvaged answer", encoding="utf-8")
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text("codex transcript", encoding="utf-8")
            raise yoyo.subprocess.TimeoutExpired(cmd, timeout)

        with mock.patch.object(yoyo, "run_to_files", side_effect=fake_run):
            code, stdout, stderr = self.run_cli(
                ["ask", "codex", "--json", "--timeout", "0.1", "hello"],
            )

        self.assertEqual(code, 124)
        payload = json.loads(stdout)
        self.assertEqual(payload["exit_code"], 124)
        self.assertEqual(payload["stdout"], "salvaged answer")
        self.assertIn("Timed out after 0.1s", payload["stderr"])

    def test_idle_timeout_salvages_codex_final_message(self):
        def fake_run(cmd, prompt, cwd, stdout_path, stderr_path, timeout, **kwargs):
            output_index = cmd.index("--output-last-message") + 1
            Path(cmd[output_index]).write_text("salvaged answer", encoding="utf-8")
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            raise yoyo.AgentIdleTimeout(idle_seconds=0.3, captured_bytes=0)

        with mock.patch.object(yoyo, "run_to_files", side_effect=fake_run):
            code, stdout, stderr = self.run_cli(
                ["ask", "codex", "--json", "--idle-timeout", "0.3", "hello"],
            )

        self.assertEqual(code, 124)
        payload = json.loads(stdout)
        self.assertEqual(payload["stdout"], "salvaged answer")
        self.assertIn("Idle timeout after 0.3s", payload["stderr"])

    def test_timeout_keeps_stdout_when_final_message_empty(self):
        # An untouched or blank final-message file must not blank out the
        # bytes the agent did write before the kill.
        def fake_run(cmd, prompt, cwd, stdout_path, stderr_path, timeout, **kwargs):
            output_index = cmd.index("--output-last-message") + 1
            Path(cmd[output_index]).write_text("   \n", encoding="utf-8")
            stdout_path.write_text("partial transcript", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            raise yoyo.subprocess.TimeoutExpired(cmd, timeout)

        with mock.patch.object(yoyo, "run_to_files", side_effect=fake_run):
            code, stdout, stderr = self.run_cli(
                ["ask", "codex", "--json", "--timeout", "0.1", "hello"],
            )

        self.assertEqual(code, 124)
        payload = json.loads(stdout)
        self.assertEqual(payload["stdout"], "partial transcript")

    def test_plain_output_uses_stderr_plain(self):
        result = {
            "stdout": "ok\n",
            "stderr": "raw stderr\n",
            "stderr_plain": "",
            "trace_id": "trace-plain",
        }
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            yoyo.emit_result(result, as_json=False)

        self.assertEqual(stdout.getvalue(), "ok\n")
        self.assertEqual(stderr.getvalue(), "trace_id=trace-plain\n")

    def test_unknown_agent_lists_known_agents(self):
        code, stdout, stderr = self.run_cli(["ask", "nobody", "hi"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Unknown agent", stderr)
        self.assertIn("codex", stderr)

    def test_absolute_custom_agent_is_reported_as_found(self):
        env = {"YOYO_AGENT_PY": "/usr/bin/env"}
        code, stdout, stderr = self.run_cli(["agents"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertIn("py", stdout)
        self.assertIn("ok", stdout)

    def test_update_dry_run_uses_recorded_source_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (source / ".git").mkdir()
            record = Path(tmp) / "record"
            record.write_text(str(source), encoding="utf-8")

            with mock.patch.object(yoyo, "git_current_branch", side_effect=AssertionError("dry-run should not inspect git")):
                code, stdout, stderr = self.run_cli(
                    ["update", "--dry-run"],
                    env={"YOYO_SOURCE_RECORD": str(record)},
                )

        self.assertEqual(code, 0, stderr)
        self.assertIn("git fetch origin '<current-branch>'", stdout)
        self.assertIn("git pull --ff-only origin '<current-branch>'", stdout)
        self.assertIn(f"/bin/sh {source.resolve() / 'install.sh'}", stdout)

    def test_update_no_pull_dry_run_skips_git_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            record = Path(tmp) / "record"
            record.write_text(str(source), encoding="utf-8")

            code, stdout, stderr = self.run_cli(
                ["update", "--no-pull", "--dry-run"],
                env={"YOYO_SOURCE_RECORD": str(record)},
            )

        self.assertEqual(code, 0, stderr)
        self.assertNotIn("git fetch", stdout)
        self.assertNotIn("git pull", stdout)
        self.assertIn(f"/bin/sh {source.resolve() / 'install.sh'}", stdout)

    def test_update_without_recorded_source_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_record = Path(tmp) / "missing"
            code, stdout, stderr = self.run_cli(
                ["update", "--dry-run"],
                env={"YOYO_SOURCE_RECORD": str(missing_record)},
            )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("No recorded yoyo source checkout", stderr)

    def test_doctor_reports_source_root_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["doctor"],
                env={"YOYO_SOURCE_ROOT": str(source)},
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn(f"source: {source.resolve()} (present)", stdout)

    def test_doctor_live_reports_fake_agent_modes_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._doctor_agent_config(
                tmp,
                "probe",
                [
                    "python3",
                    "-c",
                    "import sys; data = sys.stdin.read(); "
                    "print('OK') if data.lower().endswith('reply with exactly: ok') else sys.exit(9)",
                ],
                read_only_args=["--safe"],
            )
            code, stdout, stderr = self.run_cli(
                ["doctor", "--live", "--agent", "probe"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertIn("probe: read-only ok", stdout)
        self.assertIn("probe: full-access ok", stdout)

    def test_doctor_live_read_only_probe_asks_for_a_write_and_checks_disk(self):
        # A CLI can accept its read-only flags and still let a write through
        # (agy 1.1.27 did under --dangerously-skip-permissions); only a landed
        # file proves the mode. The probe asks for one and looks.
        self.assertIn(yoyo.DOCTOR_PROBE_FILE, yoyo.DOCTOR_READ_ONLY_PROBE_PROMPT)
        with tempfile.TemporaryDirectory() as tmp:
            seen = Path(tmp) / "prompts.txt"
            config = self._doctor_agent_config(
                tmp,
                "leaky",
                [
                    "python3",
                    "-c",
                    "import os, sys; data = sys.stdin.read(); "
                    f"open({str(seen)!r}, 'a').write(data + '\\n---\\n'); "
                    "open('yoyo-doctor-probe.txt', 'w').write('OK') if '--safe' in sys.argv else None; "
                    "print('OK')",
                ],
                read_only_args=["--safe"],
            )
            code, stdout, stderr = self.run_cli(
                ["doctor", "--live", "--agent", "leaky", "--json"],
                env={"YOYO_CONFIG": str(config)},
            )
            prompts = seen.read_text(encoding="utf-8")

        self.assertEqual(code, 0, stderr)
        self.assertIn("yoyo-doctor-probe.txt", prompts)
        self.assertIn("Reply with exactly: OK", prompts)
        rows = {row["mode"]: row for row in json.loads(stdout)}
        self.assertFalse(rows["read-only"]["ok"])
        self.assertEqual(rows["read-only"]["status"], "failed")
        self.assertEqual(rows["read-only"]["exit_code"], 0)
        self.assertFalse(rows["read-only"]["enforced"])
        self.assertIn("read-only not enforced", rows["read-only"]["stderr_snippet"])
        self.assertTrue(rows["full-access"]["ok"])
        self.assertNotIn("enforced", rows["full-access"])

    def test_doctor_live_passes_a_denied_write_that_ended_the_run(self):
        # agy under --read-only may answer the temptation by calling a tool
        # headless mode must deny; the run then ends with exit 0 and no
        # answer. No file landed, so the read-only path held — doctor says
        # ok, and says what happened, because a real call can end that way.
        diagnosis = (
            'jetski: no output produced — a tool required the "write_file" permission that '
            "headless mode cannot prompt for, so it was auto-denied."
        )
        with tempfile.TemporaryDirectory() as tmp:
            body = (
                "case \"$*\" in *--sandbox*) printf '%s\\n' " + shlex.quote(diagnosis) + " >&2; exit 0;; esac\n"
                "echo OK\n"
            )
            config = self._fake_agy_config(tmp, body)
            code, stdout, stderr = self.run_cli(
                ["doctor", "--live", "--agent", "fakeagy", "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        rows = {row["mode"]: row for row in json.loads(stdout)}
        self.assertTrue(rows["read-only"]["ok"])
        self.assertEqual(rows["read-only"]["status"], "ok")
        self.assertEqual(rows["read-only"]["exit_code"], 1)
        self.assertTrue(rows["read-only"]["enforced"])
        self.assertIn("denied", rows["read-only"]["note"])
        self.assertTrue(rows["full-access"]["ok"])

        with tempfile.TemporaryDirectory() as tmp:
            config = self._fake_agy_config(tmp, body)
            code, stdout, stderr = self.run_cli(
                ["doctor", "--live", "--agent", "fakeagy", "--strict"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("fakeagy: read-only ok", stdout)
        self.assertIn("denied", stdout)

    def test_doctor_live_strict_fails_on_an_unenforced_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._doctor_agent_config(
                tmp,
                "leaky",
                [
                    "python3",
                    "-c",
                    "import sys; sys.stdin.read(); "
                    "open('yoyo-doctor-probe.txt', 'w').write('OK') if '--safe' in sys.argv else None; "
                    "print('OK')",
                ],
                read_only_args=["--safe"],
            )
            code, stdout, stderr = self.run_cli(
                ["doctor", "--live", "--agent", "leaky", "--strict"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 1, stderr)
        self.assertIn("leaky: read-only FAIL exit 0", stdout)
        self.assertIn("read-only not enforced", stdout)
        self.assertIn("leaky: full-access ok", stdout)

    def test_doctor_live_strict_exits_one_when_probe_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._doctor_agent_config(
                tmp,
                "fail",
                ["python3", "-c", "import sys; print('unknown option', file=sys.stderr); sys.exit(7)"],
                read_only_args=["--safe"],
            )
            code, stdout, stderr = self.run_cli(
                ["doctor", "--live", "--agent", "fail", "--strict"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 1, stderr)
        self.assertIn("fail: read-only FAIL exit 7", stdout)
        self.assertIn("unknown option", stdout)

    def test_doctor_live_unknown_agent_fails_loudly(self):
        code, stdout, stderr = self.run_cli(["doctor", "--live", "--agent", "unknown-name"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Unknown agent 'unknown-name'", stderr)

    def test_doctor_live_skips_custom_read_only_without_configured_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._doctor_agent_config(
                tmp,
                "writeonly",
                ["python3", "-c", "print('OK')"],
            )
            code, stdout, stderr = self.run_cli(
                ["doctor", "--live", "--agent", "writeonly"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("writeonly: read-only skipped: no read_only_args", stdout)
        self.assertIn("writeonly: full-access ok", stdout)

    def test_doctor_live_json_reports_probe_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._doctor_agent_config(
                tmp,
                "probe",
                ["python3", "-c", "print('OK')"],
                read_only_args=["--safe"],
            )
            code, stdout, stderr = self.run_cli(
                ["doctor", "--live", "--agent", "probe", "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual([row["mode"] for row in payload], ["read-only", "full-access"])
        for row in payload:
            self.assertEqual(row["agent"], "probe")
            self.assertTrue(row["ok"])
            self.assertEqual(row["status"], "ok")
            self.assertEqual(row["exit_code"], 0)
            self.assertIsInstance(row["duration_s"], float)
            self.assertIn("stderr_snippet", row)

    def test_install_skill_installs_all_bundled_skill_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("base", encoding="utf-8")
            (source / "yoyo-workflow").mkdir()
            (source / "yoyo-workflow" / "SKILL.md").write_text("workflow", encoding="utf-8")
            home = Path(tmp) / "home"
            pi_dir = Path(tmp) / "pi"

            code, stdout, stderr = self.run_cli(
                ["install-skill"],
                env={
                    "YOYO_SKILL_SOURCE": str(source),
                    "HOME": str(home),
                    "PI_CODING_AGENT_DIR": str(pi_dir),
                },
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("yoyo", stdout)
            self.assertIn("yoyo-workflow", stdout)
            for root in (".codex", ".claude", ".agents", ".config/opencode", ".gemini/config", ".grok"):
                self.assertTrue((home / root / "skills" / "yoyo" / "SKILL.md").exists(), root)
                self.assertTrue((home / root / "skills" / "yoyo-workflow" / "SKILL.md").exists(), root)
            # Pi's own skills dir is not an install target: pi reads
            # ~/.agents/skills natively, so a copy there would collide.
            self.assertFalse((pi_dir.resolve() / "skills" / "yoyo").exists())
            self.assertFalse((pi_dir.resolve() / "skills" / "yoyo-workflow").exists())

    def test_install_skill_prunes_stale_files_from_existing_skill_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("base", encoding="utf-8")
            home = Path(tmp) / "home"
            stale = home / ".agents" / "skills" / "yoyo" / "old.txt"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale", encoding="utf-8")

            code, stdout, stderr = self.run_cli(
                ["install-skill"],
                env={
                    "YOYO_SKILL_SOURCE": str(source),
                    "HOME": str(home),
                    "PI_CODING_AGENT_DIR": str(Path(tmp) / "pi"),
                },
            )

            self.assertEqual(code, 0, stderr)
            self.assertFalse(stale.exists())
            self.assertTrue((home / ".agents" / "skills" / "yoyo" / "SKILL.md").exists())

    def test_install_skill_removes_legacy_pi_home_skill_copies(self):
        # Pi reads ~/.agents/skills natively, so a bundled-skill copy in pi's
        # own skills dir collides with the shared one at pi startup. Yoyo
        # installed that copy in older releases, so yoyo takes it back out.
        # A directory that is not part of the bundle is none of its business.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("base", encoding="utf-8")
            home = Path(tmp) / "home"
            pi_dir = Path(tmp) / "pi"
            legacy = pi_dir / "skills" / "yoyo"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("base", encoding="utf-8")
            foreign = pi_dir / "skills" / "no-ai-slop"
            foreign.mkdir(parents=True)
            (foreign / "SKILL.md").write_text("mine", encoding="utf-8")

            code, stdout, stderr = self.run_cli(
                ["install-skill"],
                env={
                    "YOYO_SKILL_SOURCE": str(source),
                    "HOME": str(home),
                    "PI_CODING_AGENT_DIR": str(pi_dir),
                },
            )

            self.assertEqual(code, 0, stderr)
            self.assertFalse(legacy.exists())
            self.assertIn("removed legacy skill", stdout)
            self.assertTrue(foreign.exists())

    def test_install_skill_moves_modified_pi_home_skill_copies_aside_intact(self):
        # Only a copy identical to the current bundle is yoyo's own artifact.
        # Anything else — a hand-edited SKILL.md, a copy from an older release
        # whose SKILL.md has since changed — still collides, so it is moved
        # aside recoverably instead of deleted.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("canonical", encoding="utf-8")
            home = Path(tmp) / "home"
            pi_dir = Path(tmp) / "pi"
            legacy = pi_dir / "skills" / "yoyo"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("from an older release", encoding="utf-8")

            code, stdout, stderr = self.run_cli(
                ["install-skill"],
                env={
                    "YOYO_SKILL_SOURCE": str(source),
                    "HOME": str(home),
                    "PI_CODING_AGENT_DIR": str(pi_dir),
                },
            )

            self.assertEqual(code, 0, stderr)
            self.assertFalse(legacy.exists())
            self.assertNotIn("removed legacy skill", stdout)
            self.assertIn("moved legacy skill aside", stdout)
            aside = pi_dir / "yoyo.legacy-moved-by-yoyo"
            self.assertTrue(aside.is_dir())
            self.assertEqual((aside / "SKILL.md").read_text(encoding="utf-8"), "from an older release")

    def test_install_skill_never_deletes_a_copy_with_extra_user_files(self):
        # An unchanged SKILL.md is not proof the directory is yoyo's: user
        # files sitting beside it must survive. The copy is moved aside whole.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("base", encoding="utf-8")
            home = Path(tmp) / "home"
            pi_dir = Path(tmp) / "pi"
            legacy = pi_dir / "skills" / "yoyo"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("base", encoding="utf-8")
            (legacy / "notes.txt").write_text("mine", encoding="utf-8")

            code, stdout, stderr = self.run_cli(
                ["install-skill"],
                env={
                    "YOYO_SKILL_SOURCE": str(source),
                    "HOME": str(home),
                    "PI_CODING_AGENT_DIR": str(pi_dir),
                },
            )

            self.assertEqual(code, 0, stderr)
            self.assertFalse(legacy.exists())
            aside = pi_dir / "yoyo.legacy-moved-by-yoyo"
            self.assertTrue(aside.is_dir())
            self.assertEqual((aside / "notes.txt").read_text(encoding="utf-8"), "mine")
            self.assertIn("moved legacy skill aside", stdout)

    def test_install_skill_moves_a_symlinked_pi_home_skill_aside_without_touching_the_target(self):
        # Pi follows directory symlinks during skill discovery, so a symlink
        # in the pi home collides like a real copy. The link is renamed aside
        # whole — the directory it points at is never touched.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("base", encoding="utf-8")
            home = Path(tmp) / "home"
            pi_dir = Path(tmp) / "pi"
            target_dir = Path(tmp) / "elsewhere" / "yoyo"
            target_dir.mkdir(parents=True)
            (target_dir / "SKILL.md").write_text("external", encoding="utf-8")
            legacy = pi_dir / "skills" / "yoyo"
            legacy.parent.mkdir(parents=True)
            legacy.symlink_to(target_dir)

            code, stdout, stderr = self.run_cli(
                ["install-skill"],
                env={
                    "YOYO_SKILL_SOURCE": str(source),
                    "HOME": str(home),
                    "PI_CODING_AGENT_DIR": str(pi_dir),
                },
            )

            self.assertEqual(code, 0, stderr)
            self.assertFalse(os.path.lexists(str(legacy)))
            self.assertTrue(target_dir.is_dir())
            self.assertEqual((target_dir / "SKILL.md").read_text(encoding="utf-8"), "external")
            aside = pi_dir / "yoyo.legacy-moved-by-yoyo"
            self.assertTrue(aside.is_symlink())
            self.assertEqual((aside / "SKILL.md").read_text(encoding="utf-8"), "external")
            self.assertIn("moved legacy skill symlink aside", stdout)

    def test_install_skill_moves_a_copy_with_an_extra_empty_directory_aside(self):
        # Directories count in the ownership comparison: a copy that is
        # byte-identical in its files but holds an extra (even empty) user
        # directory is not provably yoyo's and is moved aside, not deleted.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("base", encoding="utf-8")
            home = Path(tmp) / "home"
            pi_dir = Path(tmp) / "pi"
            legacy = pi_dir / "skills" / "yoyo"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("base", encoding="utf-8")
            (legacy / "user-notes").mkdir()

            code, stdout, stderr = self.run_cli(
                ["install-skill"],
                env={
                    "YOYO_SKILL_SOURCE": str(source),
                    "HOME": str(home),
                    "PI_CODING_AGENT_DIR": str(pi_dir),
                },
            )

            self.assertEqual(code, 0, stderr)
            self.assertFalse(legacy.exists())
            self.assertIn("moved legacy skill aside", stdout)
            aside = pi_dir / "yoyo.legacy-moved-by-yoyo"
            self.assertTrue((aside / "user-notes").is_dir())

    def test_install_skill_moves_a_copy_with_an_unreadable_subdirectory_aside(self):
        # An unreadable subdirectory makes the tree unprovable — it must fail
        # closed (move aside) rather than being treated as an identical copy.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("base", encoding="utf-8")
            home = Path(tmp) / "home"
            pi_dir = Path(tmp) / "pi"
            legacy = pi_dir / "skills" / "yoyo"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("base", encoding="utf-8")
            locked = legacy / "locked"
            locked.mkdir()
            (locked / "inner.txt").write_text("secret-ish", encoding="utf-8")
            locked.chmod(0o000)
            aside = pi_dir / "yoyo.legacy-moved-by-yoyo"
            try:
                code, stdout, stderr = self.run_cli(
                    ["install-skill"],
                    env={
                        "YOYO_SKILL_SOURCE": str(source),
                        "HOME": str(home),
                        "PI_CODING_AGENT_DIR": str(pi_dir),
                    },
                )
            finally:
                # The whole tree may have been moved aside under the lock.
                for candidate in (locked, aside / "locked"):
                    if candidate.is_dir():
                        candidate.chmod(0o755)

            self.assertEqual(code, 0, stderr)
            self.assertFalse(legacy.exists())
            self.assertIn("moved legacy skill aside", stdout)
            aside = pi_dir / "yoyo.legacy-moved-by-yoyo"
            self.assertEqual((aside / "locked" / "inner.txt").read_text(encoding="utf-8"), "secret-ish")

    def test_doctor_spares_shared_home_when_pi_dir_aliases_it(self):
        # PI_CODING_AGENT_DIR=~/.agents makes pi's skills dir the shared
        # install target: one healthy copy, nothing legacy to report.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            source = Path(tmp) / "source"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            shared = home / ".agents" / "skills" / "yoyo"
            shared.mkdir(parents=True)
            (shared / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(home / ".agents"),
                "YOYO_SKILL_SOURCE": str(source),
                "YOYO_STATE_DIR": tmp,
                "YOYO_CONFIG": str(Path(tmp) / "missing.json"),
            }

            code, stdout, stderr = self.run_cli(["doctor"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("in sync", stdout)
            self.assertNotIn("LEGACY", stdout)

    def test_install_skill_spares_shared_home_when_pi_dir_aliases_it(self):
        # PI_CODING_AGENT_DIR pointing at ~/.agents makes pi's skills dir the
        # same directory as the shared install target; the installer must not
        # then delete what it just installed as "legacy".
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("base", encoding="utf-8")
            home = Path(tmp) / "home"
            pi_dir = home / ".agents"

            code, stdout, stderr = self.run_cli(
                ["install-skill"],
                env={
                    "YOYO_SKILL_SOURCE": str(source),
                    "HOME": str(home),
                    "PI_CODING_AGENT_DIR": str(pi_dir),
                },
            )

            self.assertEqual(code, 0, stderr)
            self.assertTrue((home / ".agents" / "skills" / "yoyo" / "SKILL.md").exists())
            self.assertNotIn("removed legacy skill", stdout)

    def test_install_skill_removes_legacy_copies_of_skipped_skills(self):
        # Cleanup covers every bundled skill, including ones skipped this run
        # because their powering agent is missing — otherwise doctor keeps
        # recommending an install-skill run that cannot fix the warning.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo-imagegen").mkdir(parents=True)
            (source / "yoyo-imagegen" / "SKILL.md").write_text("imagegen", encoding="utf-8")
            home = Path(tmp) / "home"
            pi_dir = Path(tmp) / "pi"
            legacy = pi_dir / "skills" / "yoyo-imagegen"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("imagegen", encoding="utf-8")

            env = {
                "YOYO_SKILL_SOURCE": str(source),
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(pi_dir),
            }
            real_which = yoyo.shutil.which
            with mock.patch.object(yoyo.shutil, "which", side_effect=lambda name: None if name == "codex" else real_which(name)):
                code, stdout, stderr = self.run_cli(["install-skill"], env=env)

            self.assertEqual(code, 0, stderr)
            self.assertIn("skipped skill: yoyo-imagegen (requires codex on PATH)", stdout)
            self.assertFalse((home / ".agents" / "skills" / "yoyo-imagegen").exists())
            self.assertFalse(legacy.exists())
            self.assertIn("removed legacy skill", stdout)























    def test_open_idle_stdin_pipe_does_not_block_or_inject(self):
        # A prompt arg plus an open-but-idle stdin pipe (the agent-to-agent case)
        # previously blocked forever in build_prompt before launching the agent.
        env = {"YOYO_AGENT_ECHO": "cat"}
        read_fd, write_fd = os.pipe()  # writer never writes: never ready, never EOF
        reader = os.fdopen(read_fd, "r")
        merged_env = os.environ.copy()
        merged_env.update(env)
        # This test drives main() directly, so it repeats run_cli's guard: the
        # call it makes is journaled, and must not land in the real ledger.
        merged_env.setdefault("YOYO_STATE_DIR", self._state_guard.name)
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with mock.patch.dict(os.environ, merged_env, clear=True):
                with mock.patch("sys.stdin", reader):
                    with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                        code = yoyo.main(["ask", "echo", "--json", "prompt-only"])
            self.assertEqual(code, 0, stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertNotIn("<stdin>", payload["stdout"])
            self.assertIn("Task:\nprompt-only", payload["stdout"])
        finally:
            try:
                reader.close()
            except OSError:
                pass
            try:
                os.close(write_fd)
            except OSError:
                pass

    def test_no_stdin_flag_excludes_stdin_context(self):
        env = {"YOYO_AGENT_ECHO": "cat"}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--json", "--no-stdin", "prompt"],
            stdin="SHOULD-NOT-APPEAR",
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertNotIn("SHOULD-NOT-APPEAR", payload["stdout"])
        self.assertNotIn("<stdin>", payload["stdout"])

    def test_idle_timeout_returns_124_with_idle_message(self):
        # Speaks once, then stalls: a real hang the idle guard must catch.
        env = {"YOYO_AGENT_SLEEP": "python3 -c \"import time; print('working', flush=True); time.sleep(5)\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "sleep", "--json", "--idle-timeout", "0.3", "hello"],
            env=env,
        )

        self.assertEqual(code, 124)
        payload = json.loads(stdout)
        self.assertEqual(payload["exit_code"], 124)
        self.assertIn("Idle timeout", payload["stderr"])
        self.assertNotIn("stderr_plain", payload)

    def test_idle_timeout_ignores_silence_before_first_output(self):
        # Agents that buffer everything until exit (claude, cursor, pi) emit
        # nothing while they think. Arming the idle clock at process start
        # turns --idle-timeout N into a hard kill at N seconds.
        env = {"YOYO_AGENT_LATE": "python3 -c \"import time; time.sleep(1.2); print('late answer')\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "late", "--json", "--idle-timeout", "0.4", "hello"],
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertIn("late answer", payload["stdout"])

    def test_invalid_idle_timeout_fails_loudly(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "codex", "--idle-timeout", "0", "hello"],
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--idle-timeout must be greater than 0", stderr)

    def test_resolve_heartbeat_and_idle_timeout_from_env(self):
        ns = argparse.Namespace(quiet=False, idle_timeout=None)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(yoyo.resolve_heartbeat(ns), yoyo.DEFAULT_HEARTBEAT_SECONDS)
            self.assertIsNone(yoyo.resolve_idle_timeout(ns))
        with mock.patch.dict(os.environ, {"YOYO_HEARTBEAT_SECS": "0"}, clear=True):
            self.assertIsNone(yoyo.resolve_heartbeat(ns))
        with mock.patch.dict(os.environ, {"YOYO_HEARTBEAT_SECS": "5"}, clear=True):
            self.assertEqual(yoyo.resolve_heartbeat(ns), 5.0)
        with mock.patch.dict(os.environ, {"YOYO_IDLE_TIMEOUT": "7"}, clear=True):
            self.assertEqual(yoyo.resolve_idle_timeout(ns), 7.0)
        with mock.patch.dict(os.environ, {"YOYO_HEARTBEAT_SECS": "nope"}, clear=True):
            with self.assertRaises(yoyo.YoyoError):
                yoyo.resolve_heartbeat(ns)

    def test_quiet_suppresses_heartbeat(self):
        ns = argparse.Namespace(quiet=True, idle_timeout=None)
        with mock.patch.dict(os.environ, {"YOYO_HEARTBEAT_SECS": "1"}, clear=True):
            self.assertIsNone(yoyo.resolve_heartbeat(ns))

    def test_kill_active_children_terminates_registered_group(self):
        if os.name != "posix":
            self.skipTest("posix process-group cleanup")
        proc = yoyo.subprocess.Popen(
            ["python3", "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        pgid = os.getpgid(proc.pid)
        yoyo._register_child(pgid)
        try:
            yoyo._kill_active_children(yoyo.signal.SIGKILL)
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.returncode)
        finally:
            yoyo._unregister_child(pgid)
            if proc.returncode is None:
                proc.kill()
                proc.wait()


    def _echo_agent_config(self, tmp):
        config = Path(tmp) / "agents.json"
        config.write_text(
            json.dumps(
                {
                    "agents": {
                        "echo": {
                            "command": ["python3", "-c", "import sys; print(sys.stdin.read())"],
                            "read_only_args": ["--safe"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return config

    def _doctor_agent_config(self, tmp, name, command, *, read_only_args=None):
        raw = {"command": command}
        if read_only_args is not None:
            raw["read_only_args"] = read_only_args
        config = Path(tmp) / "agents.json"
        config.write_text(json.dumps({"agents": {name: raw}}), encoding="utf-8")
        return config

    def _write_run_meta(self, state_dir, run_id, *, pid, started_at="2024-01-01T00:00:00Z"):
        run_dir = Path(state_dir) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "run_id": run_id,
            "agent": "echo",
            "role": "opinion",
            "cwd": str(ROOT),
            "trace_id": "trace-test",
            "caller": "test",
            "argv": ["python3", str(YOYO_PATH), "ask", "echo", "--json", "hello"],
            "pid": pid,
            "started_at": started_at,
        }
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return run_dir

    def _write_session_record(self, state_dir, agent, name, backend_id):
        state_path = Path(state_dir)
        state_path.mkdir(parents=True, exist_ok=True)
        key = f"{agent}:{name}"
        payload = {
            "sessions": {
                key: {
                    "agent": agent,
                    "name": name,
                    "backend_id": backend_id,
                    "created_at": "2026-06-10T00:00:00Z",
                    "last_used": "2026-06-10T00:00:00Z",
                }
            }
        }
        (state_path / "sessions.json").write_text(json.dumps(payload), encoding="utf-8")
        return key

    def _write_skill(self, root, name, body="Use 8px spacing. Avoid generic gradients."):
        skill_dir = Path(root) / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n\n{body}\n", encoding="utf-8")
        return skill_dir

    def test_ask_skill_is_injected_into_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_skill(Path(tmp) / "skills", "frontend")
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--skill", "frontend", "--dry-run", "Build the page."],
                env={
                    "YOYO_AGENT_ECHO": "true",
                    "YOYO_SKILL_PATH": str(Path(tmp) / "skills"),
                },
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn('<skill name="frontend">', stdout)
        self.assertIn("Use 8px spacing", stdout)
        self.assertIn("Task:\nBuild the page.", stdout)

    def test_ask_missing_skill_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--skill", "does-not-exist", "--dry-run", "Build."],
                env={
                    "YOYO_AGENT_ECHO": "true",
                    "YOYO_SKILL_PATH": str(Path(tmp)),
                },
            )

        self.assertEqual(code, 2)
        self.assertIn("Skill not found", stderr)

    def test_ask_skill_missing_path_fails_loudly(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--skill", "../evil", "--dry-run", "Build."],
            env={"YOYO_AGENT_ECHO": "true"},
        )

        self.assertEqual(code, 2)
        self.assertIn("Skill path not found", stderr)

    def test_ask_skill_accepts_explicit_rules_file_path(self):
        # A ponytail-style overlay is just a markdown rules file; a path-based
        # --skill injects it without installing anything.
        with tempfile.TemporaryDirectory() as tmp:
            rules = Path(tmp) / "ponytail.md"
            rules.write_text("Write the least code that works.", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--skill", str(rules), "--dry-run", "Build."],
                env={"YOYO_AGENT_ECHO": "true"},
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn('<skill name="ponytail">', stdout)
        self.assertIn("Write the least code that works.", stdout)

    def test_ask_skill_accepts_directory_with_skill_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "senior-mode"
            pack.mkdir()
            (pack / "SKILL.md").write_text("Prefer stdlib over new dependencies.", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--skill", str(pack), "--dry-run", "Build."],
                env={"YOYO_AGENT_ECHO": "true"},
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn('<skill name="senior-mode">', stdout)
            self.assertIn("Prefer stdlib", stdout)

            empty = Path(tmp) / "empty-pack"
            empty.mkdir()
            code, _, stderr = self.run_cli(
                ["ask", "echo", "--skill", str(empty), "--dry-run", "Build."],
                env={"YOYO_AGENT_ECHO": "true"},
            )
            self.assertEqual(code, 2)
            self.assertIn("no SKILL.md", stderr)

    def test_skills_command_lists_discovered_skills_first_root_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "a"
            root_b = Path(tmp) / "b"
            self._write_skill(root_a, "alpha", body="from-a")
            self._write_skill(root_b, "alpha", body="from-b")
            self._write_skill(root_b, "beta")
            code, stdout, stderr = self.run_cli(
                ["skills", "--json"],
                env={"YOYO_SKILL_PATH": f"{root_a}{os.pathsep}{root_b}"},
            )

        self.assertEqual(code, 0, stderr)
        rows = {row["name"]: row["path"] for row in json.loads(stdout)}
        self.assertEqual(rows["alpha"], str(root_a / "alpha"))
        self.assertEqual(rows["beta"], str(root_b / "beta"))












    def _imagegen_agent_config(self, tmp, script):
        config = Path(tmp) / "agents.json"
        config.write_text(
            json.dumps({"agents": {"fakegen": {"command": ["python3", "-c", script], "read_only_args": ["--safe"]}}}),
            encoding="utf-8",
        )
        return config

    IMAGEGEN_WRITER = (
        "import re, sys\n"
        "prompt = sys.stdin.read()\n"
        "match = re.search(r'exactly this path: (\\S+)', prompt)\n"
        "path = match.group(1)\n"
        "open(path, 'wb').write(b'\\x89PNG\\r\\n\\x1a\\n' + b'0' * 4096)\n"
        "print('generated', path)\n"
    )

    def test_imagegen_generates_and_verifies_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._imagegen_agent_config(tmp, self.IMAGEGEN_WRITER)
            out = Path(tmp) / "art.png"
            code, stdout, stderr = self.run_cli(
                ["imagegen", "a red yo-yo", "--agent", "fakegen", "--out", str(out), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["verified"])
        self.assertEqual(payload["out"], str(out))
        self.assertGreater(payload["bytes"], 1024)

    def test_imagegen_background_detaches_and_verifies_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._imagegen_agent_config(tmp, self.IMAGEGEN_WRITER)
            out = Path(tmp) / "art.png"
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["imagegen", "a red yo-yo", "--agent", "fakegen", "--out", str(out), "--background"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()
            self.assertRegex(run_id, r"^\d{8}T\d{6}-[0-9a-f]{8}$")

            code, stdout, stderr = self.run_cli(["wait", run_id, "--timeout", "15", "--poll", "0.05"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertTrue(out.is_file())
            result = json.loads((Path(tmp) / "state" / "runs" / run_id / "result.json").read_text(encoding="utf-8"))
            self.assertTrue(result["verified"])
            self.assertEqual(result["out"], str(out))

    def test_imagegen_fails_loudly_when_no_image_is_created(self):
        script = "import sys; sys.stdin.read(); print('done, honest')"
        with tempfile.TemporaryDirectory() as tmp:
            config = self._imagegen_agent_config(tmp, script)
            out = Path(tmp) / "art.png"
            code, stdout, stderr = self.run_cli(
                ["imagegen", "a red yo-yo", "--agent", "fakegen", "--out", str(out)],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 2)
        self.assertIn("Image not created", stderr)

    def test_imagegen_rejects_non_image_bytes(self):
        script = (
            "import re, sys\n"
            "prompt = sys.stdin.read()\n"
            "path = re.search(r'exactly this path: (\\S+)', prompt).group(1)\n"
            "open(path, 'w').write('<svg>fake</svg>' * 200)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            config = self._imagegen_agent_config(tmp, script)
            out = Path(tmp) / "art.png"
            code, stdout, stderr = self.run_cli(
                ["imagegen", "a red yo-yo", "--agent", "fakegen", "--out", str(out)],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 2)
        self.assertIn("not a valid .png image", stderr)

    def test_imagegen_rejects_stale_unchanged_output(self):
        script = "import sys; sys.stdin.read(); print('pretended to work')"
        with tempfile.TemporaryDirectory() as tmp:
            config = self._imagegen_agent_config(tmp, script)
            out = Path(tmp) / "art.png"
            out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 4096)
            code, stdout, stderr = self.run_cli(
                ["imagegen", "a red yo-yo", "--agent", "fakegen", "--out", str(out)],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 2)
        self.assertIn("unchanged", stderr)

    def test_imagegen_validates_extension_size_and_edit_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._imagegen_agent_config(tmp, self.IMAGEGEN_WRITER)
            env = {"YOYO_CONFIG": str(config)}
            code, _, stderr = self.run_cli(
                ["imagegen", "x", "--agent", "fakegen", "--out", str(Path(tmp) / "a.gif")], env=env
            )
            self.assertEqual(code, 2)
            self.assertIn("Unsupported image extension", stderr)

            code, _, stderr = self.run_cli(
                ["imagegen", "x", "--agent", "fakegen", "--out", str(Path(tmp) / "a.png"), "--size", "huge"], env=env
            )
            self.assertEqual(code, 2)
            self.assertIn("Invalid --size", stderr)

            code, _, stderr = self.run_cli(
                ["imagegen", "x", "--agent", "fakegen", "--out", str(Path(tmp) / "a.png"), "--edit", str(Path(tmp) / "no.png")],
                env=env,
            )
            self.assertEqual(code, 2)
            self.assertIn("Edit reference image not found", stderr)

    def test_imagegen_dry_run_renders_prompt_without_generating(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._imagegen_agent_config(tmp, self.IMAGEGEN_WRITER)
            out = Path(tmp) / "art.png"
            code, stdout, stderr = self.run_cli(
                ["imagegen", "a red yo-yo", "--agent", "fakegen", "--out", str(out), "--dry-run"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Do NOT draw or render the image with code", stdout)
        self.assertIn(str(out), stdout)


    def test_imagegen_codex_edit_mentions_reference_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / "ref.png"
            ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 4096)
            out = Path(tmp) / "art-v2.png"
            code, stdout, stderr = self.run_cli(
                ["imagegen", "make it blue", "--edit", str(ref), "--out", str(out), "--dry-run"],
                env={"YOYO_CONFIG": str(Path(tmp) / "missing.json")},
            )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Edit the existing image", stdout)
        self.assertIn(str(ref), stdout)

    def test_install_skill_skips_imagegen_skill_without_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {"HOME": str(home), "PI_CODING_AGENT_DIR": str(home / ".pi/agent")}
            real_which = yoyo.shutil.which
            with mock.patch.object(yoyo.shutil, "which", side_effect=lambda name: None if name == "codex" else real_which(name)):
                code, stdout, stderr = self.run_cli(["install-skill"], env=env)

            self.assertEqual(code, 0, stderr)
            self.assertIn("skipped skill: yoyo-imagegen (requires codex on PATH)", stdout)
            self.assertFalse((home / ".claude/skills/yoyo-imagegen").exists())
            self.assertTrue((home / ".claude/skills/yoyo").exists())

    def test_install_skill_installs_imagegen_skill_with_codex_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {"HOME": str(home), "PI_CODING_AGENT_DIR": str(home / ".pi/agent")}
            with mock.patch.object(yoyo.shutil, "which", return_value="/usr/bin/fake"):
                code, stdout, stderr = self.run_cli(["install-skill"], env=env)

            self.assertEqual(code, 0, stderr)
            self.assertTrue((home / ".claude/skills/yoyo-imagegen/SKILL.md").exists())

    def _loop_stub_command(self, tmp, body, name="stub.py"):
        script = Path(tmp) / name
        script.write_text(body, encoding="utf-8")
        return f"python3 {shlex.quote(str(script))}"

    def _counting_stub(self, tmp, per_call_body=""):
        """A stub agent that drains stdin and tracks how many times it ran."""
        counter = Path(tmp) / "calls.txt"
        body = (
            "import os, sys\n"
            f"counter = {str(counter)!r}\n"
            "calls = int(open(counter).read()) if os.path.exists(counter) else 0\n"
            "calls += 1\n"
            "open(counter, 'w').write(str(calls))\n"
            "sys.stdin.read()\n"
            f"{per_call_body}\n"
            "print('iteration output line')\n"
        )
        return self._loop_stub_command(tmp, body), counter

    def _claude_flavor_config(self, tmp, body, name="fakeclaude"):
        command = self._loop_stub_command(tmp, body)
        config = Path(tmp) / "agents.json"
        config.write_text(
            json.dumps({"agents": {name: {"command": shlex.split(command), "kind": "claude"}}}),
            encoding="utf-8",
        )
        return config

    def test_loop_stops_at_max_iter(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, counter = self._counting_stub(tmp)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "3", "--json", "do the work"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["iterations"], 3)
            self.assertEqual(summary["end_reason"], "max-iter")
            self.assertEqual(summary["exit_code"], 0)
            self.assertEqual(counter.read_text(encoding="utf-8"), "3")

    def test_loop_stops_when_state_file_reports_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".yoyo" / "loop-state.md"
            done_body = (
                f"state = {str(state)!r}\n"
                "if calls == 2:\n"
                "    open(state, 'a').write('\\nSTATUS: DONE\\n')\n"
            )
            command, counter = self._counting_stub(tmp, done_body)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "10", "--json", "finish in two"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["iterations"], 2)
            self.assertEqual(summary["end_reason"], "done")
            self.assertEqual(counter.read_text(encoding="utf-8"), "2")

    def test_loop_removes_stale_stop_file_and_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, counter = self._counting_stub(tmp)
            loop_dir = Path(tmp) / ".yoyo"
            loop_dir.mkdir()
            stop = loop_dir / "STOP"
            stop.write_text("", encoding="utf-8")
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "1", "--json", "runs despite leftover STOP"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("removed stale STOP file", stderr)
            self.assertFalse(stop.exists())
            summary = json.loads(stdout)
            self.assertEqual(summary["iterations"], 1)
            self.assertEqual(summary["end_reason"], "max-iter")
            self.assertEqual(counter.read_text(encoding="utf-8"), "1")

    def test_loop_stop_file_created_mid_run_stops_the_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = Path(tmp) / ".yoyo" / "STOP"
            stop_body = (
                f"stop = {str(stop)!r}\n"
                "if calls == 2:\n"
                "    open(stop, 'w').write('')\n"
            )
            command, counter = self._counting_stub(tmp, stop_body)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "10", "--json", "stop midway"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["iterations"], 2)
            self.assertEqual(summary["end_reason"], "stop")
            self.assertEqual(counter.read_text(encoding="utf-8"), "2")

    def test_loop_refuses_state_file_recorded_for_a_different_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, counter = self._counting_stub(tmp)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, _, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "1", "--json", "task one"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(counter.read_text(encoding="utf-8"), "1")

            code, _, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "1", "--json", "task two"],
                env=env,
            )
            self.assertEqual(code, 2)
            self.assertIn("different loop task", stderr)
            self.assertEqual(counter.read_text(encoding="utf-8"), "1")

            # Same task resumes against the same state file without complaint.
            code, _, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "1", "--json", "task one"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(counter.read_text(encoding="utf-8"), "2")

    def test_loop_lock_blocks_concurrent_loop_and_releases_on_exit(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            command, counter = self._counting_stub(tmp)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            lock = Path(tmp) / ".yoyo" / "loop-state.md.lock"
            lock.parent.mkdir()

            # While another holder flocks the lockfile, a second loop is refused.
            holder = os.open(lock, os.O_CREAT | os.O_RDWR)
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                code, _, stderr = self.run_cli(
                    ["loop", "stub", "--cwd", tmp, "--max-iter", "1", "--json", "locked out"],
                    env=env,
                )
                self.assertEqual(code, 2)
                self.assertIn("already running", stderr)
                self.assertFalse(counter.exists())
            finally:
                os.close(holder)

            # Once the holder exits the kernel releases the flock; a leftover
            # lockfile on disk alone is not a lock (no stale-pid reclaim races).
            code, _, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "1", "--json", "locked out"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(counter.read_text(encoding="utf-8"), "1")
            self.assertTrue(lock.exists())

    def test_loop_seeds_state_file_and_does_not_clobber_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, _ = self._counting_stub(tmp)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, _, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "1", "--json", "seed me"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            state = Path(tmp) / ".yoyo" / "loop-state.md"
            seeded = state.read_text(encoding="utf-8")
            self.assertIn("GOAL:\nseed me", seeded)
            self.assertIn("NEXT:\nStart from scratch.", seeded)

            custom = "# my own state\n\nGOAL:\nseed me\n\nNEXT:\ncontinue step 4\n"
            state.write_text(custom, encoding="utf-8")
            code, _, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "1", "--json", "seed me"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(state.read_text(encoding="utf-8"), custom)



    def test_loop_max_fail_aborts_and_success_resets_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            fail_body = "sys.exit(0 if calls == 2 else 1)"
            command, counter = self._counting_stub(tmp, fail_body)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "10", "--max-fail", "3", "--json", "flaky"],
                env=env,
            )

            self.assertEqual(code, 1, stderr)
            summary = json.loads(stdout)
            # fail, ok (resets), fail, fail, fail -> abort after iteration 5
            self.assertEqual(summary["iterations"], 5)
            self.assertEqual(summary["end_reason"], "max-fail")
            self.assertEqual(summary["exit_code"], 1)
            self.assertEqual(counter.read_text(encoding="utf-8"), "5")

    def test_loop_iteration_tail_reports_failure_reason(self):
        long_failure = "p" * 130
        results = [
            {
                "exit_code": 0,
                "duration_s": 1,
                "stdout": "success first\nsuccess tail\n",
                "stderr": "success raw noise",
                "stderr_plain": "success plain noise",
            },
            {
                "exit_code": 1,
                "duration_s": 2,
                "stdout": "misleading failure stdout",
                "stderr": "raw failure noise",
                "stderr_plain": long_failure,
            },
            {
                "exit_code": 1,
                "duration_s": 3,
                "stdout": "another misleading stdout",
                "stderr": "raw failure reason",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            command = self._loop_stub_command(tmp, "import sys\nsys.stdin.read()\n")
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            with mock.patch.object(yoyo, "execute_agent_call", side_effect=results):
                code, stdout, stderr = self.run_cli(
                    ["loop", "stub", "--cwd", tmp, "--max-iter", "3", "--max-fail", "2", "--json", "report failures"],
                    env=env,
                )

        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["end_reason"], "max-fail")
        iteration_lines = [line for line in stderr.splitlines() if " iter " in line]
        self.assertEqual(len(iteration_lines), 3)
        self.assertTrue(iteration_lines[0].endswith("success tail"))
        self.assertTrue(iteration_lines[1].endswith(long_failure[:120]))
        self.assertNotIn(long_failure, iteration_lines[1])
        self.assertTrue(iteration_lines[2].endswith("raw failure reason"))
        self.assertNotIn("misleading failure stdout", stderr)
        self.assertNotIn("another misleading stdout", stderr)

    def test_loop_dry_run_prompt_contains_protocol_state_path_and_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_skill(Path(tmp) / "skills", "frontend")
            code, stdout, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--skill", "frontend", "--dry-run", "Build the page."],
                env={"YOYO_SKILL_PATH": str(Path(tmp) / "skills")},
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("=== LOOP PROTOCOL (yoyo loop) ===", stdout)
            self.assertIn("Loop position: iteration 1 of at most 20.", stdout)
            self.assertIn(str(Path(tmp).resolve() / ".yoyo" / "loop-state.md"), stdout)
            self.assertIn('<skill name="frontend">', stdout)
            self.assertIn("Use 8px spacing", stdout)
            self.assertIn("TASK:\nBuild the page.", stdout)
            self.assertIn("delegated worker", stdout)

    def test_loop_rejects_session(self):
        code, stdout, stderr = self.run_cli(
            ["loop", "claude", "--session", "named", "task"],
        )

        self.assertEqual(code, 2)
        self.assertIn("does not support --session", stderr)

    def test_loop_iterations_share_loop_id_in_run_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, _ = self._counting_stub(tmp)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, _, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "2", "--trace-id", "loop-xyz", "--json", "ledger me"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)

            code, stdout, stderr = self.run_cli(["runs", "list", "--json"], env=env)
            self.assertEqual(code, 0, stderr)
            rows = [row for row in json.loads(stdout) if row["loop_id"] == "loop-xyz"]
            self.assertEqual(len(rows), 2)
            self.assertEqual(sorted(row["iteration"] for row in rows), [1, 2])
            for row in rows:
                self.assertEqual(row["agent"], "stub")
                self.assertEqual(row["status"], "done")
                self.assertEqual(row["exit_code"], 0)

            code, stdout, stderr = self.run_cli(["runs", "list"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("loop-xyz:1", stdout)
            self.assertIn("loop-xyz:2", stdout)

    def test_loop_dry_run_executes_nothing_and_creates_no_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, counter = self._counting_stub(tmp)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--dry-run", "plan only"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("=== LOOP PROTOCOL", stdout)
            self.assertIn("python3", stdout)
            self.assertFalse(counter.exists())
            self.assertFalse((Path(tmp) / ".yoyo").exists())
            self.assertFalse((Path(tmp) / "state" / "runs").exists())


    def test_loop_task_text_requires_task_and_rejects_both_sources(self):
        code, _, stderr = self.run_cli(["loop", "claude"])
        self.assertEqual(code, 2)
        self.assertIn("requires a task", stderr)

        with tempfile.TemporaryDirectory() as tmp:
            task_file = Path(tmp) / "task.md"
            task_file.write_text("from file", encoding="utf-8")
            code, _, stderr = self.run_cli(
                ["loop", "claude", "--input", str(task_file), "also positional"],
            )
            self.assertEqual(code, 2)
            self.assertIn("not both", stderr)

            code, stdout, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--input", str(task_file), "--dry-run"],
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("TASK:\nfrom file", stdout)



    def test_loop_background_records_parent_run_and_iteration_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, _ = self._counting_stub(tmp)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "2", "--trace-id", "bg-loop", "--background", "work"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()
            self.assertRegex(run_id, r"^\d{8}T\d{6}-[0-9a-f]{8}$")

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "10", "--poll", "0.05", "--json"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["end_reason"], "max-iter")
            self.assertEqual(payload["iterations"], 2)
            self.assertEqual(payload["loop_id"], "bg-loop")

            code, stdout, stderr = self.run_cli(["runs", "list", "--json", "--limit", "10"], env=env)
            self.assertEqual(code, 0, stderr)
            children = [row for row in json.loads(stdout) if row["loop_id"] == "bg-loop"]
            self.assertEqual(sorted(row["iteration"] for row in children), [1, 2])

            # Human-mode show renders a loop summary line, not silence.
            code, stdout, stderr = self.run_cli(["runs", "show", run_id], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("loop bg-loop: max-iter after 2 iterations", stdout)

    def _done_each_call_stub(self, tmp, state):
        """A worker stub that claims STATUS: DONE on every iteration."""
        body = (
            "import sys\n"
            f"state = {str(state)!r}\n"
            "sys.stdin.read()\n"
            "open(state, 'a').write('\\nSTATUS: DONE\\n')\n"
            "print('claimed done')\n"
        )
        return self._loop_stub_command(tmp, body)

    def _checker_config(self, tmp, checker_body, name="checkbot"):
        script = Path(tmp) / f"{name}.py"
        script.write_text(checker_body, encoding="utf-8")
        config = Path(tmp) / "checker-agents.json"
        config.write_text(
            json.dumps(
                {"agents": {name: {"command": ["python3", str(script)], "read_only_args": ["--ro"], "full_access_args": []}}}
            ),
            encoding="utf-8",
        )
        return config




    def test_loop_queue_rejects_done_while_items_unchecked(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".yoyo" / "loop-state.md"
            queue = Path(tmp) / "tasks.md"
            queue.write_text("# work\n- [ ] first item\n- [x] already done\n- [ ] second item\n", encoding="utf-8")
            command = self._done_each_call_stub(tmp, state)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "3",
                 "--queue", "tasks.md", "--json", "work the queue"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            # DONE claims never end the loop while boxes stay unchecked.
            self.assertEqual(summary["end_reason"], "max-iter")
            self.assertEqual(summary["queue_rejections"], 3)
            self.assertEqual(Path(summary["queue"]).resolve(), queue.resolve())
            final_state = state.read_text(encoding="utf-8")
            self.assertFalse(any(line.strip() == "STATUS: DONE" for line in final_state.splitlines()))
            self.assertIn("work queue", final_state)
            self.assertIn("first item", final_state)
            self.assertNotIn("already done", final_state)

    def test_loop_queue_accepts_done_when_all_items_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".yoyo" / "loop-state.md"
            queue = Path(tmp) / "tasks.md"
            queue.write_text("- [ ] only item\n", encoding="utf-8")
            done_body = (
                f"state = {str(state)!r}\n"
                f"queue = {str(queue)!r}\n"
                "open(state, 'a').write('\\nSTATUS: DONE\\n')\n"
                "if calls == 2:\n"
                "    open(queue, 'w').write('- [x] only item\\n')\n"
            )
            command, counter = self._counting_stub(tmp, done_body)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "10", "--queue", str(queue), "--json", "work the queue"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["end_reason"], "done")
            self.assertEqual(summary["iterations"], 2)
            self.assertEqual(summary["queue_rejections"], 1)

    def test_loop_queue_block_appears_in_iteration_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp) / "tasks.md"
            queue.write_text("- [ ] rename the module\n- [ ] update the docs\n", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--queue", "tasks.md", "--dry-run", "work the queue"],
                env={},
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("=== WORK QUEUE", stdout)
            self.assertIn("rename the module", stdout)
            self.assertIn("Queue rules:", stdout)
            self.assertIn("STATUS: DONE is only accepted once every queue item is checked.", stdout)
            # Per-iteration content stays after the stable blocks for prefix caching.
            self.assertLess(stdout.index("=== LOOP PROTOCOL"), stdout.index("=== WORK QUEUE"))
            self.assertLess(stdout.index("=== WORK QUEUE"), stdout.index("Loop position:"))

    def test_loop_brief_block_appears_in_stable_prompt_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text("Repo map: everything lives in bin/yoyo. Tests: python3 -m unittest.", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--brief", "brief.md", "--dry-run", "do the work"],
                env={},
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("=== BACKGROUND BRIEF", stdout)
            self.assertIn("everything lives in bin/yoyo", stdout)
            self.assertIn("DO NOT edit", stdout)
            # The brief is stable across iterations, so it sits in the
            # cacheable prefix, before the per-iteration blocks.
            self.assertLess(stdout.index("=== BACKGROUND BRIEF"), stdout.index("=== LOOP PROTOCOL"))
            self.assertLess(stdout.index("=== BACKGROUND BRIEF"), stdout.index("Loop position:"))

    def test_loop_brief_missing_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--brief", "missing.md", "work"],
                env={},
            )
            self.assertEqual(code, 2)
            self.assertIn("--brief file not found", stderr)

    def test_loop_queue_done_fails_closed_when_worker_guts_the_queue(self):
        # A worker that rewrites the queue to prose (or deletes every checklist
        # line) must not be able to slip a DONE past verification.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".yoyo" / "loop-state.md"
            queue = Path(tmp) / "tasks.md"
            queue.write_text("- [ ] real work\n", encoding="utf-8")
            body = (
                f"state = {str(state)!r}\n"
                f"queue = {str(queue)!r}\n"
                "open(queue, 'w').write('all done, nothing left!')\n"
                "open(state, 'a').write('\\nSTATUS: DONE\\n')\n"
            )
            command, counter = self._counting_stub(tmp, body)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "2", "--queue", str(queue), "--json", "work"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["end_reason"], "max-iter")
            self.assertEqual(summary["queue_rejections"], 2)
            self.assertIn("no longer contains any checklist items", state.read_text(encoding="utf-8"))

    def test_parse_queue_items_skips_fences_and_accepts_plus_bullets(self):
        text = (
            "- [ ] real item\n"
            "```\n"
            "- [ ] example inside a fence, not real work\n"
            "```\n"
            "+ [x] plus-bullet item\n"
            "* [ ] star item\n"
        )
        items = yoyo.parse_queue_items(text)
        self.assertEqual(
            items,
            [(False, "real item"), (True, "plus-bullet item"), (False, "star item")],
        )

    def test_spill_fanout_answers_maps_dot_trace_ids_to_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"YOYO_STATE_DIR": tmp}):
                answers_dir = yoyo.spill_fanout_answers("..", [{"agent": "a", "stdout": "x"}])
            self.assertEqual(answers_dir, Path(tmp) / "fanout" / "fanout")
            self.assertTrue((answers_dir / "1-a.md").is_file())


    def test_loop_queue_missing_or_itemless_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--queue", "missing.md", "work"],
                env={},
            )
            self.assertEqual(code, 2)
            self.assertIn("--queue file not found", stderr)

            empty = Path(tmp) / "notes.md"
            empty.write_text("just prose, no checklist\n", encoding="utf-8")
            code, _, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--queue", "notes.md", "work"],
                env={},
            )
            self.assertEqual(code, 2)
            self.assertIn("no checklist items", stderr)







    def test_loop_dry_run_shows_the_immutable_spec_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "VISION.md"
            spec.write_text("Never touch src/payments/.", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--spec", "VISION.md", "--dry-run", "Build the page."],
                env={},
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("=== STANDING SPEC", stdout)
            self.assertIn("Never touch src/payments/.", stdout)
            # Immutable blocks lead so the prompt prefix stays cacheable.
            self.assertLess(stdout.index("=== STANDING SPEC"), stdout.index("=== LOOP PROTOCOL"))

    def test_loop_spec_file_not_found_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--spec", "missing.md", "do it"],
                env={},
            )
            self.assertEqual(code, 2)
            self.assertIn("--spec file not found", stderr)

    def test_ask_raw_sends_prompt_verbatim_with_no_wrapper(self):
        env = {"YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--raw", "/goal ship the release"],
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        self.assertTrue(stdout.startswith("/goal ship the release"), stdout)
        self.assertNotIn("Calling context", stdout)
        self.assertNotIn("Task:", stdout)
        self.assertNotIn("second-opinion", stdout)

    def test_ask_prompt_puts_calling_context_last(self):
        # The per-call-unique trace_id must ride at the prompt tail so the
        # stable prefix (role, skills, task) stays cacheable across calls.
        env = {"YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--role", "review", "--no-stdin", "Audit the module."],
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        lines = [line for line in stdout.strip().splitlines() if line.strip()]
        self.assertTrue(lines[-1].startswith("Calling context:"), lines[-1])
        self.assertIn("Task:\nAudit the module.", stdout)
        self.assertLess(stdout.index("Task:"), stdout.index("Calling context:"))

    def test_ask_raw_rejects_role_and_requires_prompt(self):
        env = {"YOYO_AGENT_ECHO": "cat"}
        code, _, stderr = self.run_cli(
            ["ask", "echo", "--raw", "--role", "review", "/cmd"],
            env=env,
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot be combined with --role", stderr)

        code, _, stderr = self.run_cli(["ask", "echo", "--raw"], env=env)
        self.assertEqual(code, 2)
        self.assertIn("requires positional prompt text", stderr)

    def _review_repo(self, tmp, *, dirty=True):
        import subprocess as sp

        repo = Path(tmp) / "repo"
        repo.mkdir()
        run = lambda *cmd: sp.run(cmd, cwd=repo, check=True, capture_output=True)
        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "test@example.com")
        run("git", "config", "user.name", "Test")
        target = repo / "app.py"
        target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        run("git", "add", "app.py")
        run("git", "commit", "-q", "-m", "init")
        if dirty:
            target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        return repo

    def _review_config(self, tmp, outputs):
        """Configure stub reviewer agents with read_only_args so --read-only works."""
        agents = {}
        for name, text in outputs.items():
            body = f"import sys\nsys.stdin.read()\nprint({text!r})\n"
            command = self._loop_stub_command(tmp, body, name=f"{name}.py")
            agents[name] = {"command": shlex.split(command), "read_only_args": ["--ro"]}
        echo_body = "import sys\nsys.stdout.write(sys.stdin.read())\n"
        echo_command = self._loop_stub_command(tmp, echo_body, name="merge.py")
        agents["merge"] = {"command": shlex.split(echo_command), "read_only_args": ["--ro"]}
        config = Path(tmp) / "review-agents.json"
        config.write_text(json.dumps({"agents": agents}), encoding="utf-8")
        return config

    def test_review_fans_out_and_synthesizes_consensus(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {"r1": "review-one findings", "r2": "review-two findings"})
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1,r2", "--synthesizer", "merge", "--json"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["agents"], ["r1", "r2"])
            self.assertEqual(len(payload["reviews"]), 2)
            self.assertIn("uncommitted changes", payload["scope"])
            # The echo synthesizer reflects its prompt: both reviews and the
            # consensus instructions must be in there.
            self.assertIn('<review agent="r1">', payload["review"])
            self.assertIn("review-one findings", payload["review"])
            self.assertIn("review-two findings", payload["review"])
            self.assertIn("CONSENSUS", payload["review"])
            self.assertIn("return a - b", payload["review"])

    def test_review_single_agent_skips_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {"r1": "solo findings"})
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1", "--json"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIsNone(payload["synthesis"])
            self.assertEqual(payload["review"], "solo findings")

    def test_review_stance_unanimous_swaps_synthesis_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {"r1": "review-one findings", "r2": "review-two findings"})
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1,r2", "--synthesizer", "merge",
                 "--stance", "unanimous", "--json"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            # The echo synthesizer reflects its prompt: the unanimous stance
            # instructions replace the default consensus format.
            self.assertIn("UNANIMOUS stance", payload["review"])
            self.assertIn("NOT UNANIMOUS", payload["review"])
            self.assertNotIn("1. CONSENSUS", payload["review"])
            self.assertIn("review-one findings", payload["review"])

    def test_review_custom_synthesis_prompt_is_verbatim_and_brace_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {"r1": "alpha finding", "r2": "beta finding"})
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1,r2", "--synthesizer", "merge",
                 "--synthesis-prompt", "Emit TOON rows findings[N]{file,claim}: only. {braces} stay literal.",
                 "--json"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIn("Emit TOON rows", payload["review"])
            self.assertIn("{braces} stay literal", payload["review"])
            self.assertNotIn("1. CONSENSUS", payload["review"])
            self.assertIn("alpha finding", payload["review"])

    def test_review_stance_and_synthesis_prompt_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {"r1": "x"})
            code, _, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1", "--stance", "any",
                 "--synthesis-prompt", "custom"],
                env={"YOYO_CONFIG": str(config)},
            )
            self.assertEqual(code, 2)
            self.assertIn("mutually exclusive", stderr)

    def test_review_background_detaches_and_wait_renders_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {"r1": "solo findings"})
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1", "--background"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()
            self.assertRegex(run_id, r"^\d{8}T\d{6}-[0-9a-f]{8}$")

            code, stdout, stderr = self.run_cli(["wait", run_id, "--timeout", "15", "--poll", "0.05"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("solo findings", stdout)

            code, stdout, stderr = self.run_cli(["runs", "show", run_id, "--json"], env=env)
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["status"], "done")
            self.assertEqual(payload["review"], "solo findings")

    def test_review_falls_back_to_raw_reviews_when_synthesis_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {"r1": "alpha finding", "r2": "beta finding"})
            agents = json.loads(Path(config).read_text(encoding="utf-8"))
            fail_command = self._loop_stub_command(tmp, "import sys\nsys.stdin.read()\nsys.exit(3)\n", name="broken.py")
            agents["agents"]["broken"] = {"command": shlex.split(fail_command), "read_only_args": ["--ro"]}
            Path(config).write_text(json.dumps(agents), encoding="utf-8")
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1,r2", "--synthesizer", "broken"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("synthesis via 'broken' failed".replace("'", ""), stderr.replace("'", ""))
            self.assertIn("=== review by r1 ===", stdout)
            self.assertIn("alpha finding", stdout)
            self.assertIn("beta finding", stdout)

    def test_review_continues_when_one_reviewer_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {"r1": "alpha finding"})
            agents = json.loads(Path(config).read_text(encoding="utf-8"))
            fail_command = self._loop_stub_command(tmp, "import sys\nsys.stdin.read()\nsys.exit(3)\n", name="broken.py")
            agents["agents"]["broken"] = {"command": shlex.split(fail_command), "read_only_args": ["--ro"]}
            Path(config).write_text(json.dumps(agents), encoding="utf-8")
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1,broken", "--json"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("broken failed", stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["review"], "alpha finding")
            self.assertIsNone(payload["synthesis"])

    def test_review_all_reviewers_failing_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {})
            agents = json.loads(Path(config).read_text(encoding="utf-8"))
            fail_command = self._loop_stub_command(tmp, "import sys\nsys.stdin.read()\nsys.exit(3)\n", name="broken.py")
            agents["agents"]["broken"] = {"command": shlex.split(fail_command), "read_only_args": ["--ro"]}
            Path(config).write_text(json.dumps(agents), encoding="utf-8")
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "broken"],
                env=env,
            )

            self.assertEqual(code, 1)
            self.assertIn("all reviewers failed", stderr)

    def test_review_clean_tree_uses_base_range_and_empty_range_errors(self):
        import subprocess as sp

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp, dirty=False)
            config = self._review_config(tmp, {"r1": "range findings"})
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}

            code, _, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1", "--base", "main"],
                env=env,
            )
            self.assertEqual(code, 2)
            self.assertIn("Nothing to review", stderr)

            run = lambda *cmd: sp.run(cmd, cwd=repo, check=True, capture_output=True)
            run("git", "checkout", "-q", "-b", "feature")
            (repo / "app.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
            run("git", "commit", "-q", "-am", "tweak")
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1", "--base", "main", "--json"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["scope"], "committed changes (main...HEAD)")
            self.assertEqual(payload["review"], "range findings")

    def test_review_dry_run_prints_commands_without_executing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            config = self._review_config(tmp, {"r1": "never printed"})
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1", "--dry-run"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("r1.py", stdout)
            self.assertIn("--ro", stdout)
            self.assertIn("Review scope: uncommitted changes", stdout)
            self.assertNotIn("never printed", stdout)

    def test_review_lists_untracked_files_in_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            (repo / "brand_new.py").write_text("print('new')\n", encoding="utf-8")
            config = self._review_config(tmp, {"r1": "findings"})
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1", "--dry-run"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("Untracked files NOT included in the diff", stdout)
            self.assertIn("brand_new.py", stdout)
            self.assertIn("untracked files are not in the diff", stderr)

    def test_review_untracked_only_error_hints_at_git_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp, dirty=False)
            (repo / "brand_new.py").write_text("print('new')\n", encoding="utf-8")
            config = self._review_config(tmp, {"r1": "findings"})
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, _, stderr = self.run_cli(
                ["review", "--cwd", str(repo), "--agents", "r1", "--base", "main"],
                env=env,
            )

            self.assertEqual(code, 2)
            self.assertIn("Nothing to review", stderr)
            self.assertIn("untracked files exist", stderr)

    def test_review_rejects_unknown_and_duplicate_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._review_repo(tmp)
            code, _, stderr = self.run_cli(["review", "--cwd", str(repo), "--agents", "nope"])
            self.assertEqual(code, 2)
            self.assertIn("Unknown agent", stderr)

            code, _, stderr = self.run_cli(["review", "--cwd", str(repo), "--agents", "claude,claude"])
            self.assertEqual(code, 2)
            self.assertIn("must not repeat", stderr)

    # --- research ---------------------------------------------------------

    def _research_config(self, tmp, names, *, read_only_args=None, broken=()):
        """Configure stub researcher agents plus an echo 'merge' synthesizer.

        Each named agent prints a name-tagged finding so perspectives are
        distinguishable; the merge agent echoes its stdin so the synthesis
        prompt can be inspected. Names in `broken` exit non-zero.
        """
        agents = {}
        for name in names:
            if name in broken:
                body = "import sys\nsys.stdin.read()\nsys.exit(3)\n"
            else:
                body = f"import sys\nsys.stdin.read()\nprint({('finding from ' + name)!r})\n"
            command = self._loop_stub_command(tmp, body, name=f"{name}.py")
            spec = {"command": shlex.split(command)}
            if read_only_args is not None:
                spec["read_only_args"] = read_only_args
            agents[name] = spec
        echo_body = "import sys\nsys.stdout.write(sys.stdin.read())\n"
        echo_command = self._loop_stub_command(tmp, echo_body, name="merge.py")
        merge_spec = {"command": shlex.split(echo_command)}
        if read_only_args is not None:
            merge_spec["read_only_args"] = read_only_args
        agents["merge"] = merge_spec
        config = Path(tmp) / "research-agents.json"
        config.write_text(json.dumps({"agents": agents}), encoding="utf-8")
        return config

    def test_research_fans_out_lenses_and_synthesizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1", "r2"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1,r2", "--lenses", "proponent,skeptic",
                 "--synthesizer", "merge", "--json", "Should we adopt X?"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["topic"], "Should we adopt X?")
            self.assertEqual(payload["agents"], ["r1", "r2"])
            self.assertEqual(payload["lenses"], ["proponent", "skeptic"])
            self.assertEqual(len(payload["perspectives"]), 2)
            # The echo synthesizer reflects its prompt: both perspectives, tagged
            # with lens AND agent, plus the decision-brief instructions.
            self.assertIn('<perspective lens="proponent" agent="r1">', payload["report"])
            self.assertIn('<perspective lens="skeptic" agent="r2">', payload["report"])
            self.assertIn("finding from r1", payload["report"])
            self.assertIn("finding from r2", payload["report"])
            self.assertIn("CONVERGENCE", payload["report"])
            self.assertIn("TENSION", payload["report"])

    def test_research_single_lens_skips_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--lenses", "proponent", "--json", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIsNone(payload["synthesis"])
            self.assertEqual(payload["report"], "finding from r1")

    def test_research_background_detaches_and_wait_renders_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--lenses", "proponent", "--background", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()
            self.assertRegex(run_id, r"^\d{8}T\d{6}-[0-9a-f]{8}$")

            code, stdout, stderr = self.run_cli(["wait", run_id, "--timeout", "15", "--poll", "0.05"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("finding from r1", stdout)

            code, stdout, stderr = self.run_cli(["runs", "show", run_id, "--json"], env=env)
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["status"], "done")
            self.assertEqual(payload["topic"], "topic")
            self.assertEqual(payload["report"], "finding from r1")

    def test_research_round_robins_agents_across_lenses(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["a", "b"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "a,b", "--lenses", "l1,l2,l3",
                 "--synthesizer", "merge", "--json", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            # 3 lenses over 2 agents -> a, b, a (perspectives keep lens order).
            assignment = [(p["lens"], p["agent"]) for p in payload["perspectives"]]
            self.assertEqual(assignment, [("l1", "a"), ("l2", "b"), ("l3", "a")])

    def test_research_custom_lens_becomes_adhoc_angle(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--lenses", "regulatory", "--dry-run", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("Research lens: REGULATORY", stdout)
            self.assertIn("through the lens of regulatory", stdout)

    def test_research_file_context_embedded_in_every_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            doc = Path(tmp) / "background.md"
            doc.write_text("DISTINCTIVE-CONTEXT-TOKEN\n", encoding="utf-8")
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--lenses", "analyst",
                 "--file", str(doc), "--dry-run", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("Context from the caller:", stdout)
            self.assertIn("DISTINCTIVE-CONTEXT-TOKEN", stdout)

    def test_research_continues_when_one_researcher_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1", "bad"], broken=("bad",))
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1,bad", "--lenses", "proponent,skeptic",
                 "--synthesizer", "merge", "--json", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("via bad failed", stderr)
            payload = json.loads(stdout)
            # Only the surviving perspective reaches synthesis.
            self.assertIn("finding from r1", payload["report"])
            self.assertNotIn('agent="bad"', payload["report"])

    def test_research_all_researchers_failing_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["bad"], broken=("bad",))
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, _, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "bad", "--lenses", "proponent", "topic"],
                env=env,
            )

            self.assertEqual(code, 1)
            self.assertIn("all researchers failed", stderr)

    def test_research_synthesis_failure_falls_back_to_raw_perspectives(self):
        with tempfile.TemporaryDirectory() as tmp:
            # synthesizer 'broken' fails, so the raw per-lens perspectives print.
            config = self._research_config(tmp, ["r1", "r2", "broken"], broken=("broken",))
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1,r2", "--lenses", "proponent,skeptic",
                 "--synthesizer", "broken", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("synthesis via broken failed", stderr.replace("'", ""))
            self.assertIn("=== proponent (via r1) ===", stdout)
            self.assertIn("=== skeptic (via r2) ===", stdout)
            self.assertIn("finding from r1", stdout)

    def test_research_dry_run_executes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--lenses", "proponent", "--dry-run", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("r1.py", stdout)
            self.assertIn("Research lens: PROPONENT", stdout)
            self.assertNotIn("finding from r1", stdout)

    def test_research_read_only_constrains_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"], read_only_args=["--ro"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--lenses", "proponent",
                 "--read-only", "--dry-run", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("--ro", stdout)
            self.assertIn("mode=read-only", stderr)

    def test_research_requires_a_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, _, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--lenses", "proponent"],
                env=env,
            )

            self.assertEqual(code, 2)
            self.assertIn("research needs a topic", stderr)

    def test_research_does_not_probe_unscheduled_pool_agents(self):
        # Fewer lenses than agents -> the trailing agent is never scheduled, so a
        # read-only run must not be rejected because that unused agent lacks
        # read_only_args. 'used' supports read-only; 'unused' does not.
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["used", "unused"])
            spec = json.loads(Path(config).read_text(encoding="utf-8"))
            spec["agents"]["used"]["read_only_args"] = ["--ro"]
            # 'unused' deliberately has no read_only_args.
            Path(config).write_text(json.dumps(spec), encoding="utf-8")
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "used,unused", "--lenses", "proponent",
                 "--read-only", "--json", "topic"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["report"], "finding from used")

    def test_research_warns_on_full_access_file_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"], read_only_args=["--ro"])
            doc = Path(tmp) / "background.md"
            doc.write_text("context\n", encoding="utf-8")
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            # Full-access (default) with --file -> warns.
            code, _, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--lenses", "proponent",
                 "--file", str(doc), "--dry-run", "topic"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("full-access delegation includes --file context", stderr)

            # Read-only with --file -> no warning.
            code, _, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--lenses", "proponent",
                 "--file", str(doc), "--read-only", "--dry-run", "topic"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            self.assertNotIn("full-access delegation includes --file context", stderr)

    def test_research_allows_duplicate_lenses_across_agents(self):
        # Same lens, different vendors = a deliberate best-of-n sample.
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1", "r2"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                [
                    "research", "--cwd", tmp, "--agents", "r1,r2", "--lenses", "analyst,analyst",
                    "--synthesizer", "merge", "--json", "topic",
                ],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual([p["lens"] for p in payload["perspectives"]], ["analyst", "analyst"])
            self.assertEqual([p["agent"] for p in payload["perspectives"]], ["r1", "r2"])

    def test_research_rejects_unknown_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, _, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "nope", "--lenses", "proponent", "topic"],
                env=env,
            )
            self.assertEqual(code, 2)
            self.assertIn("Unknown agent", stderr)

    # --- ask fan-out (best-of-n) ---

    def _fanout_env(self, **extra):
        env = {
            "YOYO_AGENT_A": "python3 -c \"import sys; sys.stdin.read(); print('alpha-answer')\"",
            "YOYO_AGENT_B": "python3 -c \"import sys; sys.stdin.read(); print('beta-answer')\"",
        }
        env.update(extra)
        return env

    def _judge_config(self, tmp):
        """A judge agent that echoes its stdin and supports read-only mode.

        The judge always runs read-only (it consumes untrusted candidate
        text), so it must advertise read_only_args.
        """
        config = Path(tmp) / "judge-agents.json"
        config.write_text(
            json.dumps(
                {
                    "agents": {
                        "j": {
                            "command": ["python3", "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
                            "read_only_args": ["--ro-marker"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return config

    def test_ask_fanout_runs_all_agents_and_prints_sections(self):
        code, stdout, stderr = self.run_cli(["ask", "a,b", "compare this"], env=self._fanout_env())

        self.assertEqual(code, 0, stderr)
        self.assertIn("=== a ===", stdout)
        self.assertIn("alpha-answer", stdout)
        self.assertIn("=== b ===", stdout)
        self.assertIn("beta-answer", stdout)

    def test_ask_fanout_marks_sites_cited_by_more_than_one_agent(self):
        # Fan-out concatenated the answers and left the caller to spot overlap
        # by eye. Co-citation is the one cheap signal only the caller can see.
        env = self._fanout_env(
            YOYO_AGENT_A="python3 -c \"import sys; sys.stdin.read(); print('bug at src/auth.ts:112 and src/solo.ts:5')\"",
            YOYO_AGENT_B="python3 -c \"import sys; sys.stdin.read(); print('also src/auth.ts:112, plus lib/other.py:9')\"",
        )
        code, stdout, stderr = self.run_cli(["ask", "a,b", "compare this"], env=env)

        self.assertEqual(code, 0, stderr)
        section = stdout.split("=== cited by more than one agent")[1]
        self.assertIn("src/auth.ts:112", section)
        self.assertIn("a, b", section)
        self.assertNotIn("src/solo.ts:5", section)
        self.assertNotIn("lib/other.py:9", section)

    def test_ask_fanout_co_cites_an_extensionless_path(self):
        # Real answers cite bin/yoyo, which carries no extension, and one agent
        # spells it absolute where the other spells it relative. A port and a
        # ratio appear in both answers and must not read as a shared site.
        env = self._fanout_env(
            YOYO_AGENT_A="python3 -c \"import sys; sys.stdin.read(); print('bin/yoyo:34 sets it, see http://host:8080, a 2:1 ratio')\"",
            YOYO_AGENT_B="python3 -c \"import sys; sys.stdin.read(); print('[bin/yoyo](/Users/me/code/bin/yoyo:34), http://host:8080, 2:1')\"",
        )
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bin").mkdir()
            (Path(tmp) / "bin" / "yoyo").write_text("\n".join(f"line {n}" for n in range(1, 41)))
            code, stdout, stderr = self.run_cli(
                ["ask", "a,b", "--cwd", tmp, "--json", "compare this"], env=env
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        # The site displays the absolute spelling, which resolves outside the
        # run cwd; the relative spelling of the same site is what disk answers,
        # and read_as names it so the note is not read as coming from the
        # absolute path.
        self.assertEqual(
            payload["co_cited"],
            [{
                "site": "/Users/me/code/bin/yoyo:34",
                "agents": ["a", "b"],
                "disk": {"status": "line", "text": "line 34", "read_as": "bin/yoyo"},
            }],
        )

    def test_co_citation_keeps_two_directories_apart(self):
        # Grouping on the basename alone merged src/auth.py:42 with
        # tests/auth.py:42 and printed one path neither agent cited. A third
        # agent naming the bare file cannot decide which of the two it meant.
        results = [
            {"agent": "a", "stdout": "leak at src/auth.py:42"},
            {"agent": "b", "stdout": "leak at tests/auth.py:42"},
            {"agent": "c", "stdout": "leak at auth.py:42"},
        ]

        self.assertEqual(yoyo.co_cited_sites(results), [])

    def test_co_citation_ignores_a_slash_only_path(self):
        # "/:5" satisfies the citation shape (it has a separator) but names no
        # file. Grouping by its last segment must not fall over on it.
        results = [
            {"agent": "a", "stdout": "look at /:5 and src/auth.py:42"},
            {"agent": "b", "stdout": "look at /:5 and src/auth.py:42"},
        ]

        self.assertEqual(
            yoyo.co_cited_sites(results),
            [{"site": "src/auth.py:42", "agents": ["a", "b"], "spellings": ["src/auth.py"]}],
        )

    def test_co_citation_merges_spellings_of_one_file(self):
        # One file gets cited three ways in the same fan-out. All three name
        # the same site, and the fullest spelling is the useful one to print.
        results = [
            {"agent": "a", "stdout": "./bin/yoyo:34 and lib/auth.py:9"},
            {"agent": "b", "stdout": "bin/yoyo:34 and src/lib/auth.py:9"},
            {"agent": "c", "stdout": "/Users/me/code/bin/yoyo:34"},
        ]

        self.assertEqual(
            yoyo.co_cited_sites(results),
            [
                {
                    "site": "/Users/me/code/bin/yoyo:34",
                    "agents": ["a", "b", "c"],
                    "spellings": ["/Users/me/code/bin/yoyo", "bin/yoyo"],
                },
                {
                    "site": "src/lib/auth.py:9",
                    "agents": ["a", "b"],
                    "spellings": ["src/lib/auth.py", "lib/auth.py"],
                },
            ],
        )

    def test_co_citation_scan_stays_linear_on_a_long_token(self):
        # Agents paste base64 and minified bundles. Restarting either regex on
        # every "+" in one token could stall a fan-out for minutes.
        blob = "aB3+" * 8000
        results = [
            {"agent": "a", "stdout": f"{blob} {blob}:112 src/auth.ts:112"},
            {"agent": "b", "stdout": f"{blob} {blob}:112 src/auth.ts:112"},
        ]

        started = time.perf_counter()
        shared = yoyo.co_cited_sites(results)
        elapsed = time.perf_counter() - started

        self.assertEqual([entry["site"] for entry in shared], ["src/auth.ts:112"])
        self.assertLess(elapsed, 1.0, f"citation scan took {elapsed:.1f}s on one long token")

    def test_co_citation_grouping_stays_linear_on_many_sites(self):
        # A pasted grep dump is thousands of distinct sites. Matching each one
        # against every site seen so far would take minutes on a 2 MB answer.
        dump = "\n".join(f"src/mod{index}/file{index}.py:{index % 900 + 1}: hit" for index in range(20000))
        results = [{"agent": "a", "stdout": dump}, {"agent": "b", "stdout": dump}]

        started = time.perf_counter()
        shared = yoyo.co_cited_sites(results)
        elapsed = time.perf_counter() - started

        self.assertEqual(len(shared), 20000)
        self.assertLess(elapsed, 1.0, f"citation grouping took {elapsed:.1f}s on 20k sites")

    def test_co_citation_grouping_stays_linear_on_one_filename(self):
        # A monorepo greps into thousands of same-named files, so the whole
        # dump lands in one filename+line bucket. Scanning that bucket per
        # candidate is quadratic even though the site count is unchanged.
        dump = "\n".join(f"src/mod{index}/index.ts:42: hit" for index in range(20000))
        results = [{"agent": "a", "stdout": dump}, {"agent": "b", "stdout": dump}]

        started = time.perf_counter()
        shared = yoyo.co_cited_sites(results)
        elapsed = time.perf_counter() - started

        self.assertEqual(len(shared), 20000)
        self.assertLess(elapsed, 1.0, f"citation grouping took {elapsed:.1f}s on one filename")

    def test_ask_fanout_reports_when_no_site_is_shared(self):
        # Silence would read as "not computed"; say the overlap was empty.
        code, stdout, stderr = self.run_cli(["ask", "a,b", "compare this"], env=self._fanout_env())

        self.assertEqual(code, 0, stderr)
        self.assertIn("no file:line site was cited by more than one agent", stdout)

    def test_ask_fanout_json_carries_the_co_citations(self):
        env = self._fanout_env(
            YOYO_AGENT_A="python3 -c \"import sys; sys.stdin.read(); print('src/auth.ts:112 leaks')\"",
            YOYO_AGENT_B="python3 -c \"import sys; sys.stdin.read(); print('confirm src/auth.ts:112')\"",
        )
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = self.run_cli(
                ["ask", "a,b", "--cwd", tmp, "--json", "compare this"], env=env
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(
            payload["co_cited"],
            [{
                "site": "src/auth.ts:112",
                "agents": ["a", "b"],
                "disk": {"status": "missing"},
            }],
        )

    def test_ask_fanout_judge_sees_all_candidates_and_the_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fanout_env(YOYO_CONFIG=str(self._judge_config(tmp)))
            code, stdout, stderr = self.run_cli(
                ["ask", "a,b", "--judge", "j", "--json", "pick the best refactor"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["agents"], ["a", "b"])
            self.assertEqual([r["agent"] for r in payload["results"]], ["a", "b"])
            # The judge consumes untrusted candidate text, so it runs read-only.
            self.assertIn("--ro-marker", payload["judge"]["command"])
            judge_out = payload["judge"]["stdout"]
            self.assertIn('answer agent="a"', judge_out)
            self.assertIn("alpha-answer", judge_out)
            self.assertIn("beta-answer", judge_out)
            self.assertIn("pick the best refactor", judge_out)
            # The default judge instructions lead with the convergence /
            # divergence map — divergence is the caller's verification list.
            self.assertIn("CONVERGENCE", judge_out)
            self.assertIn("DIVERGENCE", judge_out)
            self.assertIn("verification work list", judge_out)
            self.assertLess(judge_out.index("DIVERGENCE"), judge_out.index("VERDICT"))

    def test_ask_fanout_judge_without_read_only_support_fails_loudly(self):
        env = self._fanout_env(
            YOYO_AGENT_J="python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\"",
        )
        code, _, stderr = self.run_cli(["ask", "a,b", "--judge", "j", "task"], env=env)
        self.assertEqual(code, 2)
        self.assertIn("read_only_args", stderr)

    def test_ask_fanout_custom_judge_prompt_is_verbatim_and_brace_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fanout_env(YOYO_CONFIG=str(self._judge_config(tmp)))
            code, stdout, stderr = self.run_cli(
                [
                    "ask", "a,b", "--judge", "j", "--json",
                    "--judge-prompt", "Rank by {rubric} strictly",
                    "task text",
                ],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIn("Rank by {rubric} strictly", payload["judge"]["stdout"])
            self.assertIn("alpha-answer", payload["judge"]["stdout"])

    def test_ask_fanout_judge_prompt_without_judge_fails_loudly(self):
        code, _, stderr = self.run_cli(
            ["ask", "a,b", "--judge-prompt", "Rank strictly", "task"],
            env=self._fanout_env(),
        )
        self.assertEqual(code, 2)
        self.assertIn("--judge-prompt has no effect without --judge", stderr)

    def test_ask_fanout_judge_only_without_judge_fails_loudly(self):
        code, _, stderr = self.run_cli(
            ["ask", "a,b", "--judge-only", "task"],
            env=self._fanout_env(),
        )
        self.assertEqual(code, 2)
        self.assertIn("--judge-only has no effect without --judge", stderr)

    def test_ask_fanout_judge_only_spills_answers_and_returns_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            env = self._fanout_env(
                YOYO_CONFIG=str(self._judge_config(tmp)),
                YOYO_STATE_DIR=str(state),
            )
            code, stdout, stderr = self.run_cli(
                ["ask", "a,b", "--judge", "j", "--judge-only", "--json", "pick one"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            # Raw answers left the envelope and live in files instead.
            self.assertIn("answers_dir", payload)
            for item in payload["results"]:
                self.assertEqual(item["stdout"], "")
                answer_path = Path(item["stdout_file"])
                self.assertTrue(answer_path.is_file(), answer_path)
                self.assertTrue(str(answer_path).startswith(str(state)), answer_path)
            self.assertIn("alpha-answer", Path(payload["results"][0]["stdout_file"]).read_text(encoding="utf-8"))
            # The judge saw the full candidates and its verdict stays inline.
            self.assertIn("alpha-answer", payload["judge"]["stdout"])
            self.assertIn("beta-answer", payload["judge"]["stdout"])

    def test_ask_fanout_judge_only_text_mode_prints_verdict_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fanout_env(
                YOYO_CONFIG=str(self._judge_config(tmp)),
                YOYO_STATE_DIR=str(Path(tmp) / "state"),
            )
            code, stdout, stderr = self.run_cli(
                ["ask", "a,b", "--judge", "j", "--judge-only", "pick one"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("=== judge (j) ===", stdout)
            self.assertIn("=== raw answers (spilled to files) ===", stdout)
            self.assertNotIn("=== a ===", stdout)
            self.assertIn("1-a.md", stdout)
            self.assertIn("2-b.md", stdout)

    def test_ask_fanout_judge_only_falls_back_to_raw_answers_when_judge_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "j": {
                                "command": ["python3", "-c", "import sys; sys.exit(3)"],
                                "read_only_args": ["--ro-marker"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = self._fanout_env(
                YOYO_CONFIG=str(config),
                YOYO_STATE_DIR=str(Path(tmp) / "state"),
            )
            code, stdout, stderr = self.run_cli(
                ["ask", "a,b", "--judge", "j", "--judge-only", "pick one"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("judge via j failed", stderr)
            self.assertIn("=== a ===", stdout)
            self.assertIn("alpha-answer", stdout)
            self.assertIn("beta-answer", stdout)

    def test_ask_fanout_partial_failure_keeps_survivors_and_skips_judge(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fanout_env(
                YOYO_CONFIG=str(self._judge_config(tmp)),
                YOYO_AGENT_B="python3 -c \"import sys; sys.stdin.read(); sys.exit(3)\"",
            )
            code, stdout, stderr = self.run_cli(["ask", "a,b", "--judge", "j", "task"], env=env)

            self.assertEqual(code, 0, stderr)
            self.assertIn("alpha-answer", stdout)
            self.assertNotIn("beta-answer", stdout)
            self.assertIn("b failed", stderr)
            self.assertIn("skipping the judge", stderr)

    def test_ask_fanout_all_failed_exits_nonzero(self):
        env = {
            "YOYO_AGENT_A": "python3 -c \"import sys; sys.stdin.read(); sys.exit(3)\"",
            "YOYO_AGENT_B": "python3 -c \"import sys; sys.stdin.read(); sys.exit(4)\"",
        }
        code, stdout, stderr = self.run_cli(["ask", "a,b", "task"], env=env)

        self.assertEqual(code, 1)
        self.assertIn("all fan-out agents failed", stderr)

    def test_ask_fanout_background_wait_prints_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fanout_env(YOYO_STATE_DIR=tmp)
            code, stdout, stderr = self.run_cli(["ask", "a,b", "--background", "task"], env=env)
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "5", "--poll", "0.01"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("=== a ===", stdout)
            self.assertIn("alpha-answer", stdout)
            self.assertIn("beta-answer", stdout)
            self.assertIn("no file:line site was cited by more than one agent", stdout)

    def test_ask_fanout_background_wait_prints_co_citations(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fanout_env(
                YOYO_STATE_DIR=tmp,
                YOYO_AGENT_A="python3 -c \"import sys; sys.stdin.read(); print('alpha src/auth.ts:112')\"",
                YOYO_AGENT_B="python3 -c \"import sys; sys.stdin.read(); print('beta src/auth.ts:112')\"",
            )
            code, stdout, stderr = self.run_cli(["ask", "a,b", "--background", "task"], env=env)
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "5", "--poll", "0.01"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn(yoyo.CO_CITED_HEADER, stdout)
            self.assertIn("src/auth.ts:112 — a, b", stdout)

    def test_ask_fanout_background_one_answer_omits_co_citations(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._fanout_env(
                YOYO_STATE_DIR=tmp,
                YOYO_AGENT_B="python3 -c \"import sys; sys.stdin.read(); sys.exit(3)\"",
            )
            code, stdout, stderr = self.run_cli(["ask", "a,b", "--background", "task"], env=env)
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "5", "--poll", "0.01"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("alpha-answer", stdout)
            self.assertNotIn(yoyo.CO_CITED_HEADER, stdout)

    def test_stored_fanout_without_co_citations_omits_block(self):
        result = {
            "results": [
                {"agent": "a", "exit_code": 0, "stdout": "alpha-answer"},
                {"agent": "b", "exit_code": 0, "stdout": "beta-answer"},
            ],
            "judge": None,
        }
        stdout = io.StringIO()

        with mock.patch("sys.stdout", stdout):
            yoyo.emit_result(result, as_json=False)

        self.assertNotIn(yoyo.CO_CITED_HEADER, stdout.getvalue())

    def test_ask_fanout_rejects_session(self):
        code, _, stderr = self.run_cli(
            ["ask", "a,b", "--session", "s1", "task"],
            env=self._fanout_env(),
        )
        self.assertEqual(code, 2)
        self.assertIn("fan-out calls are one-shot", stderr)

    def test_ask_judge_requires_a_fanout(self):
        code, _, stderr = self.run_cli(["ask", "a", "--judge", "b", "task"], env=self._fanout_env())
        self.assertEqual(code, 2)
        self.assertIn("need a fan-out", stderr)

        code, _, stderr = self.run_cli(["ask", "a", "--judge-prompt", "x", "task"], env=self._fanout_env())
        self.assertEqual(code, 2)
        self.assertIn("need a fan-out", stderr)

    def test_ask_fanout_unknown_agent_fails_loudly(self):
        code, _, stderr = self.run_cli(["ask", "a,nope", "task"], env=self._fanout_env())
        self.assertEqual(code, 2)
        self.assertIn("Unknown agent(s): nope", stderr)

    # --- research flexibility ---

    def test_research_free_text_lens_is_used_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                [
                    "research", "--cwd", tmp, "--agents", "r1",
                    "--lens", "Investigate GDPR fines, focusing on 2024 rulings",
                    "--dry-run", "--json", "topic",
                ],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIn("Research lens: Investigate GDPR fines, focusing on 2024 rulings", payload["prompt"])
            # Free-text lenses replace the defaults; nothing canned sneaks in.
            self.assertNotIn("PROPONENT", payload["prompt"])

    def test_research_no_synthesis_returns_raw_perspectives(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1", "r2"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                [
                    "research", "--cwd", tmp, "--agents", "r1,r2",
                    "--lenses", "proponent,skeptic", "--no-synthesis", "--json", "topic",
                ],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIsNone(payload["synthesis"])
            self.assertIn("=== proponent (via r1) ===", payload["report"])
            self.assertIn("finding from r2", payload["report"])

    def test_research_no_synthesis_conflicts_with_synthesizer(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, _, stderr = self.run_cli(
                ["research", "--cwd", tmp, "--agents", "r1", "--no-synthesis", "--synthesizer", "merge", "topic"],
                env=env,
            )
            self.assertEqual(code, 2)
            self.assertIn("mutually exclusive", stderr)

    def test_research_custom_synthesis_prompt_is_verbatim_and_brace_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1", "r2"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                [
                    "research", "--cwd", tmp, "--agents", "r1,r2", "--lenses", "proponent,skeptic",
                    "--synthesizer", "merge",
                    "--synthesis-prompt", "Rank the findings by {impact} only",
                    "--json", "topic",
                ],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIn("Rank the findings by {impact} only", payload["synthesis"]["stdout"])
            self.assertIn("finding from r1", payload["synthesis"]["stdout"])
            self.assertNotIn("CONVERGENCE", payload["synthesis"]["stdout"])

    # --- loop agent rotation ---

    def test_loop_rotates_agents_across_iterations(self):
        with tempfile.TemporaryDirectory() as tmp:
            order = Path(tmp) / "order.txt"
            body_template = (
                "import sys\n"
                "sys.stdin.read()\n"
                f"open({str(order)!r}, 'a').write('{{name}}\\n')\n"
                "print('ok')\n"
            )
            env = {
                "YOYO_STATE_DIR": str(Path(tmp) / "state"),
                "YOYO_AGENT_WA": self._loop_stub_command(tmp, body_template.format(name="wa"), name="wa.py"),
                "YOYO_AGENT_WB": self._loop_stub_command(tmp, body_template.format(name="wb"), name="wb.py"),
            }
            code, stdout, stderr = self.run_cli(
                ["loop", "wa,wb", "--cwd", tmp, "--max-iter", "3", "--json", "rotate work"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertEqual(order.read_text().split(), ["wa", "wb", "wa"])
            summary = json.loads(stdout)
            self.assertEqual(summary["iterations"], 3)
            self.assertEqual(summary["agent"], "wa,wb")

    def test_loop_unknown_rotation_agent_fails_loudly(self):
        code, _, stderr = self.run_cli(
            ["loop", "wa,nope", "task"],
            env={"YOYO_AGENT_WA": "python3 -c \"import sys; sys.stdin.read()\""},
        )
        self.assertEqual(code, 2)
        self.assertIn("Unknown agent 'nope'", stderr)

    # --- cron ---

    def _cron_env(self, tmp):
        bindir = Path(tmp) / "bin"
        bindir.mkdir(exist_ok=True)
        store = Path(tmp) / "crontab-store.txt"
        stub = bindir / "crontab"
        stub.write_text(
            "#!/bin/sh\n"
            f'STORE="{store}"\n'
            'if [ "$1" = "-l" ]; then\n'
            '  if [ -f "$STORE" ]; then cat "$STORE"; else echo "no crontab for user" >&2; exit 1; fi\n'
            "else\n"
            '  cat > "$STORE"\n'
            "fi\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = {
            "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
            "YOYO_STATE_DIR": str(Path(tmp) / "state"),
        }
        return env, store










    def test_research_multiword_lenses_item_keeps_adhoc_scaffold(self):
        # Provenance matters: --lenses items (even multi-word) get the ad-hoc
        # scaffold; only explicit --lens text is used verbatim.
        with tempfile.TemporaryDirectory() as tmp:
            config = self._research_config(tmp, ["r1"])
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                [
                    "research", "--cwd", tmp, "--agents", "r1",
                    "--lenses", "developer experience", "--dry-run", "--json", "topic",
                ],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIn("through the lens of developer experience", payload["prompt"])


class TimeoutHelpGuardTests(CliTestCase):
    """`--help` is the surface a calling agent actually reads.

    145 of the 186 killed runs in the local ledger died on a hard --timeout the
    caller had typed, and none on the default. An agent that sees a bare
    "Per-agent timeout in seconds" supplies a number; one that is told the flag
    kills the task does not. These anchors pin that warning to each subcommand
    where a wrong value ends the run, and pin `wait --timeout` as the safe one.
    """

    def flag_help(self, subcommand: str, flag: str) -> str:
        parser = yoyo.build_parser()
        subparsers = parser._subparsers._group_actions[0]
        for action in subparsers.choices[subcommand]._actions:
            if flag in action.option_strings:
                return action.help or ""
        raise AssertionError(f"{subcommand} has no {flag}")

    def test_task_timeouts_warn_that_they_kill_the_run(self):
        for subcommand in ("ask", "review", "research", "loop"):
            with self.subTest(subcommand=subcommand):
                self.assertIn("kill", self.flag_help(subcommand, "--timeout").lower())

    def test_task_timeout_help_quotes_env_default(self):
        # The quoted number must be the one argparse will apply. A caller who
        # exports YOYO_TIMEOUT and then reads "default 14400s" is being told a
        # budget that is not theirs, on the flag that kills the most runs.
        with mock.patch.dict(os.environ, {"YOYO_TIMEOUT": "300"}):
            for subcommand in ("ask", "review", "research", "loop"):
                with self.subTest(subcommand=subcommand):
                    help_text = self.flag_help(subcommand, "--timeout")
                    self.assertIn("default 300s", help_text)
                    self.assertNotIn(str(yoyo.DEFAULT_TIMEOUT_SECONDS), help_text)

    def test_task_timeout_help_quotes_builtin_default(self):
        # With no override the help still names the built-in budget.
        without_override = {key: value for key, value in os.environ.items() if key != "YOYO_TIMEOUT"}
        with mock.patch.dict(os.environ, without_override, clear=True):
            for subcommand in ("ask", "review", "research", "loop"):
                with self.subTest(subcommand=subcommand):
                    help_text = self.flag_help(subcommand, "--timeout")
                    self.assertIn(f"default {yoyo.DEFAULT_TIMEOUT_SECONDS}s", help_text)

    def test_wait_timeout_is_marked_safe_to_repeat(self):
        self.assertIn("poll", self.flag_help("wait", "--timeout").lower())

    def test_idle_timeout_does_not_recommend_a_task_budget(self):
        self.assertNotIn("use --timeout", self.flag_help("ask", "--idle-timeout").lower())


class SkillGuardTests(CliTestCase):
    """The SKILL.md files are yoyo's interface to calling agents.

    A load-bearing section silently dropped in a rewrite broke codex's
    calling pattern for a month (v0.9.1 removed the caller-budget guidance).
    These anchors pin the *contracts*, not the phrasing: a rewrite may
    restructure freely but must keep teaching each of these.
    """

    SKILL_PATH = ROOT / "skills" / "yoyo" / "SKILL.md"
    # Anchors chosen from a 60-day audit of the run ledger: these are the lines
    # whose absence showed up as real failures (1,088 foreground calls, 5.8% of
    # them killed by a caller's tool timeout; --background used zero times).
    LOAD_BEARING_ANCHORS = {
        "background is taught as the default call shape": "--background",
        "the poll recipe agents copy verbatim": "yoyo wait",
        "exit 124 means still-running, keep waiting": "124",
        "raising the caller's own timeout is named as the wrong move": "Raising your own tool timeout",
        "a cut-off review is unavailable, never passed": "**unavailable**",
        "a lost run can be reconstructed instead of guessed at": "yoyo runs autopsy",
        "reviews and untrusted input run read-only": "--read-only",
        "the verifier is a different vendor than the author": "different vendor than the one that wrote the code",
        "cursor-via-grok is not an independent sample": "not independent of native Grok",
        "disagreement is what to work on next": "disagreement is your work list",
        "model ids come from the live CLI, not memory": "cursor-agent --list-models",
        "complex work keeps the target cli default": "Default-first quality rule",
        "quality is never traded for efficiency": "Never trade correctness or completeness",
        "fast variants are not called cost-efficient": "lower latency, not lower cost",
        "cursor read-only is plan mode, not a sandbox": "plan mode, not an OS sandbox",
        "agy read-only dies on a denied tool, and yoyo says so": "a denied call ends the run with no answer",
        "grok read-only is a tool allowlist without a shell": "tool allowlist with no shell",
        "agy prompts travel in argv": "prompt travels in argv",
        "delegated output is confirmed only after the caller confirms it": "only after you confirmed it",
        "irreversible work needs the human to ask": "only when the human asked",
        "loop DONE is self-declared and needs a diff read": "Read the diff before you believe it",
        "a caller-typed timeout is the dominant kill, not the default": "not one died on the default",
        "a timeout is a kill rather than a persistence budget": "a kill, not a budget",
        "--background does not exempt the child from that timeout": "inherits the `--timeout`",
        "a loop bound covers one iteration, set far above a normal one": "bounds a single iteration",
    }

    def test_skill_keeps_load_bearing_sections(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        missing = [why for why, anchor in self.LOAD_BEARING_ANCHORS.items() if anchor not in text]
        self.assertEqual(missing, [], f"SKILL.md lost load-bearing content: {missing}")

    def test_bundled_skill_descriptions_have_no_plain_scalar_colon_space(self):
        # Narrow regression guard (no YAML parser in the stdlib): harnesses
        # parse SKILL.md frontmatter as YAML, and a colon-space inside a plain
        # (unquoted) scalar — yoyo-watch once had "video: YouTube/..." in its
        # description — makes the whole document fail to parse. Quoted
        # descriptions may contain anything.
        for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
            front = skill_md.read_text(encoding="utf-8").split("---")[1]
            for line in front.splitlines():
                if line.startswith("description: "):
                    value = line[len("description: "):]
                    if value.startswith('"'):
                        continue  # quoted scalars may contain anything
                    self.assertNotIn(": ", value, f"{skill_md}: unquoted description contains a colon-space")

    def test_doctor_reports_resolved_default_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            personal_root = Path(tmp) / "personal-skills"
            foreign = personal_root / "fable-mode" / "SKILL.md"
            foreign.parent.mkdir(parents=True)
            foreign.write_text("# Personal harness\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(home / ".pi/agent"),
                "YOYO_DEFAULT_SKILLS": "yoyo-fable-mode,fable-mode",
                "YOYO_SKILL_PATH": str(personal_root),
                "YOYO_CONFIG": str(Path(tmp) / "missing.json"),
            }

            code, stdout, stderr = self.run_cli(["doctor"], env=env)

        bundled = ROOT / "skills" / "yoyo-fable-mode" / "SKILL.md"
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"default skill yoyo-fable-mode: bundled ({bundled})", stdout)
        self.assertIn(f"default skill fable-mode: OUTSIDE YOYO BUNDLE ({foreign})", stdout)

    def test_doctor_flags_out_of_sync_skill_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            source = Path(tmp) / "source"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            stale = home / ".codex" / "skills" / "yoyo"
            stale.mkdir(parents=True)
            (stale / "SKILL.md").write_text("# stale copy\n", encoding="utf-8")
            fresh = home / ".claude" / "skills" / "yoyo"
            fresh.mkdir(parents=True)
            (fresh / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(home / ".pi/agent"),
                "YOYO_SKILL_SOURCE": str(source),
                "YOYO_STATE_DIR": tmp,
                "YOYO_CONFIG": str(Path(tmp) / "missing.json"),
            }

            code, stdout, stderr = self.run_cli(["doctor"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("skill yoyo: OUT OF SYNC", stdout)
            self.assertIn(str(stale / "SKILL.md"), stdout)

            code, stdout, _ = self.run_cli(["doctor", "--strict"], env=env)
            self.assertEqual(code, 1, "drifted skill copies must fail doctor --strict")

    def test_doctor_reports_in_sync_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            source = Path(tmp) / "source"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            target = home / ".claude" / "skills" / "yoyo"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(home / ".pi/agent"),
                "YOYO_SKILL_SOURCE": str(source),
                "YOYO_STATE_DIR": tmp,
                "YOYO_CONFIG": str(Path(tmp) / "missing.json"),
            }

            code, stdout, stderr = self.run_cli(["doctor", "--strict"], env=env)
            self.assertEqual(code, 0, stderr + stdout)
            self.assertIn("skill yoyo: in sync (1 homes)", stdout)

    def test_doctor_flags_legacy_pi_home_skill_copy(self):
        # Pi reads ~/.agents/skills natively, so a bundled copy in pi's own
        # skills dir collides with the shared one; doctor names it and
        # install-skill removes it.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            source = Path(tmp) / "source"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            pi_dir = Path(tmp) / "pi"
            legacy = pi_dir / "skills" / "yoyo"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            shared = home / ".agents" / "skills" / "yoyo"
            shared.mkdir(parents=True)
            (shared / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(pi_dir),
                "YOYO_SKILL_SOURCE": str(source),
                "YOYO_STATE_DIR": tmp,
                "YOYO_CONFIG": str(Path(tmp) / "missing.json"),
            }

            code, stdout, stderr = self.run_cli(["doctor"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("skill yoyo: LEGACY", stdout)
            self.assertIn(str(legacy), stdout)
            self.assertNotIn("in sync", stdout, "the colliding legacy copy must not read as a healthy home")

            code, _, _ = self.run_cli(["doctor", "--strict"], env=env)
            self.assertEqual(code, 1, "a legacy pi-home skill copy must fail doctor --strict")

    def test_doctor_reports_an_orphaned_skill_copy(self):
        # Renaming a bundled skill leaves the old directory behind in every
        # agent home, where it keeps being resolved: that is exactly how the
        # pre-rename fable-mode kept shadowing yoyo-fable-mode.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            source = Path(tmp) / "source"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            target = home / ".claude" / "skills" / "yoyo"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            orphan = home / ".claude" / "skills" / "yoyo-gone"
            orphan.mkdir(parents=True)
            (orphan / "SKILL.md").write_text("# left behind\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(home / ".pi/agent"),
                "YOYO_SKILL_SOURCE": str(source),
                "YOYO_STATE_DIR": tmp,
                "YOYO_CONFIG": str(Path(tmp) / "missing.json"),
            }

            code, stdout, stderr = self.run_cli(["doctor"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("skill yoyo-gone: ORPHANED", stdout)
            self.assertIn(str(orphan), stdout)

            code, _, _ = self.run_cli(["doctor", "--strict"], env=env)
            self.assertEqual(code, 1, "an orphaned skill copy must fail doctor --strict")

    def test_doctor_ignores_a_foreign_skill_directory(self):
        # Only yoyo's own skills are yoyo's to report on; a personal skill
        # sharing an agent home is none of its business.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            source = Path(tmp) / "source"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            target = home / ".claude" / "skills" / "yoyo"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            foreign = home / ".claude" / "skills" / "no-ai-slop"
            foreign.mkdir(parents=True)
            (foreign / "SKILL.md").write_text("# mine\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(home / ".pi/agent"),
                "YOYO_SKILL_SOURCE": str(source),
                "YOYO_STATE_DIR": tmp,
                "YOYO_CONFIG": str(Path(tmp) / "missing.json"),
            }

            code, stdout, stderr = self.run_cli(["doctor", "--strict"], env=env)
            self.assertEqual(code, 0, stderr + stdout)
            self.assertNotIn("no-ai-slop", stdout)


class CallJournalTests(CliTestCase):
    """Every foreground agent call must be reconstructable after the fact."""

    ECHO_AGENT = {"YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdin.read(); print('hi')\""}

    def _runs(self, state_dir):
        root = Path(state_dir) / "runs"
        return sorted([p for p in root.iterdir() if p.is_dir()]) if root.is_dir() else []

    def test_foreground_call_is_journaled(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(self.ECHO_AGENT, YOYO_STATE_DIR=tmp)
            code, stdout, stderr = self.run_cli(["ask", "echo", "ping"], env=env)
            self.assertEqual(code, 0, stderr)

            runs = self._runs(tmp)
            self.assertEqual(len(runs), 1)
            meta = json.loads((runs[0] / "meta.json").read_text())
            self.assertEqual(meta["mode"], "call")
            self.assertEqual(meta["agent"], "echo")
            result = json.loads((runs[0] / "result.json").read_text())
            self.assertEqual(result["exit_code"], 0)
            self.assertGreater(result["stdout_bytes"], 0)
            self.assertTrue((runs[0] / "stdout.txt").exists())

    def test_journal_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(self.ECHO_AGENT, YOYO_STATE_DIR=tmp, YOYO_NO_CALL_JOURNAL="1")
            code, _, stderr = self.run_cli(["ask", "echo", "ping"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(self._runs(tmp), [])

    def test_dry_run_is_not_journaled(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(self.ECHO_AGENT, YOYO_STATE_DIR=tmp)
            code, _, stderr = self.run_cli(["ask", "echo", "--dry-run", "ping"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(self._runs(tmp), [])


class AutopsyTests(CliTestCase):
    def _make_run(self, state_dir, run_id, meta, result=None, files=None):
        run_dir = Path(state_dir) / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        if result is not None:
            (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
        for name, content in (files or {}).items():
            (run_dir / name).write_text(content, encoding="utf-8")
        return run_dir

    def test_autopsy_completed_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdin.read(); print('hi')\"", "YOYO_STATE_DIR": tmp}
            code, _, stderr = self.run_cli(["ask", "echo", "ping"], env=env)
            self.assertEqual(code, 0, stderr)

            code, stdout, stderr = self.run_cli(["runs", "autopsy"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertIn("completed successfully", stdout)

    def test_autopsy_explains_hard_killed_claude_call(self):
        # The exact scenario that caused repeated misdiagnosis: a caller's
        # exec-tool timeout SIGKILLs yoyo mid-claude-call; claude had written
        # nothing to stdout (it buffers); the caller concludes "timed out,
        # no output". The autopsy must say what actually happened.
        with tempfile.TemporaryDirectory() as tmp:
            self._make_run(
                tmp,
                "20260711T120000-deadbeef",
                {
                    "run_id": "20260711T120000-deadbeef",
                    "mode": "call",
                    "agent": "claude",
                    "pid": 99999999,
                    "started_at": "2026-07-11T12:00:00Z",
                },
                files={"stdout.txt": "", "stderr.txt": "yoyo: still running, 20s elapsed\n"},
            )

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "--json"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIn("died without recording a result", payload["verdict"])
            self.assertTrue(any("buffers all stdout" in note for note in payload["notes"]))
            self.assertTrue(any("--background" in line for line in payload["advice"]))

    def test_autopsy_reports_signal_kill(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_run(
                tmp,
                "20260711T120100-cafecafe",
                {"run_id": "20260711T120100-cafecafe", "mode": "call", "agent": "codex", "pid": 99999999, "started_at": "2026-07-11T12:01:00Z"},
                result={"exit_code": None, "killed_by_signal": 15, "duration_s": 43.2},
            )

            code, stdout, stderr = self.run_cli(["runs", "autopsy"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertIn("killed by SIGTERM after 43.2s", stdout)

    def test_autopsy_names_hard_timeout_clock(self):
        # A background parent stores the child's raw payload, which carries no
        # timed_out flag, so the autopsy called yoyo's own --timeout kill "a
        # real agent failure". The clock and its value are in the stderr suffix.
        with tempfile.TemporaryDirectory() as tmp:
            self._make_run(
                tmp, "20260711T130000-00000003",
                {"run_id": "20260711T130000-00000003", "agent": "claude", "pid": 99999999, "started_at": "2026-07-11T13:00:00Z"},
                result={
                    "agent": "claude",
                    "exit_code": 124,
                    "duration_s": 600.029,
                    "stdout": "",
                    "stderr": "warming up\n\nTimed out after 600.0s",
                },
            )

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260711T130000-00000003"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertIn("--timeout (600.0s)", stdout)
            self.assertNotIn("a real agent failure", stdout)

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260711T130000-00000003", "--json"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["timeout_clock"], "--timeout")
            self.assertEqual(payload["timeout_clock_s"], 600.0)

    def test_autopsy_names_idle_timeout_clock(self):
        # Same record shape, opposite diagnosis: the stream stalled, so a bigger
        # --timeout would not have helped.
        with tempfile.TemporaryDirectory() as tmp:
            self._make_run(
                tmp, "20260711T130100-00000004",
                {"run_id": "20260711T130100-00000004", "agent": "claude", "pid": 99999999, "started_at": "2026-07-11T13:01:00Z"},
                result={
                    "agent": "claude",
                    "exit_code": 124,
                    "duration_s": 181.039,
                    "stdout": "",
                    "stderr": "warming up\n\nIdle timeout after 180.0s with no output (192 bytes captured before the stall)",
                },
            )

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260711T130100-00000004"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertIn("no new output for 180.0s", stdout)
            self.assertNotIn("a real agent failure", stdout)

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260711T130100-00000004", "--json"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["timeout_clock"], "--idle-timeout")
            self.assertEqual(payload["timeout_clock_s"], 180.0)

    def test_autopsy_reports_journal_clock_fields(self):
        # A foreground call records the clock as flags rather than a suffix; the
        # json form must name it there too.
        with tempfile.TemporaryDirectory() as tmp:
            self._make_run(
                tmp, "20260711T130200-00000005",
                {"run_id": "20260711T130200-00000005", "mode": "call", "agent": "codex", "pid": 99999999, "started_at": "2026-07-11T13:02:00Z"},
                result={"exit_code": 124, "duration_s": 90.1, "idle_timed_out": True, "idle_seconds": 90.0, "stdout_bytes": 0, "stderr_bytes": 12},
            )

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260711T130200-00000005", "--json"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["timeout_clock"], "--idle-timeout")
            self.assertEqual(payload["timeout_clock_s"], 90.0)

    def test_autopsy_clock_stays_absent_for_other_ends(self):
        # An agent that exits 124 itself fired neither yoyo clock, and a run
        # that ended any other way keeps the shape it had before.
        with tempfile.TemporaryDirectory() as tmp:
            self._make_run(
                tmp, "20260711T130300-00000006",
                {"run_id": "20260711T130300-00000006", "agent": "codex", "pid": 99999999, "started_at": "2026-07-11T13:03:00Z"},
                result={"agent": "codex", "exit_code": 124, "duration_s": 5.0, "stdout": "", "stderr": "the agent chose 124"},
            )
            self._make_run(
                tmp, "20260711T130400-00000007",
                {"run_id": "20260711T130400-00000007", "mode": "call", "agent": "codex", "pid": 99999999, "started_at": "2026-07-11T13:04:00Z"},
                result={"exit_code": 0, "duration_s": 1.0, "stdout_bytes": 5, "stderr_bytes": 0},
            )

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260711T130300-00000006", "--json"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertIsNone(payload["timeout_clock"])
            self.assertIsNone(payload["timeout_clock_s"])
            self.assertIn("a real agent failure", payload["verdict"])

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260711T130400-00000007", "--json"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertNotIn("timeout_clock", json.loads(stdout))

    def test_autopsy_picks_latest_run_and_accepts_explicit_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_run(
                tmp, "20260711T110000-00000001",
                {"run_id": "20260711T110000-00000001", "mode": "call", "agent": "codex", "pid": 99999999, "started_at": "2026-07-11T11:00:00Z"},
                result={"exit_code": 3, "duration_s": 1.0, "stdout_bytes": 5, "stderr_bytes": 0},
            )
            self._make_run(
                tmp, "20260711T115900-00000002",
                {"run_id": "20260711T115900-00000002", "mode": "call", "agent": "pi", "pid": 99999999, "started_at": "2026-07-11T11:59:00Z"},
                result={"exit_code": 0, "duration_s": 2.0, "stdout_bytes": 9, "stderr_bytes": 0},
            )

            code, stdout, _ = self.run_cli(["runs", "autopsy"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0)
            self.assertIn("agent:   pi", stdout)

            code, stdout, _ = self.run_cli(["runs", "autopsy", "20260711T110000-00000001", "--json"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0)
            self.assertIn("exit code 3", json.loads(stdout)["verdict"])

    def test_asked_line_skips_task_headings_inside_skills(self):
        # Skill documents are injected verbatim ahead of the task, and one that
        # carries its own "Task:" line was reported as the question.
        with tempfile.TemporaryDirectory() as tmp:
            prompt = (
                "You are being called as an independent second-opinion agent.\n\n"
                "Skill guidance: follow the practices in these skill documents.\n\n"
                "<skill name=\"house\">\nBefore editing:\n\nTask:\n"
                "restate the ask in one line.\n</skill>\n\n"
                "Task:\nwhy does the retry loop double-charge\n\n"
                "Calling context: cwd=/tmp; mode=full-access delegation; caller=x; trace_id=t1.\n"
            )
            self._make_run(
                tmp, "20260712T100000-00000011",
                {"run_id": "20260712T100000-00000011", "mode": "call", "agent": "codex", "pid": 99999999, "started_at": "2026-07-12T10:00:00Z"},
                result={"exit_code": 0, "duration_s": 1.0, "stdout_bytes": 5, "stderr_bytes": 0},
                files={"prompt.txt": prompt},
            )

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260712T100000-00000011"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertIn("asked:   why does the retry loop double-charge", stdout)
            self.assertNotIn("restate the ask in one line", stdout)

    def test_asked_line_absent_when_task_is_past_the_head(self):
        # A skill larger than the retained 64 KB head pushes the task heading
        # out of prompt.txt entirely. Reporting the skill text as the question
        # is worse than reporting nothing.
        with tempfile.TemporaryDirectory() as tmp:
            head = (
                "You are being called as an independent second-opinion agent.\n\n"
                "<skill name=\"spec\">\nA line of the standing spec.\n" * 4
            )
            self._make_run(
                tmp, "20260712T100100-00000012",
                {"run_id": "20260712T100100-00000012", "mode": "call", "agent": "codex", "pid": 99999999, "started_at": "2026-07-12T10:01:00Z"},
                result={"exit_code": 0, "duration_s": 1.0, "stdout_bytes": 5, "stderr_bytes": 0},
                files={"prompt.txt": head + "\n[prompt truncated by yoyo at 65536 of 92621 bytes]"},
            )

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260712T100100-00000012", "--json"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertIsNone(json.loads(stdout)["asked"])

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260712T100100-00000012"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertNotIn("asked:", stdout)

    def test_asked_line_reports_a_prompt_with_no_skills(self):
        # --raw and background parents store a prompt with no skill block and
        # no task heading; the whole text is the question and must survive.
        with tempfile.TemporaryDirectory() as tmp:
            self._make_run(
                tmp, "20260712T100200-00000013",
                {"run_id": "20260712T100200-00000013", "mode": "call", "agent": "codex", "pid": 99999999, "started_at": "2026-07-12T10:02:00Z"},
                result={"exit_code": 0, "duration_s": 1.0, "stdout_bytes": 5, "stderr_bytes": 0},
                files={"prompt.txt": "/review the checkout diff\n"},
            )

            code, stdout, stderr = self.run_cli(["runs", "autopsy", "20260712T100200-00000013"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertIn("asked:   /review the checkout diff", stdout)


class BufferedAgentGuardTests(CliTestCase):
    """--idle-timeout is not a hang guard for agents that buffer their output."""

    def test_buffering_agents_are_marked(self):
        # Measured first-byte behavior: claude emits a little stderr then goes
        # silent for the whole run, and pi emits nothing until it exits. codex,
        # grok and cursor stream, so their silence really is a stall. cursor
        # only qualifies on stream-json — its text mode buffers to exit, which
        # is why the spec no longer asks for text.
        agents = yoyo.DEFAULT_AGENTS
        self.assertTrue(agents["claude"].buffers_output)
        self.assertTrue(agents["pi"].buffers_output)
        self.assertTrue(agents["agy"].buffers_output)
        self.assertFalse(agents["codex"].buffers_output)
        self.assertFalse(agents["grok"].buffers_output)
        self.assertFalse(agents["cursor"].buffers_output)
        self.assertIn("stream-json", agents["cursor"].command)

    def test_warning_names_the_agent_and_the_flag_that_works(self):
        warning = yoyo.idle_timeout_warning(yoyo.DEFAULT_AGENTS["claude"], 180.0)
        self.assertIsNotNone(warning)
        self.assertIn("claude", warning)
        self.assertIn("180", warning)
        self.assertIn("--timeout", warning)

    def test_streaming_agent_gets_no_warning(self):
        self.assertIsNone(yoyo.idle_timeout_warning(yoyo.DEFAULT_AGENTS["codex"], 180.0))

    def test_no_idle_timeout_means_no_warning(self):
        self.assertIsNone(yoyo.idle_timeout_warning(yoyo.DEFAULT_AGENTS["claude"], None))


class CursorStreamDecodeTests(CliTestCase):
    """cursor's answer must survive a transport drop that kills the process."""

    @staticmethod
    def _line(event: dict) -> str:
        return json.dumps(event) + "\n"

    def _assistant(self, text: str) -> str:
        return self._line({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})

    def test_result_event_is_the_answer(self):
        raw = (
            self._line({"type": "system", "subtype": "init", "model": "Composer 2.5"})
            + self._assistant("thinking out loud")
            + self._line({"type": "result", "subtype": "success", "is_error": False, "result": "the final answer"})
        )
        answer, note = yoyo.decode_cursor_stream(raw)
        self.assertEqual(answer, "the final answer")
        self.assertEqual(note, "")

    def test_partial_answer_survives_a_dropped_stream(self):
        # The measured failure: cursor writes the file, streams its prose, then
        # the transport dies before any result event. Text mode loses all of it.
        raw = (
            self._assistant("Created the file. ")
            + self._line({"type": "tool_call", "subtype": "completed"})
            + self._assistant("Here is what it contains.")
            + self._line({"type": "connection", "subtype": "reconnecting"})
            + self._line({"type": "retry", "subtype": "starting"})
        )
        answer, note = yoyo.decode_cursor_stream(raw)
        self.assertEqual(answer, "Created the file. Here is what it contains.")
        self.assertIn("without a result event", note)
        self.assertIn("2 partial chunks", note)
        self.assertIn("2 transport reconnect", note)

    def test_transport_chatter_never_reaches_the_answer(self):
        raw = (
            self._line({"type": "connection", "subtype": "reconnecting"})
            + self._line({"type": "retry", "subtype": "starting"})
            + self._line({"type": "result", "subtype": "success", "result": "clean"})
        )
        answer, note = yoyo.decode_cursor_stream(raw)
        self.assertEqual(answer, "clean")
        self.assertNotIn("reconnect", answer)

    def test_empty_stream_says_so_instead_of_returning_nothing(self):
        raw = self._line({"type": "connection", "subtype": "reconnecting"})
        answer, note = yoyo.decode_cursor_stream(raw)
        self.assertEqual(answer, "")
        self.assertIn("no answer text", note)

    def test_non_json_capture_passes_through_untouched(self):
        # An --agent-arg override can put cursor back on text; a crash can beat
        # the first event. Blanking the capture would destroy the evidence.
        raw = "RetriableError: WritableIterable is closed\n"
        answer, note = yoyo.decode_cursor_stream(raw)
        self.assertEqual(answer, raw)
        self.assertEqual(note, "")

    def test_other_flavors_are_untouched(self):
        for name in ("codex", "claude", "pi", "grok"):
            raw = '{"type":"result","result":"not cursor"}\n'
            answer, note = yoyo.decode_agent_stdout(yoyo.DEFAULT_AGENTS[name], raw)
            self.assertEqual(answer, raw, name)
            self.assertEqual(note, "", name)


class CitationResolutionTests(CliTestCase):
    """What disk says about a co-cited file:line, and what it refuses to say."""

    def resolve(self, tmp, text, line):
        return yoyo.resolve_citation(text, line, Path(tmp))

    def test_a_real_line_comes_back_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "auth.py").write_text("import os\nSECRET = compute()\nreturn None\n")

            self.assertEqual(
                self.resolve(tmp, "auth.py", "2"),
                {"status": "line", "text": "SECRET = compute()"},
            )

    def test_a_missing_file_says_missing_and_nothing_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.resolve(tmp, "src/ghost.py", "9"), {"status": "missing"})

    def test_a_line_past_the_end_reports_the_real_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "short.py").write_text("one\ntwo\n")

            self.assertEqual(
                self.resolve(tmp, "short.py", "9999"),
                {"status": "line_out_of_range", "line_count": 2},
            )

    def test_a_path_outside_the_cwd_is_never_read(self):
        # An answer is untrusted text. A delegate that cites an absolute path
        # must not turn yoyo into a reader of the caller's home directory.
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "id_rsa"
            secret.write_text("PRIVATE KEY MATERIAL\n")
            with tempfile.TemporaryDirectory() as tmp:
                disk = self.resolve(tmp, str(secret), "1")

            self.assertEqual(disk, {"status": "outside_cwd"})
            self.assertNotIn("PRIVATE", json.dumps(disk))

    def test_a_parent_escape_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "repo"
            nested.mkdir()
            (Path(tmp) / "outside.txt").write_text("secret\n")

            self.assertEqual(
                yoyo.resolve_citation("../outside.txt", "1", nested),
                {"status": "outside_cwd"},
            )

    def test_a_symlink_pointing_out_of_the_cwd_is_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "secret.txt").write_text("secret\n")
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "link.txt").symlink_to(Path(outside) / "secret.txt")

                self.assertEqual(self.resolve(tmp, "link.txt", "1"), {"status": "outside_cwd"})

    def test_a_directory_is_not_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "src").mkdir()

            self.assertEqual(self.resolve(tmp, "src/", "1"), {"status": "not_a_file"})

    def test_control_bytes_never_reach_the_answer(self):
        # A cited line can carry ANSI escapes or NULs; they would otherwise be
        # replayed into the calling agent's terminal.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "noisy.txt").write_bytes(b"before\x1b[31mred\x00after\n")

            disk = self.resolve(tmp, "noisy.txt", "1")

            self.assertEqual(disk["status"], "line")
            self.assertNotIn("\x1b", disk["text"])
            self.assertNotIn("\x00", disk["text"])

    def test_a_long_line_is_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "min.js").write_text("x" * 5000 + "\n")

            disk = self.resolve(tmp, "min.js", "1")

            self.assertEqual(disk["status"], "line")
            self.assertEqual(len(disk["text"]), yoyo.CITATION_LINE_MAX_CHARS + 3)

    def test_a_line_past_the_scan_cap_is_not_called_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.log"
            path.write_bytes(b"x" * (yoyo.CITATION_SCAN_MAX_BYTES + 10))

            self.assertEqual(
                self.resolve(tmp, "huge.log", "2"),
                {"status": "scan_limit", "scanned_bytes": yoyo.CITATION_SCAN_MAX_BYTES},
            )

    def test_the_note_reads_as_evidence_not_a_verdict(self):
        rendered = yoyo.render_co_cited([
            {"site": "auth.py:2", "agents": ["a", "b"], "disk": {"status": "missing"}},
        ])

        self.assertIn("auth.py:2 — a, b", rendered)
        self.assertIn("no such file under the run cwd", rendered)
        for verdict in ("hallucinated", "invalid", "verified", "\u2713", "\u2717"):
            self.assertNotIn(verdict, rendered)

    def test_resolution_never_reorders_or_drops_a_site(self):
        shared = [
            {"site": "ghost.py:1", "agents": ["a", "b"], "spellings": ["ghost.py"]},
            {"site": "real.py:1", "agents": ["a", "b"], "spellings": ["real.py"]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "real.py").write_text("kept\n")
            yoyo.resolve_co_cited(shared, Path(tmp))

        self.assertEqual([entry["site"] for entry in shared], ["ghost.py:1", "real.py:1"])
        self.assertEqual([entry["disk"]["status"] for entry in shared], ["missing", "line"])


class BriefCitationTests(CliTestCase):
    def test_a_brief_citing_a_path_that_is_not_there_warns_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "present.py").write_text("x = 1\n", encoding="utf-8")
            text = "See present.py:4 and gone.py:9 and gone.py:12."

            self.assertEqual(yoyo.missing_brief_citations(text, cwd), ["gone.py"])

    def test_a_line_past_the_end_of_a_present_file_is_not_a_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "short.py").write_text("x = 1\n", encoding="utf-8")
            self.assertEqual(yoyo.missing_brief_citations("short.py:900", cwd), [])

    def test_a_brief_with_no_citations_says_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(yoyo.missing_brief_citations("plain prose, version 1.2", Path(tmp)), [])
            self.assertEqual(yoyo.missing_brief_citations(None, Path(tmp)), [])

    def test_a_path_outside_the_cwd_is_not_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(yoyo.missing_brief_citations("/etc/hosts:1", Path(tmp)), [])

    def test_the_warning_names_a_sample_and_counts_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = " ".join(f"gone{index}.py:{index + 1}" for index in range(8))
            with mock.patch.object(yoyo.sys, "stderr", new=io.StringIO()) as stderr:
                yoyo.warn_missing_brief_citations(text, Path(tmp))
            message = stderr.getvalue()
            self.assertIn("cites 8 path(s) not present", message)
            self.assertIn("gone0.py", message)
            self.assertIn("(+3 more)", message)
            self.assertNotIn("gone7.py", message)


class CitationReviewFixTests(CliTestCase):
    def test_a_line_cut_by_the_scan_cap_is_not_offered_as_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            target = cwd / "big.txt"
            # One short row, then a row long enough that the cap lands inside it.
            target.write_bytes(b"first\n" + b"x" * (yoyo.CITATION_SCAN_MAX_BYTES + 10))

            self.assertEqual(yoyo.resolve_citation("big.txt", "1", cwd)["status"], "line")
            self.assertEqual(yoyo.resolve_citation("big.txt", "2", cwd)["status"], "scan_limit")

    def test_a_c1_control_is_stripped_like_a_c0_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "esc.txt").write_bytes("before\u009b2Kafter\n".encode("utf-8"))

            text = yoyo.resolve_citation("esc.txt", "1", cwd)["text"]
            self.assertNotIn("\u009b", text)
            self.assertIn("before", text)
            self.assertIn("after", text)

    def test_one_file_is_read_once_across_every_site_that_cites_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "shared.py").write_text("\n".join(f"line {n}" for n in range(1, 10)), encoding="utf-8")
            shared = [
                {"site": "shared.py:2", "agents": ["a", "b"], "spellings": ["shared.py", "./shared.py"]},
                {"site": "shared.py:4", "agents": ["a", "b"], "spellings": ["shared.py"]},
            ]
            real_open = Path.open
            opened: list[str] = []

            def counting_open(self, *rest, **kwargs):
                opened.append(self.name)
                return real_open(self, *rest, **kwargs)

            with mock.patch.object(Path, "open", counting_open):
                yoyo.resolve_co_cited(shared, cwd)

            self.assertEqual(opened, ["shared.py"])
            self.assertEqual(shared[0]["disk"]["text"], "line 2")
            self.assertEqual(shared[1]["disk"]["text"], "line 4")

    def test_two_spellings_of_one_absent_path_are_one_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = "gone/a.py:1 and ./gone/a.py:9 and gone/b.py:3"
            self.assertEqual(yoyo.missing_brief_citations(text, Path(tmp)), ["gone/a.py", "gone/b.py"])

    def test_an_existence_check_never_opens_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "present.py").write_text("x\n", encoding="utf-8")

            def refuse_open(self, *rest, **kwargs):
                raise AssertionError(f"opened {self}")

            with mock.patch.object(Path, "open", refuse_open):
                self.assertEqual(yoyo.missing_brief_citations("present.py:1 gone.py:2", cwd), ["gone.py"])


class RunsAuditTests(CliTestCase):
    """The ledger nests, so the audit's first job is not to double-count it."""

    def _write_record(self, state_dir, run_id, *, meta, result=None, started_at=None):
        run_dir = Path(state_dir) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {"run_id": run_id, "started_at": started_at or yoyo.iso_utc(yoyo.utc_now())}
        payload.update(meta)
        (run_dir / "meta.json").write_text(json.dumps(payload), encoding="utf-8")
        if result is not None:
            (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return run_dir

    def _call(self, state_dir, run_id, agent, result, **kwargs):
        return self._write_record(state_dir, run_id, meta={"mode": "call", "agent": agent}, result=result, **kwargs)

    def _audit(self, state_dir, *extra):
        code, stdout, stderr = self.run_cli(["runs", "audit", "--json", *extra], env={"YOYO_STATE_DIR": state_dir})
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)

    def test_outcomes_ignore_the_parent_and_loop_records_that_wrap_a_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call(tmp, "20260101T000001-aaaa", "codex", {"exit_code": 0, "duration_s": 12.0, "stdout_bytes": 40})
            self._write_record(tmp, "20260101T000002-bbbb", meta={"agent": "codex", "argv": ["yoyo", "ask", "codex"]})
            self._write_record(tmp, "20260101T000003-cccc", meta={"agent": "codex", "loop_id": "loop-1"})

            report = self._audit(tmp)
            self.assertEqual(report["cohorts"], {"call": 1, "background parent": 1, "loop iteration": 1, "other": 0})
            self.assertEqual([row["agent"] for row in report["agents"]], ["codex"])
            self.assertEqual(report["agents"][0]["calls"], 1)

    def test_every_ending_a_call_can_have_gets_its_own_name(self):
        cases = [
            ({"exit_code": 0}, "ok"),
            ({"exit_code": 124, "timed_out": True, "timeout_s": 300}, "hard-timeout"),
            ({"exit_code": 124, "idle_timed_out": True, "idle_seconds": 90}, "idle-timeout"),
            ({"exit_code": 124}, "exit-124"),
            ({"exit_code": None, "killed_by_signal": 15}, "signal"),
            ({"exit_code": 1}, "nonzero"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, (result, _) in enumerate(cases):
                self._call(tmp, f"20260101T00000{index}-dddd", "codex", result)

            outcomes = self._audit(tmp)["agents"][0]["outcomes"]
            for _, expected in cases:
                self.assertEqual(outcomes[expected], 1, f"{expected}: {outcomes}")

    def test_a_call_still_in_flight_is_running_not_a_missing_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_record(tmp, "20260101T000001-8888", meta={"mode": "call", "agent": "codex", "pid": os.getpid()})
            self._write_record(tmp, "20260101T000002-9999", meta={"mode": "call", "agent": "codex", "pid": 99999999})

            outcomes = self._audit(tmp)["agents"][0]["outcomes"]
            self.assertEqual(outcomes["running"], 1)
            self.assertEqual(outcomes["no-result"], 1)

    def test_a_record_with_no_usable_exit_code_is_not_blamed_on_the_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call(tmp, "20260101T000001-abcd", "codex", {"duration_s": 3.0})

            outcomes = self._audit(tmp)["agents"][0]["outcomes"]
            self.assertEqual(outcomes["unreadable"], 1)
            self.assertEqual(outcomes["nonzero"], 0)

    def test_an_old_stderr_suffix_still_names_the_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call(tmp, "20260101T000001-eeee", "claude", {"exit_code": 124, "stderr": "Idle timeout after 90.0s with no output"})
            self.assertEqual(self._audit(tmp)["agents"][0]["outcomes"]["idle-timeout"], 1)

    def test_a_record_that_never_stamped_itself_is_not_an_agent_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call(tmp, "20260101T000001-ffff", "codex", None)
            (Path(tmp) / "runs" / "20260101T000002-0000").mkdir(parents=True)
            (Path(tmp) / "runs" / "20260101T000002-0000" / "meta.json").write_text("{not json", encoding="utf-8")

            report = self._audit(tmp)
            self.assertEqual(report["agents"][0]["outcomes"]["no-result"], 1)
            self.assertEqual(report["agents"][0]["outcomes"]["nonzero"], 0)
            self.assertEqual(report["records_without_meta"], 1)

    def test_a_result_file_that_will_not_parse_is_unreadable_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._call(tmp, "20260101T000001-1111", "codex", None)
            (run_dir / "result.json").write_text("{truncated", encoding="utf-8")
            self.assertEqual(self._audit(tmp)["agents"][0]["outcomes"]["unreadable"], 1)

    def test_the_days_window_excludes_a_record_older_than_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call(tmp, "20200101T000001-2222", "codex", {"exit_code": 0}, started_at="2020-01-01T00:00:00Z")
            self._call(tmp, "20260101T000002-3333", "codex", {"exit_code": 0})

            report = self._audit(tmp, "--days", "7")
            self.assertEqual(report["cohorts"]["call"], 1)
            self.assertEqual(self._audit(tmp, "--days", "36500")["cohorts"]["call"], 2)

    def test_an_empty_answer_is_reported_with_its_denominator_not_as_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call(tmp, "20260101T000001-4444", "codex", {"exit_code": 0, "stdout_bytes": 0})
            self._call(tmp, "20260101T000002-5555", "codex", {"exit_code": 0, "stdout_bytes": 800})

            report = self._audit(tmp)
            self.assertEqual(report["zero_stdout_bytes"], {"count": 1, "of_sized_calls": 2})
            self.assertEqual(report["agents"][0]["outcomes"]["ok"], 2)

    def test_percentiles_report_a_duration_some_call_actually_took(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, seconds in enumerate([10.0, 20.0, 30.0, 40.0]):
                self._call(tmp, f"20260101T00000{index}-6666", "codex", {"exit_code": 0, "duration_s": seconds})

            row = self._audit(tmp)["agents"][0]
            self.assertEqual(row["timed_calls"], 4)
            self.assertEqual(row["p50_s"], 20.0)
            self.assertEqual(row["p90_s"], 40.0)

    def test_the_table_omits_an_outcome_nobody_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call(tmp, "20260101T000001-7777", "codex", {"exit_code": 0, "duration_s": 5.0})

            code, stdout, stderr = self.run_cli(["runs", "audit"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 0, stderr)
            self.assertIn("ok", stdout)
            self.assertNotIn("hard-timeout", stdout)
            self.assertIn("call records only", stdout)

    def test_audit_refuses_a_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = self.run_cli(["runs", "audit", "someid"], env={"YOYO_STATE_DIR": tmp})
            self.assertEqual(code, 2)
            self.assertIn("does not accept a run_id", stderr)


class RunIdentityTests(CliTestCase):
    """A recorded pid outlives the process that held it; the run must not."""

    def _run_dir(self, tmp: str, meta: dict) -> Path:
        run_dir = Path(tmp) / "20260827T000000-0000beef"
        run_dir.mkdir()
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return run_dir

    def _live_pid(self) -> int:
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc.pid

    def test_reused_pid_reads_as_dead(self):
        pid = self._live_pid()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run_dir(tmp, {
                "run_id": "20260827T000000-0000beef",
                "pid": pid,
                "pid_started": "Sat Jan  1 00:00:00 2000",
                "started_at": "2026-08-27T00:00:00Z",
            })
            self.assertEqual(yoyo.run_status(run_dir), "dead")

    def test_recorded_process_reads_as_running(self):
        pid = self._live_pid()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run_dir(tmp, {
                "run_id": "20260827T000000-0000beef",
                "pid": pid,
                "pid_started": yoyo.pid_start_time(pid),
                "started_at": "2026-08-27T00:00:00Z",
            })
            self.assertEqual(yoyo.run_status(run_dir), "running")

    def test_record_without_identity_stays_permissive(self):
        # Runs written before pid_started existed keep the old answer rather
        # than being declared dead on missing evidence.
        pid = self._live_pid()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run_dir(tmp, {
                "run_id": "20260827T000000-0000beef",
                "pid": pid,
                "started_at": "2026-08-27T00:00:00Z",
            })
            self.assertEqual(yoyo.run_status(run_dir), "running")

    def test_start_time_of_a_dead_pid_is_empty(self):
        proc = subprocess.Popen(["sleep", "30"])
        proc.kill()
        proc.wait()
        self.assertEqual(yoyo.pid_start_time(proc.pid), "")


class ReviewFixTests(CliTestCase):
    """Defects a cross-vendor review of this release found, each reproduced first."""

    def _stub_command(self, tmp, body):
        script = Path(tmp) / "stub.py"
        script.write_text(body, encoding="utf-8")
        return f"python3 {shlex.quote(str(script))}"

    def test_journal_opt_out_keeps_the_prompt_out_of_meta(self):
        # prompt.txt is suppressed under the opt-out, but the background
        # parent also stores the child's argv - and the prompt is a positional
        # argument in it, uncapped and unredacted.
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_NO_CALL_JOURNAL": "1",
                "YOYO_AGENT_ECHO": "cat",
            }
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--background", "--json", "SECRETPHRASE-alpha"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            run_dir = Path(json.loads(stdout)["run_dir"])
            meta_text = (run_dir / "meta.json").read_text(encoding="utf-8")
            self.assertNotIn("SECRETPHRASE-alpha", meta_text)
            self.assertIn("ask", json.loads(meta_text)["argv"])

    def _excerpt_of(self, tmp, prompt_text):
        run_dir = Path(tmp) / "run"
        run_dir.mkdir()
        (run_dir / "prompt.txt").write_text(prompt_text, encoding="utf-8")
        return yoyo.prompt_excerpt(run_dir)

    def test_asked_line_stops_before_attached_file_context(self):
        # The excerpt ran from the task heading to the calling-context trailer,
        # swallowing whatever --file put between them.
        with tempfile.TemporaryDirectory() as tmp:
            excerpt = self._excerpt_of(tmp, (
                "You are a delegate.\n\n"
                "Task:\nreview this config\n\n"
                "Context files:\n<file path=\"creds.env\">\nAWS_SECRET=leaked-value\n</file>\n\n"
                "Calling context: cwd=/tmp; mode=read-only delegation; caller=test.\n"
            ))

            self.assertEqual(excerpt, "review this config")

    def test_asked_line_stops_before_stdin_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            excerpt = self._excerpt_of(tmp, (
                "You are a delegate.\n\n"
                "Task:\nsummarise the log\n\n"
                "<stdin>\nSESSION_TOKEN=leaked-value\n</stdin>\n\n"
                "Calling context: cwd=/tmp; mode=read-only delegation; caller=test.\n"
            ))

            self.assertEqual(excerpt, "summarise the log")

    def test_asked_line_keeps_a_multiline_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            excerpt = self._excerpt_of(tmp, (
                "You are a delegate.\n\n"
                "Task:\nfirst line\nsecond line\n\n"
                "Calling context: cwd=/tmp; mode=read-only delegation; caller=test.\n"
            ))

            self.assertEqual(excerpt, "first line second line")

    def test_malformed_timeout_suffix_names_no_clock(self):
        # A diagnostic tool must not be the thing that crashes: an agent whose
        # own stderr ends in a lookalike line used to reach float("1..2").
        self.assertIsNone(yoyo.timeout_clock({"exit_code": 124, "stderr": "Timed out after 1..2s"}))
        self.assertEqual(
            yoyo.timeout_clock({"exit_code": 124, "stderr": "Timed out after 300s"}),
            (yoyo.HARD_TIMEOUT_CLOCK, 300.0),
        )
        self.assertEqual(
            yoyo.timeout_clock({"exit_code": 124, "stderr": "Idle timeout after 12.5s with no output (0 bytes captured before the stall)"}),
            (yoyo.IDLE_TIMEOUT_CLOCK, 12.5),
        )

    def test_loop_failure_tail_falls_back_to_stdout(self):
        # An iteration that fails with everything on stdout and nothing on
        # stderr used to print a blank reason.
        with tempfile.TemporaryDirectory() as tmp:
            command = self._stub_command(tmp, (
                "import sys\n"
                "sys.stdin.read()\n"
                "print('TypeError: cannot read x')\n"
                "sys.exit(1)\n"
            ))
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}

            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "1", "do the work"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("TypeError: cannot read x", stdout)

    def test_orphan_check_ignores_a_lookalike_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            source = Path(tmp) / "source"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            target = home / ".claude" / "skills" / "yoyo"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            for lookalike in ("yoyodyne", "yoyo2"):
                other = home / ".claude" / "skills" / lookalike
                other.mkdir(parents=True)
                (other / "SKILL.md").write_text("# mine\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(home / ".pi/agent"),
                "YOYO_SKILL_SOURCE": str(source),
                "YOYO_STATE_DIR": tmp,
                "YOYO_CONFIG": str(Path(tmp) / "missing.json"),
            }

            code, stdout, stderr = self.run_cli(["doctor", "--strict"], env=env)
            self.assertEqual(code, 0, stderr + stdout)
            self.assertNotIn("yoyodyne", stdout)
            self.assertNotIn("yoyo2", stdout)

    def test_orphan_check_flags_a_copied_inbuilt_skill(self):
        # yoyo-fable-mode is bundled but never installed, so a copy in an agent
        # home is a stray that shadows the bundle - and must not be described
        # as missing from it.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            source = Path(tmp) / "source"
            for name in ("yoyo", "yoyo-fable-mode"):
                (source / name).mkdir(parents=True)
                (source / name / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            target = home / ".claude" / "skills" / "yoyo"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            stray = home / ".claude" / "skills" / "yoyo-fable-mode"
            stray.mkdir(parents=True)
            (stray / "SKILL.md").write_text("# canonical\n", encoding="utf-8")
            env = {
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(home / ".pi/agent"),
                "YOYO_SKILL_SOURCE": str(source),
                "YOYO_STATE_DIR": tmp,
                "YOYO_CONFIG": str(Path(tmp) / "missing.json"),
            }

            code, stdout, stderr = self.run_cli(["doctor"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("skill yoyo-fable-mode: ORPHANED", stdout)
            self.assertIn("yoyo installs no copy of it", stdout)

    def test_journal_opt_out_redacts_a_prompt_split_around_flags(self):
        # argparse lets the question sit on both sides of a flag, so matching
        # one contiguous run of words left half of it in the stored argv.
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp, "YOYO_NO_CALL_JOURNAL": "1", "YOYO_AGENT_ECHO": "cat"}

            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--background", "LEAKONE", "--json", "LEAKTWO"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            meta_text = (Path(json.loads(stdout)["run_dir"]) / "meta.json").read_text(encoding="utf-8")
            self.assertNotIn("LEAKONE", meta_text)
            self.assertNotIn("LEAKTWO", meta_text)

    def test_journal_opt_out_covers_a_single_string_prompt(self):
        # imagegen takes its prompt as prompt_text, not a word list, and it is
        # just as positional in the argv the parent stores.
        words = yoyo.prompt_words_of(argparse.Namespace(prompt_text="a red bicycle"))
        argv = ["yoyo", "imagegen", "a red bicycle", "--out", "bike.png"]

        self.assertEqual(words, ["a red bicycle"])
        self.assertEqual(
            yoyo.redact_prompt_args(argv, words),
            ["yoyo", "imagegen", yoyo.REDACTED_PROMPT_ARG, "--out", "bike.png"],
        )

    def test_background_warns_about_buffering_before_it_detaches(self):
        # The child inherits --idle-timeout, but its stderr goes to log.txt,
        # where the caller reads the warning only after the run is over.
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp, "YOYO_AGENT_CLAUDE": "cat"}

            code, stdout, stderr = self.run_cli(
                ["ask", "claude", "--background", "--idle-timeout", "60", "--json", "hello"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("--idle-timeout 60s is not a hang guard", stderr)

    def test_poll_expiry_counts_a_background_parents_captures(self):
        # A background parent has no stdout.txt: the child's stdout is the
        # result envelope, and its stderr is log.txt.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "20260827T100000-00000031"
            run_dir.mkdir()
            (run_dir / "meta.json").write_text(json.dumps({"started_at": "2026-08-27T10:00:00Z"}), encoding="utf-8")
            (run_dir / "result.json").write_text("x" * 40, encoding="utf-8")
            (run_dir / "log.txt").write_text("y" * 12, encoding="utf-8")

            report = yoyo.poll_expiry_report(run_dir.name, run_dir)

            self.assertEqual(report["stdout_bytes"], 40)
            self.assertEqual(report["stderr_bytes"], 12)

    def test_timed_out_result_carries_the_clock_that_fired(self):
        # A background parent's result.json is the child's envelope and has no
        # flags of its own, so the envelope has to say which clock fired
        # instead of leaving autopsy to trust a stderr suffix.
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_AGENT_SLOW": "python3 -c \"import time; time.sleep(20)\"",
            }

            code, stdout, stderr = self.run_cli(
                ["ask", "slow", "--timeout", "1", "--json", "hello"],
                env=env,
            )

            self.assertEqual(code, 124, stderr)
            payload = json.loads(stdout)
            self.assertTrue(payload["timed_out"])
            self.assertEqual(payload["timeout_s"], 1.0)
            self.assertEqual(
                yoyo.timeout_clock(payload),
                (yoyo.HARD_TIMEOUT_CLOCK, 1.0),
            )

    def test_co_citation_sees_a_repeated_agents_two_samples(self):
        # `codex,codex` is a documented self-consistency sample: two
        # independent answers that happen to share a name.
        results = [
            {"agent": "codex", "stdout": "the bug is at bin/yoyo:1338"},
            {"agent": "codex", "stdout": "look at /repo/bin/yoyo:1338"},
        ]

        shared = yoyo.co_cited_sites(results)

        # The fullest spelling of the two is what gets displayed.
        self.assertEqual([entry["site"] for entry in shared], ["/repo/bin/yoyo:1338"])
        self.assertEqual(shared[0]["agents"], ["codex", "codex"])

    def test_co_citation_still_needs_two_answers(self):
        results = [{"agent": "codex", "stdout": "bin/yoyo:1338 and bin/yoyo:1338 again"}]

        self.assertEqual(yoyo.co_cited_sites(results), [])

    def test_configured_agent_keeps_its_buffering_behavior(self):
        # Overriding claude's command in agents.json must not quietly drop the
        # measured fact that claude buffers.
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(json.dumps({"agents": {
                "claude": {"command": ["my-claude-wrapper", "-p"], "kind": "claude"},
                "mine": {"command": ["mine"]},
                "slow": {"command": ["slow"], "buffers_output": True},
            }}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"YOYO_CONFIG": str(config)}):
                agents = yoyo.load_agents()
            self.assertTrue(agents["claude"].buffers_output)
            self.assertFalse(agents["mine"].buffers_output)
            self.assertTrue(agents["slow"].buffers_output)


if __name__ == "__main__":
    unittest.main()
