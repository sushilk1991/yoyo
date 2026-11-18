# Long runs and quota verification

Branch: `feat/long-runs-and-quota`. No push, tag, release or installation.

Commits: item1 `3b221dd`; item2 `370441b`; item3 `9b3fec4`;
item4 `35be36e`; item5 `81d4a3d`. Item6 is the final commit of this branch.

All tests and trials use isolated `YOYO_STATE_DIR` directories. Linux suites
run through `boxrun` on retained Instabox `build` (items1–3) and `masonry`
(items4–6), scheduled by remote Baton.
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

Implementation and gate results follow.

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


## Item 5 - reported tokens and audit percentiles

Tests added: `TokenUsageTests` (6) and `TokenAuditTests` (3). They cover final
Claude usage with both cache components; Codex structured usage and grouped
human trailers; absent/invalid/zero usage; interrupted journals; finite bounded
stream snapshots; provider-model precedence; agent/model percentiles; sample
denominators; and no parent/mirror double counting. Initial parser red:4 tests,
3 errors; initial audit red:3 tests, 3 errors. Combined focused green, including
12 existing audit tests:21 tests,1.256s, OK.

Actual isolated read-only Yoyo calls, one per native vendor, comparing both the
JSON envelope and saved result against the native final usage:

```json
{"vendor": "codex", "exit_code": 0, "native_usage": {"input_tokens": 36622, "cached_input_tokens": 7040, "cache_write_input_tokens": 0, "output_tokens": 6, "reasoning_output_tokens": 0}, "recorded_tokens": {"input": 36622, "output": 6, "cached": 7040, "total": 36628}, "expected_total": 36628, "matches": true, "journal_matches": true}
{"vendor": "claude", "exit_code": 0, "native_usage": {"input_tokens": 2, "cache_creation_input_tokens": 14266, "cache_read_input_tokens": 3223, "output_tokens": 12, "output_tokens_details": {"thinking_tokens": 0}, "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}, "service_tier": "standard", "cache_creation": {"ephemeral_1h_input_tokens": 14266, "ephemeral_5m_input_tokens": 0}, "inference_geo": "global", "iterations": [{"input_tokens": 2, "output_tokens": 12, "cache_read_input_tokens": 3223, "cache_creation_input_tokens": 14266, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 14266}, "type": "message"}], "speed": "standard", "fallback_credit": null}, "recorded_tokens": {"input": 17491, "output": 12, "cached": 3223, "total": 17503}, "expected_total": 17503, "matches": true, "journal_matches": true}
```

Both totals matched: Codex36,628 and Claude17,503. Claude input includes cache
creation and reads; cached reads are a subset and are not added to total twice.
No inference is made for an unreported model or token component. No dollar
figures are recorded.

