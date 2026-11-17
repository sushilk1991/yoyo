# Long runs and quota verification

Branch: `feat/long-runs-and-quota`. No push, tag, release or installation.

All tests and trials use isolated `YOYO_STATE_DIR` directories. Linux suites
run through `boxrun` on retained Instabox `build`, scheduled by remote Baton.
Native vendor probes run locally against the installed authenticated CLIs;
credentials are not copied to the test box. Raw captures remain in the ignored
`.yoyo/long-runs/evidence/` directory. No retention or prune touches the real ledger.

## Native recovery checks (item 2)

Installed CLI output:

```text
codex-cli 0.169.0
2.1.326 (Claude Code)
```

Probe: a fresh native session receives a random word, says MEMORY_READY, then
runs `sleep 90`. The supervisor waits for that tool to start, sends SIGINT to
its process group, waits up to 10 seconds, and would escalate through SIGTERM
and SIGKILL. A fresh process resumes the captured session ID and is asked for
the word without including the word in the follow-up prompt.

Actual output:

```json
{"vendor": "codex", "session_id": "01a1ea0c-0a54-74b0-9d87-76cebaaf2752", "mid_tool": true, "interrupted_at_s": 10.6, "exit_code": 1, "result_event": false, "agent_message": true, "resume_exit": 0, "recall": true}
{"vendor": "claude", "session_id": "38fb51c7-a3ca-4a0a-8c57-988c226e615a", "mid_tool": true, "interrupted_at_s": 7.54, "exit_code": 0, "result_event": true, "agent_message": true, "resume_exit": 0, "recall": true}
```

Both recall checks passed. Claude emitted a final result event on SIGINT;
Codex exited 1 without a final result, but its completed agent message was
already captured. Recovery therefore cannot depend on a final result event.
The first Claude attempt failed before starting because `--mcp-config {}` is
invalid; the successful attempt used `{"mcpServers":{}}`.

The documented Claude signal contract was checked against the
[official headless guide](https://code.claude.com/docs/en/headless#stop-a-run-with-sigterm).
The quota pipe and listing filters were checked against the
[official Codex app-server reference](https://learn.chatgpt.com/docs/app-server).

Codex listing readback (native app-server, same test cwd):

```json
{
  "vendor": "codex",
  "session_id": "01a1ea0c-0a54-74b0-9d87-76cebaaf2752",
  "default_interactive_listing_contains_probe": false,
  "exec_source_listing_contains_probe": true
}
```

Claude native project picker output (`claude --safe-mode --resume`, captured
through a headless PTY in the test cwd after accepting that test folder):

```text
Resume session
No conversations found in this project.
Ctrl+A to show all projects
```

The probe transcript exists in the native project directory, and explicit-ID
resume succeeds. For this CLI/version and safe-mode probe, the project picker
did not add a visible conversation. No claim is made about other picker modes.

Implementation and gate results follow as each item is completed.

## Item 1 - renewable default deadline

Tests added: `SoftTimeoutTests` (9): foreground and background renewal,
quiet soft timeout, absolute ceiling, explicit flag/env hard deadlines,
buffered hard default, propagated explicitness and audit clock separation.
Red: 9 tests, 3 failures and 6 errors. Green: 9 tests, 8.090s, OK.

Full remote gate output:

```text
boxrun: build:/data/workspace/yoyo-2c067b94/
.......................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 535 tests in 52.861s

OK
.........
----------------------------------------------------------------------
Ran 9 tests in 0.072s

OK
```

The remote shell's fixed `XDG_CONFIG_HOME` is unset for tests so fixture HOME
isolation works. Three existing skill-integrity tests now mock vendor binary
presence, matching adjacent fixtures; their skill assertions are unchanged.
The Linux box has no authenticated native vendor CLIs. No smaller deadline
behavior shipped. The ceiling bounds the implicit renewable policy; explicit
caller-selected budgets retain their selected hard limit.
