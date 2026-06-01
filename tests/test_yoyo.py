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

    def test_chat_builds_interactive_command(self):
        code, stdout, stderr = self.run_cli(
            ["chat", "claude", "--dry-run", "--model", "haiku", "Debug this."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("claude", stdout)
        self.assertIn("--permission-mode bypassPermissions", stdout)
        self.assertIn("--model haiku", stdout)
        self.assertIn("'Debug this.'", stdout)

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
