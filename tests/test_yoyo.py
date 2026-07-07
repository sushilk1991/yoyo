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

    def test_version_outputs_current_release(self):
        code, stdout, stderr = self.run_cli(["--version"])

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout.strip(), "yoyo 0.18.2")

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
        # With YOYO_DEFAULT_SKILLS unset entirely, the bundled fable-mode
        # skill resolves from the repo's own skills dir and rides every call.
        env = {
            "YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\"",
            "YOYO_DEFAULT_SKILLS": None,
        }
        code, stdout, stderr = self.run_cli(["ask", "echo", "hello"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertIn('<skill name="fable-mode">', stdout)
        self.assertIn("Done Gate", stdout)
        self.assertIn("Task:\nhello", stdout)

    def test_default_skills_empty_string_disables_injection(self):
        env = {
            "YOYO_AGENT_ECHO": "python3 -c \"import sys; sys.stdout.write(sys.stdin.read())\"",
            "YOYO_DEFAULT_SKILLS": "",
        }
        code, stdout, stderr = self.run_cli(["ask", "echo", "hello"], env=env)

        self.assertEqual(code, 0, stderr)
        self.assertNotIn("<skill", stdout)

    def test_user_installed_skill_overrides_bundled_fable_mode(self):
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
        self.assertIn("custom-override-marker", stdout)
        self.assertNotIn("Done Gate", stdout)

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

        self.assertEqual(ask_args.timeout, 14400.0)
        self.assertEqual(workflow_args.timeout, 14400.0)

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

    def test_ask_defaults_to_full_access_for_cursor(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "cursor", "--dry-run", "Do it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("cursor-agent -p --output-format text --trust --force", stdout)
        self.assertIn("mode=full-access delegation", stdout)

    def test_ask_read_only_constrains_cursor(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "cursor", "--dry-run", "--read-only", "Review it."],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("cursor-agent -p --output-format text --trust --mode plan", stdout)
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
        self.assertIn("--dangerously-skip-permissions", stdout)
        self.assertIn("mode=full-access delegation", stdout)

    def test_ask_read_only_rejects_agy(self):
        code, stdout, stderr = self.run_cli(
            ["ask", "agy", "--dry-run", "--read-only", "Review it."],
        )

        # agy has no headless read-only mode, so --read-only must fail loudly.
        self.assertEqual(code, 2)
        self.assertIn("no headless read-only mode", stderr)

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
        self.assertIn("grok --prompt-file /dev/stdin --output-format plain --permission-mode plan", stdout)
        self.assertIn("mode=read-only delegation", stdout)

    def test_on_demand_agents_pass_model_flag(self):
        for agent, model in (("cursor", "sonnet-4"), ("agy", "gemini-3.1-pro"), ("grok", "grok-4")):
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

    def test_chat_grok_rejects_initial_prompt(self):
        code, stdout, stderr = self.run_cli(
            ["chat", "grok", "--dry-run", "hello"],
        )

        self.assertEqual(code, 2)
        self.assertIn("positional prompt", stderr)

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

    def test_workflow_background_detaches_and_wait_renders_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "agents.json"
            config.write_text(
                json.dumps({"agents": {"echo": {"command": ["python3", "-c", "import sys; sys.stdin.read(); print('job output')"], "read_only_args": ["--safe"]}}}),
                encoding="utf-8",
            )
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "bg-smoke",
                        "defaults": {"agent": "echo"},
                        "phases": [{"name": "p1", "jobs": [{"id": "j1", "prompt": "Say hello"}]}],
                    }
                ),
                encoding="utf-8",
            )
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_CONFIG": str(config)}

            code, stdout, stderr = self.run_cli(["workflow", str(spec), "--background"], env=env)
            self.assertEqual(code, 0, stderr)
            run_id = stdout.strip()
            self.assertRegex(run_id, r"^\d{8}T\d{6}-[0-9a-f]{8}$")

            code, stdout, stderr = self.run_cli(["wait", run_id, "--timeout", "15", "--poll", "0.05"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("=== p1/j1 ===", stdout)
            self.assertIn("job output", stdout)

            meta = json.loads((Path(tmp) / "state" / "runs" / run_id / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["agent"], "workflow:bg-smoke")
            self.assertTrue(meta["trace_id"])

            code, stdout, stderr = self.run_cli(["runs", "show", run_id, "--json"], env=env)
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["workflow"], "bg-smoke")
            self.assertEqual(payload["phases"][0]["jobs"][0]["job_id"], "j1")

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
        self.assertNotIn("stderr_plain", payload)

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

    def test_imagegen_codex_delegates_to_builtin_image_gen_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "art.png"
            code, stdout, stderr = self.run_cli(
                ["imagegen", "a red yo-yo on white, flat vector, no other text",
                 "--out", str(out), "--size", "1024x1024", "--quality", "low", "--dry-run"],
                env={"YOYO_CONFIG": str(Path(tmp) / "missing.json"), "OPENAI_API_KEY": ""},
            )
        self.assertEqual(code, 0, stderr)
        # Agent delegation to codex exec, driving the built-in image_gen tool.
        self.assertIn("exec", stdout)
        self.assertIn("built-in image_gen tool", stdout)
        self.assertIn("Do NOT draw or render the image with code", stdout)
        self.assertIn(str(out), stdout)
        # The API-key CLI fallback must be explicitly forbidden, not used.
        self.assertIn("do NOT use the scripts/image_gen.py CLI fallback", stdout)
        self.assertNotIn("--no-augment", stdout)

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
            self.assertIn("=== LOOP PROTOCOL (yoyo loop) ===", stdout)
            self.assertIn("Loop position: iteration 1 of at most 20.", stdout)
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
            self.assertIn("do not report cost", stderr)
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

    def test_loop_gate_rejects_self_declared_done_and_strips_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".yoyo" / "loop-state.md"
            command = self._done_each_call_stub(tmp, state)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "3", "--gate", "exit 1", "--json", "do the work"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            # A failing gate never lets the self-declared DONE end the loop.
            self.assertEqual(summary["end_reason"], "max-iter")
            self.assertEqual(summary["done_policy"], "gate")
            self.assertFalse(summary["verified"])
            self.assertEqual(summary["gate_failures"], 3)
            self.assertEqual(summary["iterations"], 3)
            # The false DONE line was stripped (the rejection text mentions the
            # phrase, but no standalone STATUS: DONE line — what the loop checks —
            # survives) and the rejection was recorded for the next iteration.
            final_state = state.read_text(encoding="utf-8")
            self.assertFalse(any(line.strip() == "STATUS: DONE" for line in final_state.splitlines()))
            self.assertIn("VERIFICATION REJECTED", final_state)

    def test_loop_gate_accepts_done_when_gate_passes(self):
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
                ["loop", "stub", "--cwd", tmp, "--max-iter", "10", "--gate", "exit 0", "--json", "finish in two"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["end_reason"], "done")
            self.assertTrue(summary["verified"])
            self.assertEqual(summary["done_policy"], "gate")
            self.assertEqual(summary["gate_failures"], 0)
            self.assertEqual(summary["iterations"], 2)

    def test_loop_queue_rejects_done_while_items_unchecked(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".yoyo" / "loop-state.md"
            queue = Path(tmp) / "tasks.md"
            queue.write_text("# work\n- [ ] first item\n- [x] already done\n- [ ] second item\n", encoding="utf-8")
            command = self._done_each_call_stub(tmp, state)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "3", "--queue", "tasks.md", "--json", "work the queue"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            # DONE claims never end the loop while boxes stay unchecked.
            self.assertEqual(summary["end_reason"], "max-iter")
            self.assertEqual(summary["queue_rejections"], 3)
            self.assertEqual(Path(summary["queue"]).resolve(), queue.resolve())
            # The worker owns the queue file, so a queue alone is not
            # independent verification.
            self.assertFalse(summary["verified"])
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
            self.assertIn("the work queue", stdout)  # verifiers label in COMPLETION CHECK
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

    def test_workflow_json_slims_nested_job_results(self):
        payload = {
            "phases": [
                {"name": "p1", "jobs": [{"agent": "a", "stdout": "hi", "stderr": "raw", "stderr_plain": ""}]}
            ],
        }
        slim = yoyo.slim_result_for_emission(payload)
        job = slim["phases"][0]["jobs"][0]
        self.assertNotIn("stderr_plain", job)
        self.assertEqual(job["stderr"], "")

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

    def test_loop_gate_does_not_run_until_done_is_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The worker never claims done; the gate writes a marker if it ever runs.
            command, _ = self._counting_stub(tmp)
            marker = Path(tmp) / "gate-ran"
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, stdout, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--max-iter", "2", "--gate", f"touch {shlex.quote(str(marker))}", "--json", "never done"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["end_reason"], "max-iter")
            self.assertEqual(summary["gate_failures"], 0)
            self.assertFalse(marker.exists(), "gate must not run when STATUS: DONE was never claimed")

    def test_loop_done_policy_gate_without_gate_command_errors(self):
        code, _, stderr = self.run_cli(["loop", "claude", "--done-policy", "gate", "do it"])
        self.assertEqual(code, 2)
        self.assertIn("no --gate command", stderr)

    def test_loop_explicit_worker_policy_with_gate_is_a_conflict(self):
        code, _, stderr = self.run_cli(["loop", "claude", "--done-policy", "worker", "--gate", "exit 0", "do it"])
        self.assertEqual(code, 2)
        self.assertIn("ignores gates", stderr)

    def test_loop_checker_rejects_then_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".yoyo" / "loop-state.md"
            worker = self._done_each_call_stub(tmp, state)
            checker_counter = Path(tmp) / "checks.txt"
            checker_body = (
                "import os, sys\n"
                f"counter = {str(checker_counter)!r}\n"
                "sys.stdin.read()\n"
                "n = int(open(counter).read()) if os.path.exists(counter) else 0\n"
                "n += 1\n"
                "open(counter, 'w').write(str(n))\n"
                "print('missing tests' if n == 1 else 'looks complete')\n"
                "print('VERDICT: FAIL' if n == 1 else 'VERDICT: PASS')\n"
            )
            config = self._checker_config(tmp, checker_body)
            env = {
                "YOYO_STATE_DIR": str(Path(tmp) / "state"),
                "YOYO_AGENT_WORKER": worker,
                "YOYO_CONFIG": str(config),
            }
            code, stdout, stderr = self.run_cli(
                ["loop", "worker", "--cwd", tmp, "--max-iter", "5", "--checker", "checkbot", "--json", "build it"],
                env=env,
            )

            self.assertEqual(code, 0, stderr)
            summary = json.loads(stdout)
            self.assertEqual(summary["end_reason"], "done")
            self.assertTrue(summary["verified"])
            self.assertEqual(summary["done_policy"], "checker")
            self.assertEqual(summary["checker_rejections"], 1)
            self.assertEqual(summary["iterations"], 2)
            self.assertEqual(checker_counter.read_text(encoding="utf-8"), "2")

    def test_loop_checker_unknown_agent_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            command, _ = self._counting_stub(tmp)
            env = {"YOYO_STATE_DIR": str(Path(tmp) / "state"), "YOYO_AGENT_STUB": command}
            code, _, stderr = self.run_cli(
                ["loop", "stub", "--cwd", tmp, "--checker", "nope", "do it"],
                env=env,
            )
            self.assertEqual(code, 2)
            self.assertIn("Unknown checker agent", stderr)

    def test_loop_dry_run_shows_spec_block_and_completion_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "VISION.md"
            spec.write_text("Never touch src/payments/.", encoding="utf-8")
            code, stdout, stderr = self.run_cli(
                ["loop", "claude", "--cwd", tmp, "--spec", "VISION.md", "--gate", "pytest -q", "--dry-run", "Build the page."],
                env={},
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("STANDING SPEC", stdout)
            self.assertIn("Never touch src/payments/.", stdout)
            self.assertIn("COMPLETION CHECK", stdout)
            self.assertIn("pytest -q", stdout)

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

    def test_cron_add_list_rm_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, store = self._cron_env(tmp)
            code, stdout, stderr = self.run_cli(
                [
                    "cron", "add", "nightly", "--schedule", "0 2 * * *", "--cwd", tmp,
                    "--", "loop", "claude", "Work through TODO.md",
                ],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            line = store.read_text().strip()
            self.assertTrue(line.startswith("0 2 * * *"), line)
            self.assertIn("loop claude", line)
            self.assertIn("YOYO_CALLER=cron", line)
            self.assertTrue(line.endswith("# yoyo-cron:nightly"), line)

            code, stdout, stderr = self.run_cli(["cron", "list", "--json"], env=env)
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(len(payload["entries"]), 1)
            entry = payload["entries"][0]
            self.assertEqual(entry["name"], "nightly")
            self.assertEqual(entry["schedule"], "0 2 * * *")
            self.assertTrue(entry["installed"])
            self.assertEqual(payload["untracked_crontab_names"], [])

            code, stdout, stderr = self.run_cli(["cron", "rm", "nightly"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertNotIn("yoyo-cron:nightly", store.read_text())

            code, stdout, _ = self.run_cli(["cron", "list", "--json"], env=env)
            self.assertEqual(json.loads(stdout)["entries"], [])

    def test_cron_add_validates_name_schedule_and_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, _ = self._cron_env(tmp)
            code, _, stderr = self.run_cli(["cron", "add", "x", "--", "loop", "claude", "t"], env=env)
            self.assertEqual(code, 2)
            self.assertIn("requires --schedule", stderr)

            code, _, stderr = self.run_cli(
                ["cron", "add", "x", "--schedule", "1 2 3", "--", "loop", "claude", "t"], env=env
            )
            self.assertEqual(code, 2)
            self.assertIn("five cron fields", stderr)

            code, _, stderr = self.run_cli(
                ["cron", "add", "x", "--schedule", "@daily", "--", "rm", "-rf", "/"], env=env
            )
            self.assertEqual(code, 2)
            self.assertIn("must start with a yoyo subcommand", stderr)

            code, _, stderr = self.run_cli(["cron", "add", "x", "--schedule", "@daily"], env=env)
            self.assertEqual(code, 2)
            self.assertIn("after '--'", stderr)

    def test_cron_add_duplicate_requires_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, store = self._cron_env(tmp)
            argv = ["cron", "add", "job", "--schedule", "@daily", "--cwd", tmp, "--", "ask", "claude", "hello"]
            code, _, stderr = self.run_cli(argv, env=env)
            self.assertEqual(code, 0, stderr)

            code, _, stderr = self.run_cli(argv, env=env)
            self.assertEqual(code, 2)
            self.assertIn("already exists", stderr)

            forced = ["cron", "add", "job", "--schedule", "@daily", "--cwd", tmp, "--force", "--", "ask", "claude", "hello"]
            code, _, stderr = self.run_cli(forced, env=env)
            self.assertEqual(code, 0, stderr)
            # Replaced, not duplicated: exactly one tagged line remains.
            tagged = [line for line in store.read_text().splitlines() if line.endswith("# yoyo-cron:job")]
            self.assertEqual(len(tagged), 1)

    def test_cron_run_executes_recorded_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, _ = self._cron_env(tmp)
            code, _, stderr = self.run_cli(
                ["cron", "add", "job", "--schedule", "@hourly", "--cwd", tmp, "--", "ask", "claude", "hi"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)

            recorded = Path(tmp) / "ran.txt"
            fake_yoyo = Path(tmp) / "bin" / "fake-yoyo"
            fake_yoyo.write_text(f"#!/bin/sh\necho \"$@\" > {recorded}\n", encoding="utf-8")
            fake_yoyo.chmod(0o755)
            registry = Path(tmp) / "state" / "cron.json"
            data = json.loads(registry.read_text())
            data["entries"]["job"]["yoyo"] = str(fake_yoyo)
            registry.write_text(json.dumps(data), encoding="utf-8")

            code, _, stderr = self.run_cli(["cron", "run", "job"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(recorded.read_text().strip(), "ask claude hi")

    def test_cron_line_escapes_percent_for_crontab(self):
        line = yoyo.build_cron_line(
            {
                "name": "pct",
                "schedule": "@daily",
                "cwd": "/tmp",
                "argv": ["ask", "claude", "is this 100% done?"],
                "log": "/tmp/x.log",
                "yoyo": "/usr/local/bin/yoyo",
                "path_env": "/usr/bin",
            }
        )
        self.assertIn(r"100\%", line)
        self.assertTrue(line.endswith("# yoyo-cron:pct"))

    def test_cron_line_carries_captured_yoyo_env(self):
        line = yoyo.build_cron_line(
            {
                "name": "envy",
                "schedule": "@daily",
                "cwd": "/tmp",
                "argv": ["ask", "claude", "hi"],
                "log": "/tmp/x.log",
                "yoyo": "/usr/local/bin/yoyo",
                "path_env": "/usr/bin",
                "env": {"YOYO_DEFAULT_SKILLS": "discipline", "IGNORED_KEY": "nope"},
            }
        )
        self.assertIn("YOYO_DEFAULT_SKILLS=discipline", line)
        self.assertNotIn("IGNORED_KEY", line)
        self.assertLess(line.index("YOYO_DEFAULT_SKILLS"), line.index("YOYO_CALLER=cron"))

    def test_cron_add_captures_default_skills_env_into_crontab_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "discipline"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Discipline\nBe rigorous.\n", encoding="utf-8")
            env, store = self._cron_env(tmp)
            env["YOYO_DEFAULT_SKILLS"] = "discipline"
            env["YOYO_SKILL_PATH"] = str(Path(tmp) / "skills")
            code, _, stderr = self.run_cli(
                ["cron", "add", "envy", "--schedule", "@daily", "--cwd", tmp, "--", "ask", "claude", "hi"],
                env=env,
            )
            self.assertEqual(code, 0, stderr)
            line = store.read_text().strip()
            self.assertIn("YOYO_DEFAULT_SKILLS=discipline", line)
            self.assertIn("YOYO_SKILL_PATH=", line)
            self.assertLess(line.index("YOYO_DEFAULT_SKILLS"), line.index("YOYO_CALLER=cron"))

    def test_cron_add_rejects_newlines_in_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, store = self._cron_env(tmp)
            code, _, stderr = self.run_cli(
                ["cron", "add", "nl", "--schedule", "@daily", "--cwd", tmp, "--", "ask", "claude", "line one\nline two"],
                env=env,
            )
            self.assertEqual(code, 2)
            self.assertIn("single line", stderr)
            self.assertFalse(store.exists())

    def test_workflow_removed_expect_field_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._echo_agent_config(tmp)
            spec = Path(tmp) / "workflow.json"
            spec.write_text(
                json.dumps(
                    {
                        "name": "legacy",
                        "defaults": {"agent": "echo"},
                        "phases": [
                            {"name": "one", "jobs": [
                                {"id": "j1", "prompt": "First", "expect": {"contains": ["X"]}}
                            ]}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, _, stderr = self.run_cli(["workflow", str(spec)], env={"YOYO_CONFIG": str(config)})

            self.assertEqual(code, 2)
            self.assertIn("removed in 0.15.0", stderr)

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


if __name__ == "__main__":
    unittest.main()
