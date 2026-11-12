# Read-only visibility into YOYO agents

Checked: 2026-10-18. Repository: `41fca7c`. Research only; commands below marked proposed do not exist yet.

## Recommendation

Add a terminal-independent, read-only `yoyo watch` dashboard backed by the existing run ledger. Add Ghostty and Herdr launchers after the viewer works. Keep agents running under YOYO's existing subprocess supervision; display their captured events in a separate pane.

A viewer can show the task, agent, working directory, elapsed time, lifecycle, and available output without changing execution ownership. It cannot reconstruct a native interactive agent screen: YOYO launches headless processes with stdin and output files, not attached interactive terminal sessions. [L1, L2]

## What exists

| Capability | Evidence | Missing piece |
| --- | --- | --- |
| Run inventory and lifecycle | `runs list`, `run_summary`, PID/start-time validation | Live refresh, project filters, task preview |
| Task text | `prompt.txt`, bounded to 65,536 bytes; `prompt_bytes` | Dedicated short task label; full prompt may start with injected skills |
| Live raw output | Per-call `stdout.txt` and `stderr.txt` written directly by subprocesses | Follow command, readable event rendering |
| Background supervision | Parent `meta.json`, `log.txt`, eventual JSON result; separate child call records | Explicit parent/child relationships and deduplication |
| Diagnosis | `runs show`, `runs autopsy`, `wait` progress | Browse and select agents while they run |

These are verified in the checked-out code. Prompt capture can be disabled with `YOYO_NO_CALL_JOURNAL=1`; foreground captures then use temporary storage. Older records can lack prompt text. Background parent records and agent call records have different shapes. A viewer must not treat a parent result file as an agent's live transcript. [L1–L4]

The current run list reports `done` whenever a result exists, regardless of its exit code. The dashboard should distinguish process exit, failure, and task verification. A zero exit is not proof that the requested work is correct. [L1, L4]

## Proposed interface

```sh
# Proposed: all agents for the current project, with run selection.
yoyo watch --cwd "$PWD"

# Proposed: follow one run in a plain terminal or pipe.
yoyo runs follow RUN_ID

# Proposed: launch one dashboard in a separate terminal surface.
yoyo watch --open ghostty --split right
yoyo watch --open herdr --split right
```

```text
YOYO  project: ~/Code/app
RUN     AGENT    STATE       ELAPSED  TASK
8a12    codex    running       2m14s  Review auth regression
9c03    claude   running       1m03s  Fix empty-state layout

Selected: 8a12  cwd: ~/Code/app  role: review
12:04:10  command started: python3 -m unittest ...
12:04:13  command finished: exit 0
12:04:14  message: Checking the failing boundary case ...

Up/Down select   Enter details   p prompt   q detach
```

This is a textual design, not a screenshot or implemented feature. Display only events the provider actually exposes. Label absent output as `no output yet`, never `thinking`, `stuck`, or `blocked` without evidence.

## Streaming is necessary

Opening a pane alone will not make buffered output appear.

- **Codex:** YOYO currently captures native stdout/stderr and reads its final answer from `--output-last-message`. Official `codex exec --json` provides JSONL events for turns, commands, file changes, tool calls, and messages. Retain the final-answer path while adding event capture. Recheck session-ID extraction, resume behavior, error handling, and final-answer equivalence: the present implementation extracts session IDs from stderr, which a format change could affect. [L2, L5, W1]
- **Claude:** YOYO currently uses text print mode. Official streaming uses `--output-format stream-json --verbose --include-partial-messages`. Parse final `result` separately from activity and partial text; do not return the entire JSONL stream as the answer. Claude documents nested subagent messages with `parent_tool_use_id`, which can support an optional provider-specific child view. This does not establish equivalent support for every vendor. [L5, W2]
- **Cursor:** YOYO already uses stream JSON and reduces it to a final answer. Preserve raw captures and incrementally render them rather than launching another agent. [L6]
- **Other/custom agents:** retain raw stdout/stderr display with an explicit limited-visibility label. Streaming compatibility should be verified separately; no promise of universal tool-level activity. [L2]

A normalized display can support `message`, `tool start`, `tool finish`, and `diagnostic` events where available. Preserve unknown event types and tolerate partial final lines. Provider streams are activity evidence, not access to hidden model reasoning. [W1, W2; proposed design]

## Terminal integration

### Ghostty

Ghostty's official documentation says AppleScript arrived in 1.3.0, is enabled by default on macOS, and is subject to macOS Automation permission prompts. `macos-applescript = false` disables it. `new surface configuration` supports command, initial working directory, environment variables, and wait-after-command; it can be passed to `new window`, `new tab`, or `split`. [W3]

Ghostty's 1.3.0 release notes call AppleScript a preview feature and anticipate breaking API changes in 1.4. Capability/version checks are required; do not treat the interface as stable. Direct local inspection confirms Ghostty **1.3.1** is installed and its scripting dictionary exposes these commands: [W6]

```applescript
set cfg to new surface configuration
set win to new window with configuration cfg
set t2 to split t1 direction right with configuration cfg
```

These are documented API fragments, not a complete launcher. A launcher should create a surface running the trusted viewer command rather than type into the active agent terminal. Prefer a new window when the invoking surface cannot be identified reliably; splitting the frontmost window can target the wrong project. Check configured automation support and handle denied permission without retrying via simulated keystrokes. No GUI actions were executed during this research. [W3, L7; proposed safeguards]

### Herdr

