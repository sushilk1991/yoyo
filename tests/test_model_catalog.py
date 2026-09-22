import json
import os
import tempfile
from pathlib import Path

from test_yoyo import CliTestCase


class ModelCatalogTests(CliTestCase):
    def test_probe_timeout_cleans_up(self):
        # A stalled metadata process has a short independent deadline and must
        # be reaped; it cannot remain as a background service after discovery.
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "codex"
            pid_path = Path(tmp) / "pid"
            executable.write_text(
                "#!/usr/bin/env python3\nimport os, time\n"
                f"open({str(pid_path)!r}, 'w').write(str(os.getpid()))\n"
                "time.sleep(10)\n")
            executable.chmod(0o755)
            code, stdout, stderr = self.run_cli(
                ["models", "codex", "--cwd", tmp, "--timeout", "1", "--json"],
                env={"PATH": tmp + os.pathsep + os.environ["PATH"], "YOYO_CONFIG": tmp + "/absent.json"},
            )
            self.assertEqual(code, 2)
            self.assertIn("timed out", stderr)
            self.assertEqual(stdout, "")
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_path.read_text()), 0)

    def test_custom_cli_is_not_probed(self):
        code, stdout, stderr = self.run_cli(
            ["models", "codex", "--json"], env={"YOYO_AGENT_CODEX": "false"})
        self.assertEqual(code, 2)
        self.assertIn("custom CLI", stderr)
        self.assertEqual(stdout, "")

    def test_empty_reply_fails(self):
        # A changed native schema cannot silently look like an empty but valid
        # subscription catalog. Keep diagnostics free of the raw account reply.
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "claude"
            executable.write_text(
                "#!/usr/bin/env python3\nimport json, sys\n"
                "msg = json.loads(sys.stdin.readline())\n"
                "print(json.dumps({'type': 'control_response', 'response': {'subtype': 'success',\n"
                "'request_id': msg['request_id'], 'response': {'models': None, 'email': 'private@example.com'}}}), flush=True)\n")
            executable.chmod(0o755)
            code, stdout, stderr = self.run_cli(
                ["models", "claude", "--cwd", tmp, "--json"],
                env={"PATH": tmp + os.pathsep + os.environ["PATH"], "YOYO_CONFIG": tmp + "/absent.json"},
            )

        self.assertEqual(code, 2, stderr)
        self.assertIn("model", stderr.lower())
        self.assertNotIn("private@example.com", stdout + stderr)

    def test_claude_catalog(self):
        # Initialization returns aliases after native settings are applied;
        # preserving the alias avoids guessing vendor-specific deployment IDs.
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "claude"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "if sys.argv[1:] == ['auth', 'status']:\n"
                "    print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',\n"
                "                      'subscriptionType': 'max', 'email': 'private@example.com'}))\n"
                "    sys.exit(0)\n"
                "assert '--strict-mcp-config' in sys.argv\n"
                "assert sys.argv[sys.argv.index('--tools') + 1] == ''\n"
                "msg = json.loads(sys.stdin.readline())\n"
                "assert msg['request']['subtype'] == 'initialize'\n"
                "payload = {'models': [{'value': 'sonnet', 'resolvedModel': 'account-sonnet',\n"
                "    'displayName': 'Sonnet', 'description': 'Efficient', 'supportedEffortLevels': ['low', 'high']}],\n"
                "    'account': {'subscriptionType': 'Claude Max', 'email': 'private@example.com'}}\n"
                "print(json.dumps({'type': 'control_response', 'response': {'subtype': 'success',\n"
                "    'request_id': msg['request_id'], 'response': payload}}), flush=True)\n"
                "assert not sys.stdin.readline(), 'unexpected inference request'\n"
            )
            executable.chmod(0o755)
            code, stdout, stderr = self.run_cli(
                ["models", "claude", "--cwd", tmp, "--json"],
                env={"PATH": tmp + os.pathsep + os.environ["PATH"], "YOYO_CONFIG": tmp + "/absent.json"},
            )

        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["account"]["plan"], "max")
        self.assertEqual(result["account"]["type"], "claude.ai")
        self.assertEqual(result["models"][0]["model"], "sonnet")
        self.assertEqual(result["models"][0]["resolved_model"], "account-sonnet")
        self.assertNotIn("private@example.com", stdout + stderr)

    def test_codex_catalog(self):
        # The native protocol supplies account-scoped choices, including pages;
        # listing them must never start an inference or reveal account identity.
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "codex"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "assert sys.argv[1:] == ['app-server']\n"
                "for line in sys.stdin:\n"
                "    msg = json.loads(line)\n"
                "    method = msg['method']\n"
                "    if method == 'initialized': continue\n"
                "    if method == 'initialize': result = {}\n"
                "    elif method == 'account/read':\n"
                "        result = {'account': {'type': 'chatgpt', 'planType': 'pro', 'email': 'private@example.com'}}\n"
                "    elif method == 'model/list':\n"
                "        cursor = msg['params'].get('cursor')\n"
                "        result = {'data': [{'model': 'account-model-b' if cursor else 'account-model-a',\n"
                "                   'displayName': 'Available', 'description': 'From this account',\n"
                "                   'isDefault': not cursor}], 'nextCursor': None if cursor else 'page2'}\n"
                "    else: raise RuntimeError('unexpected inference request')\n"
                "    print(json.dumps({'id': msg['id'], 'result': result}), flush=True)\n"
            )
            executable.chmod(0o755)
            code, stdout, stderr = self.run_cli(
                ["models", "codex", "--cwd", tmp, "--json"],
                env={"PATH": tmp + os.pathsep + os.environ["PATH"], "YOYO_CONFIG": tmp + "/absent.json"},
            )

        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["account"], {"type": "chatgpt", "plan": "pro"})
        self.assertEqual([row["model"] for row in result["models"]], ["account-model-a", "account-model-b"])
        self.assertNotIn("private@example.com", stdout + stderr)


if __name__ == "__main__":
    import unittest
    unittest.main()
