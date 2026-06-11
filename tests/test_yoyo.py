import argparse
import importlib.util
import importlib.machinery
import io
import json
import os
import shlex
import sys
import tempfile
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


class YoyoTests(unittest.TestCase):
    def run_cli(self, argv, *, stdin="", env=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        with mock.patch.dict(os.environ, merged_env, clear=True):
            with mock.patch("sys.stdin", io.StringIO(stdin)):
                with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                    code = yoyo.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_version_outputs_current_release(self):
        code, stdout, stderr = self.run_cli(["--version"])

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout.strip(), "yoyo 0.9.0")

    def test_custom_agent_receives_rendered_prompt_on_stdin(self):
        env = {"YOYO_AGENT_ECHO": "python3 -c \"import sys; print(sys.stdin.read())\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--role", "opinion", "Check this."],
            env=env,
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("independent second-opinion agent", stdout)
        self.assertIn("Task:\nCheck this.", stdout)

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

    def test_background_run_writes_meta_and_result_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "YOYO_STATE_DIR": tmp,
                "YOYO_AGENT_ECHO": "python3 -c \"print('ok')\"",
            }
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--background", "--trace-id", "trace-bg", "hello"],
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
            self.assertEqual(result["agent"], "echo")
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("duration_s", result)

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
            self._write_run_meta(tmp, run_id, pid=os.getpid())

            code, stdout, stderr = self.run_cli(
                ["wait", run_id, "--timeout", "0.05", "--poll", "0.01"],
                env=env,
            )
            self.assertEqual(code, 124)
            self.assertEqual(stdout, "")
            self.assertIn("timed out waiting", stderr)

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

    def test_default_timeout_is_one_hour_for_agent_and_workflow_calls(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            parser = yoyo.build_parser()

        ask_args = parser.parse_args(["ask", "codex", "hello"])
        workflow_args = parser.parse_args(["workflow", "workflow.json"])

        self.assertEqual(ask_args.timeout, 3600.0)
        self.assertEqual(workflow_args.timeout, 3600.0)

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

    def test_input_budget_is_aggregate_across_stdin_and_files(self):
        env = {"YOYO_AGENT_ECHO": "python3 -c \"import sys; print(sys.stdin.read())\""}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("file-data", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["ask", "echo", "--cwd", tmp, "--max-input-bytes", "3", "--file", "note.txt", "hello"],
                stdin="abc",
                env=env,
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("<stdin>\nabc", stdout)
        self.assertIn("input budget exhausted before this file", stdout)

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

    def test_chat_builds_interactive_command(self):
        code, stdout, stderr = self.run_cli(
            ["chat", "claude", "--dry-run", "--model", "haiku", "Debug this."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("claude", stdout)
        self.assertIn("--permission-mode bypassPermissions", stdout)
        self.assertIn("--model haiku", stdout)
        self.assertIn("'Debug this.'", stdout)

    def test_chat_builds_codex_interactive_full_access_command(self):
        code, stdout, stderr = self.run_cli(
            ["chat", "codex", "--dry-run", "Debug this."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("codex -C", stdout)
        self.assertIn("--sandbox danger-full-access", stdout)
        self.assertIn("--ask-for-approval never", stdout)

    def test_chat_builds_codex_interactive_read_only_command(self):
        code, stdout, stderr = self.run_cli(
            ["chat", "codex", "--dry-run", "--read-only", "Debug this."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("--sandbox read-only", stdout)
        self.assertNotIn("--ask-for-approval never", stdout)

    def test_chat_claude_session_creates_and_resumes_named_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YOYO_STATE_DIR": tmp, "YOYO_CONFIG": str(Path(tmp) / "missing.json")}
            code, stdout, stderr = self.run_cli(
                ["chat", "claude", "--session", "foo", "--dry-run", "Debug this."],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            first_command = shlex.split(stdout.strip())
            self.assertIn("--session-id", first_command)
            backend_id = first_command[first_command.index("--session-id") + 1]

            code, stdout, stderr = self.run_cli(
                ["chat", "claude", "--session", "foo", "--dry-run", "Debug more."],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            second_command = shlex.split(stdout.strip())
            self.assertIn("--resume", second_command)
            self.assertEqual(second_command[second_command.index("--resume") + 1], backend_id)

    def test_chat_codex_session_resumes_recorded_session(self):
        fixed_id = "44444444-4444-4444-8444-444444444444"
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session_record(tmp, "codex", "foo", fixed_id)
            code, stdout, stderr = self.run_cli(
                ["chat", "codex", "--session", "foo", "--dry-run", "Debug this."],
                env={"YOYO_STATE_DIR": tmp, "YOYO_CONFIG": str(Path(tmp) / "missing.json")},
            )

        self.assertEqual(code, 0, stderr)
        command = shlex.split(stdout.strip())
        self.assertEqual(command[0:2], ["codex", "resume"])
        self.assertEqual(command[-2:], [fixed_id, "Debug this."])

    def test_chat_launches_interactive_subprocess_without_capture(self):
        env = {"YOYO_AGENT_FAKE": "/usr/bin/env"}
        with mock.patch.object(yoyo.subprocess, "call", return_value=7) as call:
            code, stdout, stderr = self.run_cli(["chat", "fake", "hello"], env=env)

        self.assertEqual(code, 7)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        call.assert_called_once()
        self.assertEqual(call.call_args.args[0], ["/usr/bin/env", "hello"])
        self.assertIn("cwd", call.call_args.kwargs)

    def test_chat_joins_custom_agent_prompt_as_one_argument(self):
        env = {"YOYO_AGENT_FAKE": "/usr/bin/env"}
        with mock.patch.object(yoyo.subprocess, "call", return_value=0) as call:
            code, stdout, stderr = self.run_cli(["chat", "fake", "hello", "world"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(call.call_args.args[0], ["/usr/bin/env", "hello world"])

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
        self.assertIn("Timed out after 0.1s", payload["stderr_plain"])

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

    def test_codex_last_message_replaces_stdout_and_keeps_raw_stderr_in_json(self):
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
        self.assertEqual(payload["stderr"], "codex stderr")
        self.assertEqual(payload["stderr_plain"], "")

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
                    "print('OK') if data == 'Reply with exactly: OK' else sys.exit(9)",
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
            self.assertTrue((pi_dir.resolve() / "skills" / "yoyo" / "SKILL.md").exists())
            self.assertTrue((pi_dir.resolve() / "skills" / "yoyo-workflow" / "SKILL.md").exists())

    def test_install_skill_prunes_stale_files_from_existing_skill_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "yoyo").mkdir(parents=True)
            (source / "yoyo" / "SKILL.md").write_text("base", encoding="utf-8")
            pi_dir = Path(tmp) / "pi"
            stale = pi_dir / "skills" / "yoyo" / "old.txt"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale", encoding="utf-8")

            code, stdout, stderr = self.run_cli(
                ["install-skill"],
                env={
                    "YOYO_SKILL_SOURCE": str(source),
                    "HOME": str(Path(tmp) / "home"),
                    "PI_CODING_AGENT_DIR": str(pi_dir),
                },
            )

            self.assertEqual(code, 0, stderr)
            self.assertFalse((pi_dir.resolve() / "skills" / "yoyo" / "old.txt").exists())
            self.assertTrue((pi_dir.resolve() / "skills" / "yoyo" / "SKILL.md").exists())

    def test_workflow_runs_phases_and_passes_previous_outputs_to_review_job(self):
        with tempfile.TemporaryDirectory() as tmp:
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
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "two-phase",
                        "defaults": {"agent": "echo", "read_only": True},
                        "phases": [
                            {"name": "fanout", "jobs": [{"id": "first", "prompt": "First pass"}]},
                            {
                                "name": "review",
                                "jobs": [
                                    {
                                        "id": "cross-check",
                                        "role": "review",
                                        "include_previous": True,
                                        "prompt": "Check prior output",
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json", "--trace-id", "wf-1"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["workflow"], "two-phase")
        self.assertEqual(payload["job_count"], 2)
        self.assertEqual(payload["phases"][0]["jobs"][0]["job_id"], "first")
        review_stdout = payload["phases"][1]["jobs"][0]["stdout"]
        self.assertIn("Previous workflow outputs", review_stdout)
        self.assertIn("First pass", review_stdout)
        self.assertTrue(payload["phases"][1]["jobs"][0]["read_only"])

    def test_workflow_for_each_expands_jobs_and_templates_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("a", encoding="utf-8")
            (Path(tmp) / "b.txt").write_text("b", encoding="utf-8")
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
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "fanout",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {
                                "name": "audit",
                                "jobs": [
                                    {
                                        "id": "audit-{index}",
                                        "for_each": ["a.txt", "b.txt"],
                                        "prompt": "Audit {item}",
                                        "files": ["{item}"],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json", "--cwd", tmp],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        jobs = payload["phases"][0]["jobs"]
        self.assertEqual([job["job_id"] for job in jobs], ["audit-0", "audit-1"])
        self.assertIn("<file path=\"a.txt\">", jobs[0]["stdout"])
        self.assertIn("<file path=\"b.txt\">", jobs[1]["stdout"])

    def test_workflow_blocks_previous_output_into_write_capable_job_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "echo": {
                                "command": ["python3", "-c", "import sys; print(sys.stdin.read())"],
                                "read_only_args": ["--safe"],
                                "full_access_args": ["--write"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "unsafe",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "one", "jobs": [{"id": "source", "prompt": "Find something"}]},
                            {
                                "name": "two",
                                "jobs": [
                                    {
                                        "id": "writer",
                                        "read_only": False,
                                        "include_previous": True,
                                        "prompt": "Apply prior advice",
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 2)
        payload = json.loads(stdout)
        self.assertEqual(payload["exit_code"], 2)
        failed = payload["phases"][1]["jobs"][0]
        self.assertIn("allow_untrusted_context=true", failed["stderr"])

    def test_workflow_dry_run_renders_commands_without_running_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "dry",
                        "defaults": {"agent": "codex", "model": "gpt-5"},
                        "phases": [{"name": "plan", "jobs": [{"id": "one", "prompt": "Plan {input}"}]}],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--dry-run", "--json", "--input", "the migration"],
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        job = payload["phases"][0]["jobs"][0]
        self.assertIn("--model", job["command"])
        self.assertIn("gpt-5", job["command"])
        self.assertIn("Plan the migration", job["prompt"])
        self.assertIn("--sandbox", job["command"])
        self.assertIn("read-only", job["command"])

    def test_workflow_duplicate_phase_names_fail_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "bad",
                        "phases": [
                            {"name": "same", "jobs": [{"id": "one", "prompt": "One"}]},
                            {"name": "same", "jobs": [{"id": "two", "prompt": "Two"}]},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(["workflow", str(spec), "--json"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("duplicated", stderr)

    def test_workflow_unknown_include_phase_is_reported_in_job_result(self):
        with tempfile.TemporaryDirectory() as tmp:
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
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "bad-include",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {
                                "name": "review",
                                "jobs": [
                                    {
                                        "id": "future",
                                        "include_phases": ["missing"],
                                        "prompt": "Review missing phase",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 2)
        payload = json.loads(stdout)
        self.assertIn("unknown or future include_phases", payload["phases"][0]["jobs"][0]["stderr"])

    def test_workflow_phase_level_include_previous_is_inherited_by_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
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
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "inherited-context",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "first", "jobs": [{"id": "source", "prompt": "Source output"}]},
                            {
                                "name": "second",
                                "include_previous": True,
                                "jobs": [{"id": "reviewer", "prompt": "Review inherited context"}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertIn("Previous workflow outputs", payload["phases"][1]["jobs"][0]["stdout"])
        self.assertIn("Source output", payload["phases"][1]["jobs"][0]["stdout"])

    def test_workflow_previous_output_with_agent_args_requires_explicit_untrusted_context(self):
        with tempfile.TemporaryDirectory() as tmp:
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
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "raw-args-context",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "first", "jobs": [{"id": "source", "prompt": "Source output"}]},
                            {
                                "name": "second",
                                "jobs": [
                                    {
                                        "id": "reviewer",
                                        "include_previous": True,
                                        "agent_args": ["--maybe-write"],
                                        "prompt": "Review with raw args",
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 2)
        payload = json.loads(stdout)
        self.assertIn("write-capable or raw-arg agent", payload["phases"][1]["jobs"][0]["stderr"])

    def test_workflow_duplicate_expanded_job_ids_fail_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "duplicate-jobs",
                        "phases": [
                            {
                                "name": "audit",
                                "jobs": [
                                    {
                                        "id": "same",
                                        "for_each": ["a", "b"],
                                        "prompt": "Audit {item}",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(["workflow", str(spec), "--json"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("duplicate job id", stderr)

    def test_workflow_invalid_timeout_fails_before_job_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "bad-timeout",
                        "phases": [{"name": "one", "jobs": [{"id": "one", "prompt": "One"}]}],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(["workflow", str(spec), "--timeout", "0", "--json"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--timeout must be greater than 0", stderr)

    def test_workflow_invalid_io_limits_fail_before_job_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "bad-limits",
                        "phases": [{"name": "one", "jobs": [{"id": "one", "prompt": "One"}]}],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(["workflow", str(spec), "--max-output-bytes", "0", "--json"])
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("--max-output-bytes must be at least 1", stderr)

            code, stdout, stderr = self.run_cli(["workflow", str(spec), "--max-input-bytes", "0", "--json"])
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("--max-input-bytes must be at least 1", stderr)

    def test_workflow_invalid_context_bytes_env_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "bad-context-env",
                        "phases": [{"name": "one", "jobs": [{"id": "one", "prompt": "One"}]}],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_WORKFLOW_CONTEXT_BYTES": "0"},
            )
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("YOYO_WORKFLOW_CONTEXT_BYTES must be at least 1", stderr)

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_WORKFLOW_CONTEXT_BYTES": "not-an-int"},
            )
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("YOYO_WORKFLOW_CONTEXT_BYTES must be an integer", stderr)

    def test_workflow_exit_code_uses_first_failing_job_in_phase_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "slow2": {
                                "command": ["python3", "-c", "import sys, time; time.sleep(0.1); sys.exit(2)"],
                                "read_only_args": ["--safe"],
                            },
                            "fast3": {
                                "command": ["python3", "-c", "import sys; sys.exit(3)"],
                                "read_only_args": ["--safe"],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "exit-order",
                        "phases": [
                            {
                                "name": "failures",
                                "jobs": [
                                    {"id": "first", "agent": "slow2", "prompt": "First fails second"},
                                    {"id": "second", "agent": "fast3", "prompt": "Second fails first"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 2)
        payload = json.loads(stdout)
        self.assertEqual(payload["exit_code"], 2)
        self.assertEqual([job["job_id"] for job in payload["phases"][0]["jobs"]], ["first", "second"])
        self.assertEqual([job["exit_code"] for job in payload["phases"][0]["jobs"]], [2, 3])

    def test_workflow_fail_fast_stops_after_failing_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "fail": {
                                "command": ["python3", "-c", "import sys; sys.exit(2)"],
                                "read_only_args": ["--safe"],
                            },
                            "echo": {
                                "command": ["python3", "-c", "import sys; print(sys.stdin.read())"],
                                "read_only_args": ["--safe"],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "fail-fast",
                        "phases": [
                            {"name": "one", "jobs": [{"id": "fail", "agent": "fail", "prompt": "Fail"}]},
                            {"name": "two", "jobs": [{"id": "skip", "agent": "echo", "prompt": "Should not run"}]},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json", "--fail-fast"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 2)
        payload = json.loads(stdout)
        self.assertEqual([phase["name"] for phase in payload["phases"]], ["one"])

    def test_workflow_allow_untrusted_context_permits_agent_args_escape_hatch(self):
        with tempfile.TemporaryDirectory() as tmp:
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
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "allow-untrusted",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "first", "jobs": [{"id": "source", "prompt": "Source output"}]},
                            {
                                "name": "second",
                                "jobs": [
                                    {
                                        "id": "reviewer",
                                        "include_previous": True,
                                        "agent_args": ["--maybe-write"],
                                        "allow_untrusted_context": True,
                                        "prompt": "Review with acknowledged raw args",
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertIn("--maybe-write", payload["phases"][1]["jobs"][0]["command"])
        self.assertIn("Previous workflow outputs", payload["phases"][1]["jobs"][0]["stdout"])

    def test_workflow_include_phases_selects_named_prior_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
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
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "include-phases",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "one", "jobs": [{"id": "one", "prompt": "Phase one marker"}]},
                            {"name": "two", "jobs": [{"id": "two", "prompt": "Phase two marker"}]},
                            {
                                "name": "review",
                                "include_phases": ["one"],
                                "jobs": [{"id": "reviewer", "prompt": "Review selected phase"}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        review_stdout = json.loads(stdout)["phases"][2]["jobs"][0]["stdout"]
        self.assertIn("Phase one marker", review_stdout)
        self.assertNotIn("Phase two marker", review_stdout)

    def test_workflow_max_jobs_guard_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "too-many",
                        "phases": [
                            {
                                "name": "fanout",
                                "jobs": [
                                    {
                                        "id": "job-{index}",
                                        "for_each": ["a", "b"],
                                        "prompt": "Audit {item}",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(["workflow", str(spec), "--max-jobs", "1", "--json"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("above max_jobs=1", stderr)

    def test_workflow_cli_max_jobs_overrides_spec_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "cli-cap-wins",
                        "max_jobs": 100,
                        "phases": [
                            {
                                "name": "fanout",
                                "jobs": [
                                    {
                                        "id": "job-{index}",
                                        "for_each": ["a", "b"],
                                        "prompt": "Audit {item}",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(["workflow", str(spec), "--max-jobs", "1", "--json"])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("above max_jobs=1", stderr)

    def test_workflow_cli_context_bytes_overrides_spec_context_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
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
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "cli-context-wins",
                        "context_bytes": 1000,
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "source", "jobs": [{"id": "source", "prompt": "0123456789abcdef"}]},
                            {
                                "name": "review",
                                "jobs": [{"id": "reviewer", "include_previous": True, "prompt": "Review short context"}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json", "--context-bytes", "10"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        review_stdout = json.loads(stdout)["phases"][1]["jobs"][0]["stdout"]
        self.assertIn("workflow context budget exhausted after 10 bytes", review_stdout)

    def test_workflow_previous_context_is_truncated_at_context_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
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
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "truncated-context",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "source", "jobs": [{"id": "source", "prompt": "0123456789abcdef"}]},
                            {
                                "name": "review",
                                "jobs": [{"id": "reviewer", "include_previous": True, "prompt": "Review short context"}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json", "--context-bytes", "10"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        review_stdout = json.loads(stdout)["phases"][1]["jobs"][0]["stdout"]
        self.assertIn("workflow context budget exhausted after 10 bytes", review_stdout)


    def test_open_idle_stdin_pipe_does_not_block_or_inject(self):
        # A prompt arg plus an open-but-idle stdin pipe (the agent-to-agent case)
        # previously blocked forever in build_prompt before launching the agent.
        env = {"YOYO_AGENT_ECHO": "cat"}
        read_fd, write_fd = os.pipe()  # writer never writes: never ready, never EOF
        reader = os.fdopen(read_fd, "r")
        merged_env = os.environ.copy()
        merged_env.update(env)
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
        env = {"YOYO_AGENT_SLEEP": "python3 -c \"import time; time.sleep(5)\""}
        code, stdout, stderr = self.run_cli(
            ["ask", "sleep", "--json", "--idle-timeout", "0.3", "hello"],
            env=env,
        )

        self.assertEqual(code, 124)
        payload = json.loads(stdout)
        self.assertEqual(payload["exit_code"], 124)
        self.assertIn("Idle timeout", payload["stderr"])
        self.assertIn("Idle timeout", payload["stderr_plain"])

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

    def test_ask_rejects_path_like_skill_names(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "echo", "--skill", "../evil", "--dry-run", "Build."],
            env={"YOYO_AGENT_ECHO": "true"},
        )

        self.assertEqual(code, 2)
        self.assertIn("Invalid skill name", stderr)

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

    def test_workflow_template_resolves_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._echo_agent_config(tmp)
            templates = Path(tmp) / "templates"
            templates.mkdir()
            (templates / "smoke.json").write_text(
                json.dumps(
                    {
                        "name": "smoke",
                        "defaults": {"agent": "echo"},
                        "phases": [{"name": "one", "jobs": [{"id": "j1", "prompt": "Say hi"}]}],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["workflow", "smoke", "--json"],
                env={"YOYO_CONFIG": str(config), "YOYO_WORKFLOW_PATH": str(templates)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["workflow"], "smoke")
        self.assertEqual(payload["spec"], str(templates / "smoke.json"))

    def test_workflow_list_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            templates = Path(tmp) / "templates"
            templates.mkdir()
            (templates / "yoyo-test-wf.json").write_text("{}", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["workflow", "--list", "--json"],
                env={"YOYO_WORKFLOW_PATH": str(templates)},
            )

        self.assertEqual(code, 0, stderr)
        names = [row["name"] for row in json.loads(stdout)]
        self.assertIn("yoyo-test-wf", names)

    def test_workflow_without_spec_or_list_fails_loudly(self):
        code, stdout, stderr = self.run_cli(["workflow"])

        self.assertEqual(code, 2)
        self.assertIn("Pass a workflow spec path or template name", stderr)

    def test_workflow_gate_failure_stops_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._echo_agent_config(tmp)
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "gated",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {
                                "name": "one",
                                "jobs": [{"id": "j1", "prompt": "First"}],
                                "gates": [{"name": "must-fail", "run": "echo gate-stdout; exit 7"}],
                            },
                            {"name": "two", "jobs": [{"id": "j2", "prompt": "Never runs"}]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 7, stderr)
        payload = json.loads(stdout)
        self.assertEqual(len(payload["phases"]), 1)
        gate = payload["phases"][0]["gates"][0]
        self.assertEqual(gate["exit_code"], 7)
        self.assertIn("gate-stdout", gate["stdout"])

    def test_workflow_gate_success_lets_next_phase_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._echo_agent_config(tmp)
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "gated-ok",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {
                                "name": "one",
                                "jobs": [{"id": "j1", "prompt": "First"}],
                                "gates": ["true"],
                            },
                            {"name": "two", "jobs": [{"id": "j2", "prompt": "Second"}]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(len(payload["phases"]), 2)
        self.assertEqual(payload["phases"][0]["gates"][0]["exit_code"], 0)

    def test_workflow_gates_are_skipped_when_phase_jobs_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "boom": {
                                "command": ["python3", "-c", "import sys; sys.stdin.read(); sys.exit(5)"],
                                "read_only_args": ["--safe"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "gated-fail",
                        "defaults": {"agent": "boom"},
                        "phases": [
                            {
                                "name": "one",
                                "jobs": [{"id": "j1", "prompt": "First"}],
                                "gates": ["echo should-not-run"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 5, stderr)
        payload = json.loads(stdout)
        gate = payload["phases"][0]["gates"][0]
        self.assertEqual(gate["skipped"], "phase jobs failed")

    def test_workflow_expect_contract_failure_sets_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._echo_agent_config(tmp)
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "contract",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {
                                "name": "one",
                                "jobs": [
                                    {
                                        "id": "j1",
                                        "prompt": "First",
                                        "expect": {"contains": "TEXT_THAT_NEVER_APPEARS"},
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 3, stderr)
        payload = json.loads(stdout)
        job = payload["phases"][0]["jobs"][0]
        self.assertEqual(job["exit_code"], 3)
        self.assertEqual(job["attempts"], 1)
        self.assertIn("output contract not met", job["stderr"])

    def test_workflow_expect_pass_keeps_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._echo_agent_config(tmp)
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "contract-ok",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {
                                "name": "one",
                                "jobs": [
                                    {
                                        "id": "j1",
                                        "prompt": "MARKER_ABC",
                                        "expect": {"contains": ["MARKER_ABC"], "regex": "Task:"},
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["phases"][0]["jobs"][0]["attempts"], 1)

    def test_workflow_retries_until_success_and_records_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            counter = Path(tmp) / "counter"
            agent_code = (
                "import sys, os\n"
                "path = sys.argv[1]\n"
                "n = int(open(path).read()) if os.path.exists(path) else 0\n"
                "open(path, 'w').write(str(n + 1))\n"
                "sys.stdin.read()\n"
                "if n == 0:\n"
                "    sys.exit(1)\n"
                "print('recovered')\n"
            )
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "flaky": {
                                "command": ["python3", "-c", agent_code, str(counter)],
                                "read_only_args": ["--safe"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "flaky-retry",
                        "defaults": {"agent": "flaky"},
                        "phases": [
                            {
                                "name": "one",
                                "jobs": [{"id": "j1", "prompt": "Go", "retries": 2}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={"YOYO_CONFIG": str(config)},
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        job = payload["phases"][0]["jobs"][0]
        self.assertEqual(job["exit_code"], 0)
        self.assertEqual(job["attempts"], 2)
        self.assertIn("recovered", job["stdout"])

    def test_workflow_job_skill_is_injected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._echo_agent_config(tmp)
            self._write_skill(Path(tmp) / "skills", "myskill", body="WORKFLOW_SKILL_BODY")
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "skilled",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "one", "jobs": [{"id": "j1", "prompt": "Do it", "skill": "myskill"}]}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={
                    "YOYO_CONFIG": str(config),
                    "YOYO_SKILL_PATH": str(Path(tmp) / "skills"),
                },
            )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        job_stdout = payload["phases"][0]["jobs"][0]["stdout"]
        self.assertIn('<skill name="myskill">', job_stdout)
        self.assertIn("WORKFLOW_SKILL_BODY", job_stdout)

    def test_workflow_missing_skill_fails_before_any_job_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._echo_agent_config(tmp)
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "skilled-missing",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "one", "jobs": [{"id": "j1", "prompt": "Do it"}]},
                            {"name": "two", "jobs": [{"id": "j2", "prompt": "Do it", "skill": "nope"}]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                ["workflow", str(spec), "--json"],
                env={
                    "YOYO_CONFIG": str(config),
                    "YOYO_SKILL_PATH": str(Path(tmp) / "empty-skills"),
                },
            )

        self.assertEqual(code, 2)
        self.assertIn("Skill not found", stderr)
        self.assertEqual(stdout.strip(), "")

    def test_bundled_workflow_templates_are_valid_specs(self):
        bundled = ROOT / "workflows"
        templates = sorted(bundled.glob("*.json"))
        self.assertTrue(templates, "no bundled workflow templates found")
        for template in templates:
            spec = json.loads(template.read_text(encoding="utf-8"))
            phases = yoyo.workflow_phases(spec)
            self.assertTrue(phases)
            for phase in phases:
                yoyo.normalize_phase_gates(phase)
                jobs = yoyo.expand_workflow_jobs(phase, "input")
                for job in jobs:
                    self.assertTrue(str(job.get("prompt", "")).strip())


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
        self.assertFalse(out.exists())

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

    def test_loop_stop_file_prevents_first_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, counter = self._counting_stub(tmp)
            loop_dir = Path(tmp) / ".yoyo"
            loop_dir.mkdir()
            (loop_dir / "STOP").write_text("", encoding="utf-8")
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--json", "never runs"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["iterations"], 0)
            self.assertEqual(summary["end_reason"], "stop")
            self.assertFalse(counter.exists())

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

    def test_loop_claude_flavor_envelope_cost_accumulates_until_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = (
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'result': 'did one increment', 'total_cost_usd': 0.6, "
                "'usage': {'output_tokens': 8412, 'cache_read_input_tokens': 121000, "
                "'cache_creation_input_tokens': 43000}}))\n"
            )
            config = self._claude_flavor_config(tmp, body)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["loop", "fakeclaude", "--cwd", tmp, "--max-iter", "10", "--budget-usd", "1.0", "--json", "work"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["iterations"], 2)
            self.assertEqual(summary["end_reason"], "budget")
            self.assertAlmostEqual(summary["total_cost_usd"], 1.2)
            self.assertEqual([row["cost_usd"] for row in summary["runs"]], [0.6, 0.6])
            self.assertIn("$0.60", stderr)
            self.assertIn("out=8,412", stderr)
            self.assertIn("did one increment", stderr)

    def test_loop_malformed_claude_envelope_keeps_loop_alive_with_unknown_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = "import sys\nsys.stdin.read()\nprint('this is not a json envelope')\n"
            config = self._claude_flavor_config(tmp, body)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["loop", "fakeclaude", "--cwd", tmp, "--max-iter", "2", "--json", "work"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["iterations"], 2)
            self.assertEqual(summary["end_reason"], "max-iter")
            self.assertIsNone(summary["total_cost_usd"])
            self.assertEqual([row["exit_code"] for row in summary["runs"]], [0, 0])
            self.assertIn("this is not a json envelope", stderr)

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

    def test_loop_dry_run_prompt_contains_protocol_state_path_and_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_skill(Path(tmp) / "skills", "frontend")
            code, stdout, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--skill", "frontend", "--dry-run", "Build the page."],
                env={"YOYO_SKILL_PATH": str(Path(tmp) / "skills")},
            )

            self.assertEqual(code, 0, stderr)
            self.assertIn("=== LOOP PROTOCOL (yoyo loop iteration 1/20) ===", stdout)
            self.assertIn(str(Path(tmp).resolve() / ".yoyo" / "loop-state.md"), stdout)
            self.assertIn('<skill name="frontend">', stdout)
            self.assertIn("Use 8px spacing", stdout)
            self.assertIn("TASK:\nBuild the page.", stdout)
            self.assertIn("--output-format json", stdout)
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

    def test_loop_budget_with_costless_agent_warns_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, _ = self._counting_stub(tmp)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "2", "--budget-usd", "5", "--json", "work"],
                env=env,
            )

            self.assertEqual(code, 0)
            self.assertIn("does not report cost", stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["iterations"], 2)
            self.assertEqual(summary["end_reason"], "max-iter")

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

    def test_loop_done_outranks_budget_on_the_same_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".yoyo" / "loop-state.md"
            body = (
                "import json, sys\n"
                "sys.stdin.read()\n"
                f"open({str(state)!r}, 'a').write('\\nSTATUS: DONE\\n')\n"
                "print(json.dumps({'result': 'finished and verified', 'total_cost_usd': 5.0, 'usage': {}}))\n"
            )
            config = self._claude_flavor_config(tmp, body)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["loop", "fakeclaude", "--cwd", tmp, "--budget-usd", "1.0", "--json", "finish now"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["end_reason"], "done")
            self.assertEqual(summary["iterations"], 1)
            self.assertAlmostEqual(summary["total_cost_usd"], 5.0)

    def test_loop_malformed_envelope_with_budget_warns_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = "import sys\nsys.stdin.read()\nprint('no envelope here')\n"
            config = self._claude_flavor_config(tmp, body)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}
            code, stdout, stderr = self.run_cli(
                ["loop", "fakeclaude", "--cwd", tmp, "--max-iter", "1", "--budget-usd", "1.0", "--json", "work"],
                env=env,
            )

            self.assertEqual(code, 0)
            self.assertIn("not a parseable cost envelope", stderr)
            self.assertIn("not counted toward --budget-usd", stderr)
            summary = json.loads(stdout)
            self.assertIsNone(summary["total_cost_usd"])

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


if __name__ == "__main__":
    unittest.main()