The product matching “Herder” appears to be **Herdr**, whose official CLI uses a local socket API. Herdr is a server-backed terminal multiplexer; its splits live inside an outer terminal, such as Ghostty. Product identity still needs user confirmation. It documents: [W7]

```sh
herdr pane split --current --direction right --no-focus
herdr pane run PANE_ID 'trusted-viewer-command'
herdr pane read PANE_ID --source recent-unwrapped --lines 120
herdr terminal session observe TARGET
```

The split response returns `.result.pane.pane_id`. `--current` requires `HERDR_PANE_ID`; an explicit target avoids focus ambiguity. `pane run` submits a command atomically with Enter. A launcher can split and run the YOYO viewer in the new pane. [W4]

Herdr also supports genuinely read-only terminal observation: `terminal session observe` emits JSON frame records with base64-encoded ANSI bytes. Multiple observers do not gain input, resize, scroll, or takeover authority. It only observes terminals already hosted by Herdr; it does not turn an existing YOYO file-captured subprocess into a pane. A screen-mirroring client would still need to decode and render those records. [W4]

Herdr was not found on the current PATH, so these commands were checked against official docs, not live-tested. Treat the installed CLI/server schema as the integration contract and test it before shipping. [W4; local availability check]

## Ranked choices

1. **Ledger-backed CLI/TUI plus launchers.** Recommended. Works with current headless agents, supports existing run history, and does not require interactive sessions.
2. **One viewer pane per run.** Reuse the same follow command for users who prefer a terminal grid. Opt in; do not open a pane for every fan-out automatically.
3. **PTY-managed agent screens.** A later execution backend for exact interactive-screen requirements. Requires terminal ownership, input policy, resize handling, reconnects, permission behavior, and lifecycle tests. Existing headless runs cannot be retroactively attached. Herdr observation or tmux can help only for processes launched under those systems. `tmux attach-session -r -t SESSION` provides read-only attachment without affecting other clients' sizes; it requires a tmux-managed session. [L2, W4, W5; engineering assessment]

## Implementation order and acceptance

1. **CLI visibility:** add bounded `runs follow`, richer list rows, project filtering, task labels, and explicit parent IDs. Follow both streams and detach without signalling the worker. Do not infer a reliable tree solely from trace-string prefixes.
2. **Streaming adapters:** enable structured activity for Codex and Claude, reuse Cursor. Preserve answer/JSON/session contracts and custom-command behavior. Test fragmented JSONL, unknown events, nonzero exits, no output, and truncation.
3. **Read-only TUI:** use the Python stdlib where practical, with a plain-text fallback. Bounded reads and incremental offsets must avoid rereading entire captures. Search active records beyond the existing recent-list limit. Handle simultaneous metadata writes, run pruning, and disappearing files.
4. **Launchers:** Ghostty macOS AppleScript and Herdr CLI. Pass only trusted, quoted viewer arguments; preserve the effective state directory. Reuse a dashboard per project and leave failed launchers harmless to running agents.

Closing a viewer must leave agents running. Viewing must not resume an agent, send prompts, claim input ownership, or grant tool permissions. Strip unsafe terminal controls from untrusted output before rendering; restrict rendering to viewer-owned styling. Keep prompts collapsed by default, keep captures private, and honor the journaling opt-out. “Read-only” means no agent control, not a privacy boundary against the same OS user.

Planning estimate for an implementer: 1–2 engineering days for a useful CLI viewer and launcher prototype; 3–5 total for a tested dashboard plus Codex/Claude streaming compatibility. These are estimates, not measured benchmarks. Exact interactive screens are a separate project.

## Sources

Local source anchors are function names because line numbers drift:

- **L1:** [`bin/yoyo`](../../bin/yoyo): `run_status`, `run_summary`, `write_journal_prompt`, `journal_call_start`.
- **L2:** [`bin/yoyo`](../../bin/yoyo): `run_to_files`, `execute_agent_call`.
- **L3:** [`bin/yoyo`](../../bin/yoyo): `start_background_run`, `background_child_argv`.
- **L4:** [`docs/REFERENCE.md`](../REFERENCE.md): Background runs; The call journal and autopsy; `yoyo runs audit`.
- **L5:** [`bin/yoyo`](../../bin/yoyo): `command_for_agent`, `attach_session_result`, `record_codex_session`.
- **L6:** [`bin/yoyo`](../../bin/yoyo): `decode_cursor_stream`, `decode_agent_stdout`; [reference](../REFERENCE.md), Agents.
- **L7:** `/Applications/Ghostty.app/Contents/Info.plist` and `/Applications/Ghostty.app/Contents/Resources/Ghostty.sdef`, inspected locally on the checked date.
- **W1:** [OpenAI: Non-interactive mode](https://developers.openai.com/codex/noninteractive), JSONL events and final-answer output.
- **W2:** [Anthropic: Run Claude Code programmatically](https://code.claude.com/docs/en/headless), Stream responses and Follow subagent messages.
- **W3:** [Ghostty: AppleScript on macOS](https://ghostty.org/docs/features/applescript), API, availability, permissions, surface configurations.
- **W4:** [Herdr: CLI reference](https://herdr.dev/docs/cli-reference/), pane split/run/read and direct terminal observe.
- **W5:** [OpenBSD tmux manual](https://man.openbsd.org/tmux), `attach-session -r` and client flags.
- **W6:** [Ghostty 1.3.0 release notes](https://ghostty.org/docs/install/release-notes/1-3-0), AppleScript preview and expected 1.4 breaking changes.
- **W7:** [Herdr product page](https://herdr.dev/), server-owned terminals and terminal clients.
