import importlib.util
import importlib.machinery
import io
import json
import os
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
        def fake_run(cmd, prompt, cwd, stdout_path, stderr_path, timeout):
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


if __name__ == "__main__":
    unittest.main()