These are CLI-reported usage snapshots, not a promise of exact per-call billing.
The [current Codex implementation](https://github.com/openai/codex/blob/main/codex-rs/exec/src/event_processor_with_jsonl_output.rs)
uses cumulative totals, so the latest completion wins and resumed counts can
include earlier thread usage. Claude's [result usage definition](https://code.claude.com/docs/en/agent-sdk/cost-tracking#get-the-total-cost-of-a-query)
covers its main loop and excludes subagents. The implementation follows the
brief's named final events and documents those provider limits. Answer-size
limits do not truncate usage parsing; unflushed interrupted usage stays unknown.

Full remote gate output (`python3 -m py_compile bin/yoyo`: silent, exit0):

```text
boxrun: masonry:/data/workspace/yoyo-2c067b94/
.....................
----------------------------------------------------------------------
Ran 21 tests in 1.256s

OK
.............................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 589 tests in 59.349s

OK
.........
----------------------------------------------------------------------
Ran 9 tests in 0.064s

OK
```


## Item 6 - daily raw-stream retention

Tests added: `RetentionTests` (12): default30-day cleanup and preserved audit;
recent records; live PID with/without final result and legacy identity; reused
PID; disabled/invalid settings; same UTC day and next-day scans; nonblocking
lock contention; malformed metadata and ambiguous terminal proof; symlinked
directories, metadata, lock, marker and ledger root; nonregular streams;
per-file deletion failure; no maintenance during fatal-signal flushing; and
Python3.9 pathlib API compatibility.
Baseline red:4 tests in0.179s,1 failure (old streams remained). Final focused
retention plus the updated version test:13 tests in0.709s, OK.

Automatic retention runs only after a saved final call result. It deletes only
regular stdout/stderr captures for old, inactive generated run directories,
keeps the small history files, and preserves ambiguous records. The separate
persistent nonblocking lock and UTC day marker coordinate finishing calls;
marking precedes scanning, so partial failures retry the next day. Filesystem
errors do not change a successful call into a failure. Settings and filesystem
fixtures use isolated state directories; the real ledger was never pruned.
Independent review found no additional defects after tightening terminal proof
for legacy records without process identity. The implementation also uses
`lstat()` for Python3.9 compatibility: [Python's documentation](https://docs.python.org/3.10/library/pathlib.html#pathlib.Path.stat)
confirms the alternative `Path.stat(follow_symlinks=...)` keyword was added
in3.10. A focused fixture emulates the older pathlib API; it failed against an
in-memory variant using that keyword (1 test,1 error in0.003s), then passed
against the actual `lstat()` implementation. No actual Python3.9 interpreter
run is claimed. No smaller retention behavior shipped. VERSION and its expected
test were changed together to0.35.0 here.

Full remote gate output (`python3 -m py_compile bin/yoyo`: silent, exit0):

```text
boxrun: masonry:/data/workspace/yoyo-2c067b94/
.............
----------------------------------------------------------------------
Ran 13 tests in 0.709s

OK
.........................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 601 tests in 57.620s

OK
.........
----------------------------------------------------------------------
Ran 9 tests in 0.062s

OK
```

## Verification and resource boundaries

All six items have a red regression and passing per-item gates. Native recovery,
quota and token trials used the installed authenticated macOS CLIs. All Python
suites and compilation ran on Linux Instabox; the final gates used
Python3.13.5 on Masonry. Credentials were not copied. SHA256 checks of the tested CLI, test file and skill matched the
local final source.
Real multi-hour production runs were not induced: timeout tests use short
clocks. Quota exhaustion was simulated rather than consuming an account limit.
CLI usage scope and picker limits are documented above; no billing totals or
unreported model identity are inferred.

All task-owned foreground test jobs completed; no detached job copies,
containers or ports were created. The final Masonry Baton readback has no
running or queued jobs. The retained build/Masonry machines and reusable
YoYo checkout caches are preserved. The video box used only for preflight is
back in suspended state, and no new VM was created. Native probe supervisors
are no longer running. The two test Codex sessions were reversibly archived
through the native protocol and verified archived; three empty temporary
native workspaces were removed. Claude transcripts and isolated raw captures
remain as verification evidence. No global Claude configuration was rewritten.

The branch remains local: no push, tag, release or installation occurred.


## Follow-up - Muse review and repeat gates

User requested committing the changes and testing everything once with Muse.
All six implementation commits were already present with a clean working tree,
ending at `4f256c987bb6701713264144ee642e1077442c44`.

Fresh verification ran once on retained Masonry, scheduled by remote Baton:
`python3 -m py_compile bin/yoyo`, full unittest discovery, SkillGuardTests,
the expected-version test, CLI help, and SHA256 correspondence for the CLI,
tests and skill. Compilation and help were silent successes; the shell used
`set -eu`, and the complete command exited0. Full output follows:

```text
boxrun: masonry:/data/workspace/yoyo-2c067b94/
.........................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 601 tests in 57.290s

OK
.........
----------------------------------------------------------------------
Ran 9 tests in 0.058s

OK
.
----------------------------------------------------------------------
Ran 1 test in 0.005s

OK
0709e67c46aeb064fdb35860ede515afabdcdf4e31f78ccf86a2133efec06b7a  bin/yoyo
26723260361224592bea0b2a0bebf321e8519b29a47e23bd4370dae4cb082a84  tests/test_yoyo.py
14e5ed4a4220a98aca6cdc0dae916ee792cad26e38152bac5c8cbcd59e3836ea  skills/yoyo/SKILL.md
ALL_REMOTE_GATES_PASSED
```

Muse reviewed the full six-commit diff, current callers/tests, the completed
verification manifest and full gate output. The isolated read-only review
`20261117T165926-4d6d0785` exited0 after359.189s. Its verdict was **No material
findings.** It traced deadlines and detached arguments; interrupted messages,
signals and session validation; sanitized quota and terminal errors; explicit
routing; token snapshots/audit; and retention locking, PID identity and file
safety. Raw answer and manifest are retained in
`.yoyo/muse-final/evidence/{muse-review.txt,muse-verdict.json,verification.json}`.

The parent executed the remote commands; Muse independently inspected their
results and code. Muse's workspace sandbox cannot create boxrun's lock outside
the workspace. Its sandbox stayed enabled, with file reads/search only and
no shell or writes; no YOLO or weaker policy was used. This is consistent with
[Muse's documented sandbox boundaries](https://dev.meta.ai/docs/muse-code/permissions).

Parent inspected all residual review points. Fan-out leaf calls retaining
sessions is consistent with the per-call full-access ask contract. Possible
plain-text error precedence and multi-window expiry cases lacked a concrete
vendor failure in this repeat, so no speculative patch was made. One confirmed
coverage boundary: generated continuation commands preserve state/config paths
and model but do not replay raw `--agent-arg` flags. Custom-profile continuation
was not live-tested and may require native resume configuration; the installed
Codex CLI exposes different options for initial exec and resume, so blindly
replaying profile flags is not established as valid. No guarantee is made for
that unverified configuration.

This repeat added no live vendor probes or real multi-hour runs, induced no
quota exhaustion, and did not run a Python3.9 interpreter. Existing native
probe evidence and provider-usage caveats above still apply. Final remote Baton
readback has zero running or queued jobs; no detached copies, containers or
ports were created. Program, test and skill source remain unchanged after the
review. No push, tag, release or installation occurred.


## Release - v0.35.0

On2026-11-18, the user explicitly authorized commit, push and release,
superseding the original local-only publishing scope. Upstream remained at
v0.34.0; main fast-forwarded without conflicts. Release-preparation commit
`4a33f372055c3ea6a16d0a29ce18beb8c11da23f` and annotated tag `v0.35.0`
were pushed atomically. [yoyo v0.35.0](https://github.com/sushilk1991/yoyo/releases/tag/v0.35.0)
was published with `--verify-tag --latest` and read back as published,
non-draft and non-prerelease. Remote main and the peeled tag matched the
release commit at publication.

The downloaded public tagged `bin/yoyo` matched the Git tag byte-for-byte and
the remote-tested SHA256:
`0709e67c46aeb064fdb35860ede515afabdcdf4e31f78ccf86a2133efec06b7a`.
The CLI, test and skill hashes also matched the Muse-reviewed verification
manifest before publication. No program/test/skill changes followed that
601-test run, so the same verified source was released without another suite
run. This final verification record changes documentation only. The local
installed CLI was not changed.
