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

## Item 2 - interrupted answers and continuation

Tests added: `InterruptedRunTests` (12): last complete native message on
kill, automatic full-access sessions and ephemeral read-only calls, graceful
Claude final result, Yoyo and agent signals, continuation identity/permission
validation, background wait/autopsy, fresh internal calls, Codex JSON watch,
invalid grace clocks before spawn and detached-parent signal recovery.
Initial red: 4 selected tests, 4 failures and 1 error. Focused final green:
22 tests (including lifecycle/skill guards), 7.210s, OK.

Actual current-Yoyo adapter output (each receives a random word, starts sleep90,
hits explicit25s timeout, then resumes by run ID with no word in follow-up):

```json
{"vendor": "codex", "timeout_exit": 124, "has_answer": true, "continue_command": "env YOYO_STATE_DIR=/Users/sushil/Code/yoyo/.yoyo/long-runs/adapter-state-codex YOYO_CONFIG=/Users/sushil/Code/yoyo/.yoyo/long-runs/adapter-codex/no-config.json /opt/homebrew/opt/python@3.15/bin/python3.15 /Users/sushil/Code/yoyo/bin/yoyo ask codex --cwd /Users/sushil/Code/yoyo/.yoyo/long-runs/adapter-codex --resume-run 20261117T153844-6168aa82 'Continue the interrupted task.'", "run_id": "20261117T153844-6168aa82", "resume_exit": 0, "recall": true, "named_registry_created": false}
{"vendor": "claude", "timeout_exit": 124, "has_answer": true, "continue_command": "env YOYO_STATE_DIR=/Users/sushil/Code/yoyo/.yoyo/long-runs/adapter-state-claude YOYO_CONFIG=/Users/sushil/Code/yoyo/.yoyo/long-runs/adapter-claude/no-config.json /opt/homebrew/opt/python@3.15/bin/python3.15 /Users/sushil/Code/yoyo/bin/yoyo ask claude --cwd /Users/sushil/Code/yoyo/.yoyo/long-runs/adapter-claude --resume-run 20261117T153920-621a7207 'Continue the interrupted task.'", "run_id": "20261117T153920-621a7207", "resume_exit": 0, "recall": true, "named_registry_created": false}
```

Both actual deadline paths retained an answer and resumed successfully. Codex
uses immediate group kill because the earlier SIGINT probe emitted no final
result; Claude uses bounded SIGINT, then TERM/KILL escalation. Yoyo fatal
signals also allow that Claude flush before recovery. Named-session registry
was absent after both calls. No smaller recovery behavior shipped. Sessions
remain vendor-owned; picker observations above are limited to tested versions.
A SIGKILL of Yoyo itself cannot flush an active journal.

Full remote gate output (`python3 -m py_compile bin/yoyo` was silent, exit0):

```text
boxrun: build:/data/workspace/yoyo-2c067b94/
...................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 547 tests in 55.837s

OK
.........
----------------------------------------------------------------------
Ran 9 tests in 0.062s

OK
```

## Item 3 - quota visibility and classification

Tests added: `QuotaTests` (13): native pipe allowlisting, newest Claude event
and age, three real terminal error formats, explicit Codex allowance and
unknown numeric values, minimal Claude events and expiry, retained sanitized
snapshots, terminal error/clock precedence, custom adapters and transport
failures, model-pipe reuse, structured Claude error arrays, progress followed
by terminal stderr, raw-stream bounds, adapter provenance and malformed dates.
Initial red: 3 tests, 2 failures and 1 error (missing quota command/metadata).
Expanded green: 13 tests, 1.053s, OK.

Actual `yoyo quota codex claude pi --json` output, using only the isolated
Claude adapter-trial ledger for observations:

```json
[{"agent": "codex", "source": "live", "observed_at": "2026-11-17T15:59:48.040385Z", "age_s": 0, "windows": [{"name": "primary", "used_percent": 44, "resets_at": "2026-11-18T06:46:07Z"}], "blocked": false, "resets_at": null}, {"agent": "claude", "source": "observed", "observed_at": "2026-11-17T15:39:51.761714Z", "windows": [{"name": "seven_day", "used_percent": 10.0, "resets_at": "2026-11-23T21:00:00Z"}, {"name": "seven_day_overage_included", "used_percent": 2.0, "resets_at": "2026-11-23T21:00:00Z"}], "blocked": false, "resets_at": "2026-11-23T21:00:00Z", "age_s": 1196.3}, {"agent": "pi", "source": "unknown", "observed_at": null, "age_s": null, "windows": [], "blocked": null, "resets_at": null}]
```

The Claude observation time uses capture mtime, an approximation rather than
a server event timestamp; age and source are explicit. Native quota failure
leaves a model catalog available with unknown quota. Full native overrides
remain unknown to avoid probing a different account. Claude's structured
terminal error array was verified against the
[official SDK result type](https://code.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage).

The remote build lane was occupied. Disposable test-box creation was rejected
at the account's five-box limit; nothing was created. The user confirmed remote
suites only and authorized starting another existing machine. The old video
verification box failed the disk preflight and was restored to suspended state.
The retained `masonry` test box has a separate YoYo checkout for later items;
its files and services are preserved. Item3's already-running batch completed
on `build`. No local suite ran.

Full remote gate output (repetitive scheduler wait lines omitted; these are
outside the gate commands; compile was silent, exit0):

```text
boxrun: build:/data/workspace/yoyo-2c067b94/
.............
----------------------------------------------------------------------
Ran 13 tests in 1.053s

OK
................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 560 tests in 59.317s

OK
.........
----------------------------------------------------------------------
Ran 9 tests in 0.066s

OK
```

## Item 4 - explicit fallback and route aliases

Tests added: `FallbackTests` (14), `RouteDoctorTests` (6). Coverage includes
quota-only fallback, no ordinary/throttle/timeout retry, blocked/expired/all
blocked choices, exact models, one prompt render with stdin and file content,
read-only propagation, direct and alias background/wait, flag/config
validation, raw-profile account isolation, once-per-vendor catalogs, resolved
IDs, unavailable catalogs, strict warnings and valid live JSON output.
Routing red: 4 failures in0.037s. Doctor red: 4 failures among5 tests in0.030s.
Final focused25 (including5 existing catalog tests):2.339s, OK.

Routing rejects --raw, named sessions, resume-run, threads and fan-out because
those have incompatible prompt or conversation identity contracts. Aliases
reject model/raw-argument overrides. Direct routing with raw arguments keeps
them but treats launch-time quota as unknown, avoiding a default-account probe
for a potentially different profile. No ordinary call auto-routes or probes.
Doctor warnings also fail --strict; live JSON keeps its list shape and writes
warnings to stderr. No live quota exhaustion was induced; proof uses fake
agents as specified. No model table or unnamed model substitution shipped.

Full remote gate output (`python3 -m py_compile bin/yoyo`: silent, exit0):

```text
boxrun: masonry:/data/workspace/yoyo-2c067b94/
.........................
----------------------------------------------------------------------
Ran 25 tests in 2.339s

OK
....................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 580 tests in 56.547s

OK
.........
----------------------------------------------------------------------
Ran 9 tests in 0.057s

OK
```
