# yoyo reference

The full flag-level reference. For the tour, see the [README](../README.md).

## Agents

| Agent | Command | Notes |
| --- | --- | --- |
| `codex` | `codex exec` | Default reviewer/second opinion; powers `imagegen` |
| `claude` | `claude -p` | Default worker for scoped edits |
| `pi` | `pi -p --mode text` | Lightweight and cheap |
| `cursor` | `cursor-agent -p --output-format stream-json` | On-demand worker and model picker (`--model composer-2.5`, `cursor-grok-4.5-high`, …) |
| `agy` | `agy -p` | On-demand. Google Antigravity CLI; reuses its native Google login. `--read-only` is `--mode plan --sandbox` with permission prompts auto-denied — no writes, no shell beyond agy's own allowlist. yoyo forwards `--timeout` as `--print-timeout` (agy's own default is 5m) |
| `grok` | `grok --prompt-file /dev/stdin --output-format plain` | On-demand. A fourth independent vendor for adversarial cross-checks. `--read-only` is a headless tool allowlist (`read_file,grep,list_dir`, MCP meta-tools and subagents removed) — no writes, shell, web, or MCP |
| `muse` | `muse exec --prompt-file <temporary-file>` | Meta's Muse Code. Workers skip approval but retain the default sandbox; reviews expose only `read_file` and `search` |

`codex`, `claude`, and `pi` support `--session` follow-ups and are the battle-tested defaults. The on-demand agents are one-shot only — reach for them when a specific edge fits (a model the others don't expose, a third vendor to break a tie). On-demand agents authenticate through their own CLIs.

Built-in agents depend on specific CLI flags (codex's `exec`/`--sandbox`/`--output-last-message`; claude's `-p`/`--permission-mode`/`--tools`/`--disallowedTools`; pi's `--mode`/`--tools`; agy's `-p`/`--add-dir`/`--mode`/`--sandbox`/`--print-timeout`; grok's `--prompt-file`/`--tools`/`--permission-mode`; Muse's `exec`/`--prompt-file`/`--workspace`/`--disable-approval`/`--disable-write`/`--disable-shell`/`-c`). yoyo doesn't detect CLI versions, so a renamed flag surfaces as an agent error — run `yoyo doctor --live` after upgrading a CLI to catch drift early.

Cursor setup and discovery:

```bash
cursor-agent login
cursor-agent --list-models
yoyo doctor --live --agent cursor
yoyo ask cursor --role worker --model composer-2.5 --cwd "$PWD" "Make the scoped edit and run its focused test."
```

Cursor model IDs are account- and version-dependent; YOYO deliberately forwards `--model` instead of maintaining a stale allowlist. Cursor is one-shot under `yoyo ask` (`--session` is rejected), uses Cursor plan mode—not an OS sandbox—for `--read-only`, and uses `--force` only for the default full-access worker path. Pair write delegations with focused tests or another independent verification step.

yoyo drives cursor over `--output-format stream-json`, not its `text` mode, and reduces the event stream back to prose itself. The reason is measured, not stylistic: in `text` mode `cursor-agent` holds the entire answer in memory and flushes it once on exit, so when its transport drops mid-run — `Connection lost, reconnecting to …` then `RetriableError: WritableIterable is closed` — the process exits 1 with **empty stdout** even though the edits already landed on disk. On a five-run write-heavy sample (three files written and read back per run, cursor-agent 2026.08.04) four of five text-mode runs ended exactly that way: exit 1, zero bytes of answer, all three files correctly written. The same workload over `stream-json` kept every answer, because each chunk is already on disk when the transport dies.

Two consequences worth knowing. A cursor answer may now arrive with a note on stderr — `cursor stream ended without a result event; answer stitched from N partial chunks` — which means the text is real but possibly incomplete; treat it as a partial. And the raw capture in the run ledger is JSONL rather than prose, so read a cursor run's answer through `yoyo runs show` rather than by eyeballing `stdout.txt`.

Antigravity (`agy`) is yoyo's only built-in Google target and reuses the CLI's existing login. Google names it as the consumer replacement in its [transition announcement](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/).

Antigravity (`agy`) and Grok setup and discovery:

```bash
agy models                                    # slugs agy accepts for --model
grok models                                   # same for grok
yoyo doctor --live --agent agy --agent grok   # both modes; the read-only probe tries a write and checks disk
```

What `--read-only` is on each, measured on agy 1.1.27 and grok 1.0.13 rather than read off the help text:

- **agy** runs `--mode plan --sandbox` *without* `--dangerously-skip-permissions`. Plan mode on its own is a prompt-level directive: with the skip flag present, a `--read-only` worker's `write_to_file` landed on disk. What enforces read-only is headless mode itself — a tool that needs a permission it cannot prompt for is auto-denied — so the skip flag is the one thing read-only must not carry. Reads (`list_dir`, `view_file`, `grep_search`) need no permission. Shell commands are denied unless the user allowlisted them under `permissions.allow` in agy's `settings.json`, and `--sandbox` keeps an allowlisted command from writing to the tree. The rough edges: a denied call ends the run with no answer while agy exits 0 — yoyo turns that into exit 1 with agy's own diagnosis on stderr (`silent_exit: true` in `--json`); and agy's print mode can end its turn while a command it launched in the background is still running, so a one-line answer such as "waiting for the results" is not a review. Keep read-only agy prompts to reads, read a short agy answer with suspicion, and give it full access when the task needs a shell.
- **grok** runs `--tools read_file,grep,list_dir --disallowed-tools search_tool,use_tool,Agent --permission-mode bypassPermissions`: the model has no write, edit, shell, web, MCP, or subagent tool, and a call to one returns an error it can report rather than a cancelled turn. The `--disallowed-tools` half matters: grok's allowlist keeps its MCP meta-tools (`search_tool`/`use_tool` reach every configured MCP server, which can write), and `--disallowed-tools` wins over `--tools`. Two alternatives were rejected on evidence. `--permission-mode plan` does block writes, but it cancels the whole turn on the first denied call and returns the preamble as the answer with exit 0 — a silent truncation. `--sandbox read-only` logged itself applied and enforced (Seatbelt) while the Write tool still created the file. Full-access grok keeps its full toolset.

agy takes the prompt as the value of `-p`, that is in argv, so a prompt above the kernel's argument limit (1 MiB for the whole vector on macOS, 128 KiB per argument on Linux) fails before the call starts; yoyo reports the size and the fix (trim `--file` context, or use an agent that reads stdin). agy's print mode also has a clock of its own, `--print-timeout` (default 5m); yoyo forwards its `--timeout` plus a 60s margin so that when a call does run out of time it is yoyo's deadman that fires and the ledger records a timeout, instead of agy's `Error: timeout waiting for response` being filed as a crash. Both agents are one-shot under `yoyo ask` (`--session` is rejected). Antigravity CLI's documented global skill home is `~/.gemini/antigravity-cli/skills`; the working legacy `~/.gemini/config/skills` home remains supported. grok uses `~/.grok/skills` (plus `~/.claude/skills`). `yoyo install-skill` writes these homes and the shared `~/.agents/skills` home used by Cursor, Codex, and Pi. It also resyncs existing `~/.cursor/skills` overrides without creating duplicate copies in a fresh native home. Symlink overrides are repaired without changing their targets; links or native roots already pointing to the bundled source remain intact.

## Muse Code

Authenticate through Muse's native CLI (`muse`, then `/login` if needed). yoyo reuses the existing login and forwards account-specific `--model` IDs; it does not select a remembered Muse model or configure credentials.

```bash
muse --version
muse skills inspect yoyo --source user --json
yoyo doctor --live --agent muse
run_id=$(yoyo ask muse --role review --read-only --cwd "$PWD" --background "Read the auth files and report verified bugs. Cite file/line.")
yoyo wait "$run_id" --timeout 25
```

Muse requires a regular prompt file: omitted prompt text does not read stdin, `-` is literal, and `/dev/stdin` is rejected. yoyo writes the complete rendered prompt to a private per-call temporary directory and passes its path through `--prompt-file`; cleanup also runs on failures, timeouts, and handled POSIX signals. This avoids argv-size limits and preserves piped context and UTF-8 text.

Workers use `--trust-workspace --disable-approval`, leaving Muse's default sandbox enabled. The sandbox can prevent writes or child-agent activity outside the workspace; yoyo does not persist a weaker native policy. Reviews add `--disable-write --disable-shell` and `-c 'run.toolset=["read_file","search"]'`; no write, shell, MCP, or subagent tool is selected. Native hooks are outside Muse's tool sandbox, so this is not an isolation guarantee against configured hooks. Calls are one-shot and use `--no-session-log`; yoyo's own ledger and captures remain available, but `--session` is rejected. Text output can stay quiet across tool/model steps; use background/wait instead of an idle timeout.

Muse supports `--yolo`, which disables approvals and sandboxing and trusts the workspace for one run. yoyo can forward it with `--agent-arg=--yolo`; it is not the default. Use it only with explicit approval in an already-isolated environment, not as a read-only review flag. If Muse is the caller, its outer sandbox can block Codex home writes and nested macOS Seatbelt setup; a child flag cannot remove that inherited boundary. Meta also documents one-time human approval for an exact blocked command. Neither YOLO nor that approval flow has been live-verified for nested delegation here. See the [sandbox diagnosis](compatibility/2026-10-13-muse-sandbox.md).

Caller skills install into `$XDG_CONFIG_HOME/muse/skills` (default `~/.config/muse/skills`). Muse also discovers the shared `~/.agents/skills` copy when foreign personal context is enabled. The native copy remains available with that context disabled. Start a fresh Muse session after skill changes.

Native references, checked October 13, 2026: [headless runs and skills](https://dev.meta.ai/docs/muse-code/extending), [permissions](https://dev.meta.ai/docs/muse-code/permissions), [configuration](https://dev.meta.ai/docs/muse-code/configuration).

## Calling agents

Claude Code, Codex, Cursor, Antigravity, and Muse Code can launch other targets through an allowed shell tool using the same commands. Caller support means discovering the installed skill and permitting subprocess launches; target support means a built-in adapter and working native login. A target adapter alone does not verify caller discovery or quota.

```bash
command -v yoyo
yoyo install-skill                            # repair/resync from the recorded checkout
run_id=$(yoyo ask codex --role review --read-only --cwd "$PWD" --background "Review the diff. Cite file/line.")
yoyo wait "$run_id" --timeout 25               # 124: still running; repeat this poll
```

Restart the caller after skill installation. Authenticate targets through their own CLIs; yoyo does not create credentials. Poll 124 means running, not failure; a nonzero agent exit means unavailable or failed, not a completed review. Inspect the run's result and captured diagnostics. A read-only target does not override the caller's own shell permissions.

Native discovery references, checked October 12, 2026: [Cursor skills](https://cursor.com/docs/context/skills) and [Antigravity skills](https://antigravity.google/docs/skills/).

## Models

```bash
yoyo models codex --cwd "$PWD" --json
yoyo models claude --cwd "$PWD" --json
```

Reads the native CLI's account metadata and selectable model list, without sending a user turn. Codex uses its app-server `account/read` and paginated `model/list`; Claude uses the SDK initialization handshake and `auth status`. No API keys, email addresses, or organization identifiers appear in Yoyo's output. Probes close their processes and have a 20-second deadline (`--timeout` changes it); nothing stays resident.

JSON contains `agent`, `source`, `checked_at`, `account`, and `models`. Each model includes its selectable `model` ID, name, native description, picker-default marker, and supported reasoning efforts when supplied. Claude also reports the resolved model ID. The picker-default marker is not a readback of your configured model. Account type distinguishes ChatGPT/Claude login from API-key access; remaining quota and extra-usage billing are not checked. Model availability can change after discovery.

Discovery supports unmodified built-in Codex and Claude commands. If an agent is overridden in `agents.json` or `YOYO_AGENT_*`, it fails rather than dropping profile/wrapper arguments and querying a different account. Use that custom CLI directly. Other providers retain their own discovery commands.

The calling agent selects a returned model with the existing `--model` flag. The bundled skill prefers an available subscription tier for bounded, objectively checkable tasks and keeps the configured default for complex work. There is no background router, hard-coded model allowlist, or automatic provider fallback. Existing explicit model choices still pass through unchanged.

Protocol references, checked September 22, 2026: [Codex app server](https://learn.chatgpt.com/docs/app-server), [Claude model configuration](https://code.claude.com/docs/en/model-config), and [Claude SDK initialization](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/query.py).

## Local advice

`yoyo advise --file evidence.json` returns JSON suggestions for suspected repeated attempts, failure categories, and possible duplicate review findings. It accepts focused evidence from any CLI; it does not select a coding model or change delegation behavior. The optional Apple Silicon backend uses Decider 0.8B with 4-bit MLX weights. [Setup, schema, examples, and benchmark limits](../extras/README.md).

Advice is an explicit, bounded call: at most 32 checks, 64 KiB of input, and 1,024 tokens per check. One child loads the model and exits afterward; concurrent calls return busy. `--timeout` accepts up to 30 seconds. Missing dependencies, invalid input, and inference failures return exit 2. Successful requests return exit 0 even if every check abstains; this is never a coding-task completion signal.

The `choice` field is advisory; raw `candidate` and `scores` are not acceptance evidence. Low scores abstain, and repetition checks can only suggest `repeated` or `unclear`. Neither a model score nor a duplicate suggestion can stop a loop, mark DONE, discard findings, or override the caller's verification. Existing `ask`, `loop`, and `review` commands do not invoke this helper automatically.

## Ask

`yoyo ask` is one-shot and full-access by default (so agent-to-agent calls don't stall on permission prompts). `--role` defaults to `opinion`; pass `review` or `worker` for those behaviors. Use `--read-only` for a bounded reviewer or untrusted input.

```bash
yoyo ask claude --role opinion "Challenge this design and list failure modes."
yoyo ask codex --role review --read-only --cwd "$PWD" --file bin/yoyo "Find correctness bugs and missing tests."
yoyo ask pi --role worker --cwd "$PWD" "Fix the failing test. Don't touch unrelated files."
git diff | yoyo ask claude --role review --cwd "$PWD" "Review this diff against the worktree."
```

**Fan-out (best-of-n).** A comma-separated agent list runs the same prompt on every agent in parallel. Add `--judge <agent>` to have an independent judge compare the answers on correctness/evidence/completeness and recommend the best (or a merge). The default judge verdict leads with a **convergence/divergence map**: what the answers independently agree on, then every genuine conflict with the narrowest check that would settle it — divergence is the caller's verification work list, and cross-vendor agreement is treated as signal, not proof. The judge is **blind**: answers reach it shuffled under letter labels (`A`, `B`, …) with the agent names withheld, each between `<<<ANSWER A nonce>>>` / `<<<END ANSWER A nonce>>>` lines whose random marker no candidate can predict, so an answer cannot close its own fence and speak as the prompt. The label → agent key comes back to you, never to the judge: `judge_labels` in `--json`, a `labels: A = codex, B = claude` line under the verdict in text. `--judge-prompt "..."` replaces the judging instructions (used verbatim; the task and the same blind candidates are appended). Without `--judge`, you get all answers in tagged sections and judge them yourself. The judge always runs **read-only** — its prompt embeds untrusted candidate output — so the judge agent must support a read-only mode. Exit is 0 if at least one agent succeeded and a failed judge falls back to the raw answers; scripted callers should use `--json` and inspect per-result `exit_code`s. Repeating an agent (`codex,codex`) is allowed — that's a self-consistency sample. Prefer a judge that isn't among the candidates: models measurably favor their own answers, and blind labels do not stop a model recognizing its own style — yoyo prints a note when the judge is also a candidate.

```bash
yoyo ask codex,claude --cwd "$PWD" "Design the rate limiter. Name the riskiest assumption." --judge grok
yoyo ask codex,claude --json "..."   # results array + judge in one envelope
yoyo ask codex,claude,grok --judge cursor --judge-only "..."   # verdict only; raw answers go to files
```

**Co-citation.** Every fan-out with at least two successful answers ends with a `cited by more than one agent` block listing the `file:line` sites that appear in more than one answer, matched on line and on the path's trailing segments, so `bin/yoyo:1338` and the absolute path count as the same site while `src/auth.py:42` and `tests/auth.py:42` stay separate (`co_cited` in `--json`). Across 88 real fan-outs replayed from the local ledger, 52% had at least one shared site. It says two agents looked at the same line — not that either is right, and not that a site cited once is wrong. Each site carries what is on disk at that line right now — the line's text, `no such file under the run cwd`, or `the file has N lines` — as evidence, never a verdict: yoyo does not grade an answer, and a full-access agent may have edited the file between citing it and the read. Agents spell one site several ways, so when the spelling that resolves is not the one displayed the note names the path it actually read (`read_as`). An answer is untrusted text, so a cited path that resolves outside `--cwd` is reported as not read rather than opened (`disk` in `--json`).

**`--judge-only`** keeps a judged fan-out from flooding the caller's context: the raw candidate answers are written to files under `$YOYO_STATE_DIR/fanout/<trace>/` and only the judge's verdict (plus the file paths) is returned. In JSON mode each result's inline `stdout` is emptied and a `stdout_file` path is added, and the envelope gains `answers_dir`. A failed or skipped judge falls back to returning the raw answers inline — the caller still needs them.

**Steer output with a skill.** `--skill <name>` (repeatable) injects a named `SKILL.md` into the prompt as guidance — the skill says *how*, the prompt says *what*. Names resolve by directory from `YOYO_SKILL_PATH`, then `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills`, `~/.config/opencode/skills`, `~/.gemini/config/skills`, `~/.grok/skills`, and Pi's skills dir, then native `~/.cursor/skills`, `~/.gemini/antigravity-cli/skills`, and Muse's XDG skill home, before the bundled skills; a missing skill fails loudly. Discover with `yoyo skills`. A name containing a path separator is treated as an explicit path — a markdown rules file or a directory holding a `SKILL.md` — so overlay rulesets (e.g. a [ponytail](https://github.com/DietrichGebert/ponytail)-style minimal-code ladder as a "senior engineer mode") inject without installing anything: `--skill ./rules/ponytail.md`. Relative skill paths resolve against the process working directory (not `--cwd`) — prefer absolute paths in scripts and background runs. **Inbuilt default: `yoyo-fable-mode`.** yoyo ships a delegate harness inside its own bundle — evidence discipline for one-shot work — and injects it into **every non-`--raw` call by default**, whichever agent is called. It needs no install step: it resolves from the yoyo checkout (or the recorded source of an installed binary). The name is `yoyo-`prefixed deliberately. User skill roots are searched *before* the bundle, so the earlier bare name `fable-mode` was shadowed by whatever personal skill the caller happened to have installed — typically one written for an interactive editor session, which told every delegate it was "running as Opus". `YOYO_DEFAULT_SKILLS` (comma-separated) replaces the default set, and the empty string disables injection; duplicates of explicit `--skill` names are dropped, and an unresolvable default is skipped with a stderr warning rather than failing the call.

**Structured findings without a schema.** When the caller wants machine-readable findings, ask for them in the prompt as [TOON](https://github.com/toon-format/toon) rows (`findings[N]{file,line,severity,claim}:` — YAML-style nesting, CSV-style rows, ~40% fewer tokens than JSON on uniform data). This is a prompt convention, not a yoyo feature: yoyo never validates or parses agent output. Use TOON only for tabular lists; prose reads better (and cheaper) as plain markdown.

**Writing good prompts:** put the instruction first; attach context with `--cwd`/`--file`/stdin; state the success criterion, scope, and exclusions; ask reviewers to falsify ("find the strongest reason this is wrong"); replace vague words with observable criteria. Let the worktree be the source of truth.

**Other flags:** `--raw` sends the prompt verbatim (no role/context wrapper) so a leading `/command` reaches the target CLI; `--json` emits a result envelope; `--trace-id` tags a call; `--model` passes a model through; `--max-output-bytes` / `--max-input-bytes` cap output and (stdin + `--file`) input. Calls default to a four-hour timeout — a hung-process deadman guard, not a progress budget (`YOYO_TIMEOUT` or `--timeout` to change). A periodic stderr heartbeat keeps a working agent from looking hung (`--quiet` to disable); `--idle-timeout` adds a hang guard that fires when an agent goes quiet after its first byte. Against an agent that buffers its output it is not a hang guard at all, and yoyo says so on stderr before the call: `pi -p` emits nothing until it exits, so the guard never arms; `claude -p` emits a little stderr in its first second and then goes silent for the rest of the run, so the guard arms and fires in the middle of healthy work. `--timeout` is what bounds a run that never starts. `cursor-agent` is asked for `--output-format stream-json` precisely so it streams: its text mode holds the whole answer until exit, and a dropped connection then loses it (see below), so the guard is real for cursor after its first event — roughly fifteen seconds of silent thinking.

## Research

`yoyo research` gathers **diverse perspectives before you decide what to do next**. Each lens runs as one parallel agent call investigating from a single angle; a synthesizer then writes a decision brief: convergence, tension (the most useful part), key evidence, open questions, and options.

```bash
yoyo research --cwd "$PWD" "Should we move the core engine to Rust?"
yoyo research --lenses proponent,skeptic,analyst --agents codex,claude --json "Is WebGPU ready for our renderer?"
yoyo research --lenses analyst,analyst --agents codex,claude "..."      # same lens, two vendors: best-of-n
yoyo research --lens "Investigate only the licensing implications, citing the actual license texts" "..."
yoyo research --no-synthesis --json "..."                                # raw perspectives; you synthesize
yoyo research --synthesis-prompt "Rank findings by decision impact" "..."
```

Default lenses are `proponent,skeptic,analyst,explorer,pragmatist`. Lenses are entirely yours to define: unknown single-word names become ad-hoc angles, repeatable `--lens` takes full free-text instructions verbatim, and duplicates are allowed (round-robin assignment lands them on different vendors — a deliberate best-of-n sample). `--synthesizer` picks the merging agent (default: first of `--agents`); `--no-synthesis` skips the merge so the caller — who usually holds the decision context — synthesizes; `--synthesis-prompt` replaces the brief format with your own instructions.

Research defaults to **full-access** so agents can use web search, fetch, and code execution; `--read-only` restricts them but limits those tools on some agents. `--file` adds shared context to every researcher. The synthesis surfaces disagreement rather than averaging it — verify the load-bearing claims yourself.

## Review

`yoyo review` runs a cross-vendor consensus review of the current git diff: each agent reviews independently, in parallel, read-only, then a synthesizer merges the results.

```bash
yoyo review --cwd "$PWD"                         # codex + claude in parallel
yoyo review --agents codex,claude,agy --json     # three vendors with independent architectures
yoyo review --base main --pr                     # review committed work, post as a PR comment via gh
yoyo review --stance unanimous                   # precision: only findings ALL reviewers raised
yoyo review --stance any                         # recall: every distinct finding, tagged with reviewer count
yoyo review --synthesis-prompt "Report only security findings, as TOON rows"   # your own synthesis
```

A dirty tree reviews `git diff HEAD`; a clean tree reviews `<base>...HEAD` (`--base` auto-detects origin HEAD, then `main`, then `master`). The synthesizer (`--synthesizer`, default: first of `--agents`) splits findings into **CONSENSUS** (raised by ≥2 reviewers) and **SINGLE-REVIEWER** (unconfirmed). `--stance` is a precision/recall dial on the synthesis: `unanimous` reports only what every reviewer independently raised (unanimous juries measurably cut false positives, at recall's cost — the rest is still listed one-line under NOT UNANIMOUS), `any` keeps every distinct finding tagged with how many reviewers raised it (for audits where a miss costs more than noise). `--synthesis-prompt` replaces the merge instructions entirely (verbatim; diff and reviews appended). Agreement across vendors is signal, not proof — models fail in correlated ways, so verify surprising consensus too. One reviewer failing is reported and the rest proceed; only all reviewers failing exits non-zero. Untracked files aren't in a git diff, so they're listed in the prompt for reviewers to read.

## Loop

`yoyo loop` runs one task as a sequence of independent fresh-context iterations. A long-lived session re-reads its whole growing context on every tool call, so cost compounds; a loop makes each iteration a brand-new session with empty context — continuity lives in a small state file (default `.yoyo/loop-state.md`) the agent reads first and rewrites before ending.

```bash
yoyo loop claude --cwd "$PWD" --max-iter 30 --background "Fix the failing tests, one per iteration."
yoyo loop codex,claude --cwd "$PWD" "Refactor module by module."   # rotate vendors across iterations
```

A comma-separated agent list rotates vendors iteration by iteration. The loop ends on a `STOP` file beside the state file, accepted `STATUS: DONE`, `--max-iter` (default 20), or `--max-fail` consecutive crashed iterations (default 3). Crashes reaching `--max-fail` exit 1. `--skill`, `--read-only`, `--idle-timeout`, `--model`, and byte caps pass through; `--background` detaches the whole loop.

`--spec PATH` pins a standing spec: re-read every iteration, never rewritten, holding the constraints the lossy state rewrite would otherwise drop.

State-file guards: a `.task` sidecar makes reusing a state file recorded for a different task fail loudly; a leftover `STOP` file is cleared at startup; a `.lock` (flock) rejects a second concurrent loop.

`STATUS: DONE` is whatever the worker writes. Read the diff before acting on it.

### Completion checks and stalls

```bash
yoyo loop claude --cwd "$PWD" --model sonnet --verify 'python3 -m unittest discover -s tests' --max-stall 3 --background "Finish the scoped fix."
```

Choose the model from the current catalog first. `--verify COMMAND` runs `/bin/sh -c COMMAND` in `--cwd` only after a DONE claim and a satisfied queue. It uses the caller's permissions and the per-iteration `--timeout`; it is not confined by the worker's `--read-only` flag. Choose a repeatable acceptance check. A nonzero exit or timeout rejects DONE and puts bounded output into the state file. The next worker sees the failure. Logged DONE text cannot become a completion marker. After `--max-fail` failed completion checks, the loop stops with exit 1. With verification enabled, any ending other than verified DONE exits 1, including an exhausted iteration budget. JSON adds the last `verification` result and `verification_rejections` count.

`--max-stall N` stops with `end_reason: state-unchanged` and exit 1 when N consecutive iterations leave both state and queue byte-for-byte unchanged. Worker changes reset the counter; Yoyo's feedback does not count. Missing, unreadable, or truncated continuity files cannot establish a stall. This reads only those files, does not scan the worktree, and does not judge semantic progress. It is off by default. Both flags survive `--background`; `--dry-run` executes neither the worker nor the check.

Use failure evidence to choose the next action. Repeated code failures can justify a stronger subscribed model with the same state and task; login, quota, and environment failures need their own fix. No model is automatically promoted, and a passing command proves only what that command checks.

### Work queue (`--queue FILE`)

A markdown checklist turns the loop from one fuzzy goal into N crisply checkable increments:

```bash
yoyo loop claude --cwd "$PWD" --queue tasks.md --background "Work through the queue."
```

`tasks.md` holds `- [ ] item` lines (free text around them is ignored; fenced code blocks are skipped). Each iteration is *instructed* to complete exactly ONE unchecked item and mark it `- [x]` in the file — pacing is guidance, not enforced; what is enforced is completion: a `STATUS: DONE` claim is mechanically rejected while any box is unchecked (the rejection lists the remaining items in the state file). Verification reads the whole file uncapped and **fails closed**: an unreadable queue, or one rewritten without any checklist items, rejects DONE rather than passing it. The queue file must exist and contain at least one checklist item at start. The worker owns the queue file and could check a box falsely, so this catches the honest mistake of claiming done with work still listed, not a determined faker. The summary reports `queue_rejections`.

### Shared brief (`--brief FILE`)

Fresh-context iterations re-derive the same repo knowledge — conventions, layout, build/test commands — every time. A brief stops that:

```bash
yoyo ask claude --read-only --cwd "$PWD" \
  "Write a dense brief for agents working in this repo: layout, conventions, build/test commands, gotchas. Under 150 lines." \
  > .yoyo/brief.md
yoyo loop codex,claude --cwd "$PWD" --brief .yoyo/brief.md --queue tasks.md --background "Work through the queue."
```

The brief is injected read-only into every iteration (workers are told not to edit it and not to re-derive what it records), re-read each iteration so you can regenerate it between runs, and placed in the stable prompt-prefix region so it can hit vendor prompt caches. Keep it dense — it rides in every iteration's prompt, so its token budget is the design constraint. At loop start yoyo warns once on stderr if the brief cites `file:line` paths that are not present under `--cwd`, naming up to five — existence only, never the line number, since a line moves as ordinary drift while an absent file sends a fresh-context worker hunting. It is a note, not a gate: the loop runs either way, and the path may be one the loop is about to create. Regenerating when the repo changes materially is the caller's call, not yoyo's. For one-shot fan-outs (`ask`, `research`), pass the same file with `--file` so parallel agents don't each re-explore the repo.

## Background runs

A real review or worker task can outlive the caller's tool budget. `--background` detaches the run into a durable ledger and returns immediately. It works on every long-running command — `ask` (including fan-outs), `loop`, `research`, and `review`:

```bash
run_id=$(yoyo ask codex --role review --cwd "$PWD" --background "Audit the auth module.")
run_id=$(yoyo research --background "Should we adopt X?")
run_id=$(yoyo review --background)
run_id=$(yoyo review --cwd "$PWD" --background)
yoyo runs list
yoyo wait "$run_id"            # blocks until done, exits with the run's exit code (a finished 124 exits 1)
yoyo runs show "$run_id" --json
yoyo runs prune --days 7
```

`yoyo wait` exit codes: 124 = still running (wait again); 0 = success (result on stdout); anything else = failed or died. 124 is reserved for a live poll: a finished run that recorded 124 (yoyo's `--timeout` or `--idle-timeout`, or the agent's own exit) makes `wait` exit 1, while the printed result and `result.json` keep the recorded 124, so a caller that polls while the exit is 124 never spins on a finished run. The 124 path reports proof of life — the poll expired, not the run — with how long the run has been going and the freshest progress on disk: the last heartbeat line from `log.txt`, else the captured byte counts. Under `--json` those arrive as `run_status`, `elapsed_s`, `last_progress`, `stdout_bytes`, `stderr_bytes` alongside the unchanged `run_id` and `status`. Runs live under `$YOYO_STATE_DIR/runs/<run_id>/` (default `~/.local/state/yoyo`). A run reports `running` only while its recorded pid still belongs to the process that started it: the ledger stores the pid's start time alongside it, because a pid on its own is reused, and a run killed hard enough to skip writing its result would otherwise read as `running` forever — polled by `wait` and skipped by `prune`. The check needs both halves, so a record written before this release, or one on a host where `ps` cannot answer, falls back to the pid alone and stays as permissive as it was. Argument validation still happens in the foreground, so a bad agent name fails loudly before detaching.

## The call journal and autopsy

Foreground agent calls are journaled into the same ledger as they execute (`meta.json` at start, `result.json` at exit, live stdout/stderr captures in the run dir), so a call killed by its caller leaves evidence instead of vanishing. If yoyo receives SIGINT/SIGTERM/SIGHUP mid-call, the journal records which signal and when.

```bash
yoyo runs autopsy              # explain how the most recent run ended
yoyo runs autopsy "$run_id"    # or a specific one; --json for structure
```

When prompt capture is enabled and the parent has the prompt, its record keeps a 64 KB head in `prompt.txt` and the exact full size in metadata as `prompt_bytes`, so the autopsy opens with an `asked:` line. That size measures what was stored: the assembled prompt for a foreground call, the command-line question alone for a background parent, whose assembled prompt is built later in the child. Older records, and runs whose prompt is assembled inside a detached child, have no stored prompt and get no `asked:` line. Each foreground call also keeps its normalized final answer as `answer.txt` — for codex that is the `--output-last-message` text, which lives in call scratch that is deleted when the call returns, and for cursor the decoded stream — so `yoyo runs show <run_id>` prints a foreground call's answer, not only its exit code. The line reports the caller's own task or nothing: when injected skill documents fill that head the task heading falls outside it, and the autopsy omits the line rather than quoting skill text as the question. It ends where the question does — piped input and `--file` contents are attachments, and never appear in the preview.

The autopsy states, from recorded evidence, whether the run completed, failed, hit a timeout, was signal-killed (naming the signal and elapsed time), or died hard — plus captured byte counts. A run killed at exit 124 names the clock that fired and the value it fired at (`--timeout`, the caller's budget was too small, or `--idle-timeout`, the stream stalled); `--json` carries the same as `timeout_clock` and `timeout_clock_s`, null when the record cannot tell an agent's own 124 apart. Use it before concluding an agent "timed out with no output": most such reports are the caller's own exec-tool budget killing a healthy run (note: `claude -p` writes stdout only at completion, so zero bytes mid-run is normal). Set `YOYO_NO_CALL_JOURNAL=1` to disable foreground call journals and background-parent prompt capture — a background parent still records the argv it spawned, with the question itself blanked; `yoyo runs prune --days N` trims the ledger.

### `yoyo runs audit`

What the ledger says about a window of real calls, per agent: how many, how they ended, and p50/p90 duration.

```bash
yoyo runs audit               # last 7 days
yoyo runs audit --days 60     # or a wider window; --json for structure
```

The ledger nests — one `ask codex,claude --background` writes a background parent plus one call record per agent, and every loop iteration mirrors the call it wraps — so summing directories counts the same work up to three times. The audit reports each cohort's size for coverage and aggregates outcomes from call records alone.

Outcomes stay separate because they need opposite fixes: `hard-timeout` means the caller's budget was too small, `idle-timeout` means the stream stalled, a bare `exit-124` is the agent's own, and `signal` means the caller killed yoyo rather than the agent failing. A record with no `result.json` is `running` if its process is still alive and `no-result` if it died without stamping itself, and one that records no usable exit code is `unreadable`; none of the three joins a rate that would blame an agent for it. The pid is probed only for records that have no result yet, never for the whole ledger. Durations come only from what a run recorded, and percentiles are nearest-rank, so every number is a duration some call actually took. Empty answers are reported as a count against their denominator, not as failures — codex answers via `--output-last-message`, so zero captured bytes is normal for it. There is no cost or token accounting.

`yoyo doctor` reports the effective default skills and the path each resolves from, marking any path outside yoyo's own bundle. It also checks that each installable caller skill exists in at least one supported shared or native home and that every existing copy matches the bundled source. Dependency-gated skills such as `yoyo-imagegen` are not reported missing when their required CLI is absent. It also flags both drifted copies and `yoyo`-prefixed directories the bundle no longer ships — the strays a rename leaves behind, still resolvable and still winning lookups. Pi reads `~/.agents/skills` alongside its own skills dir, so yoyo installs pi's copy only through the shared dir; a leftover copy inside the pi home is reported as LEGACY, and `yoyo install-skill` removes it when it is byte-identical to the bundle or moves it aside intact otherwise (a foreign skill there is left alone). `doctor --strict` fails on missing guidance or any of these copy problems; `yoyo install-skill` resolves its bundle through the recorded checkout and resyncs the drift, and an orphan waits for a human to move it aside — the SKILL.md files are yoyo's interface to calling agents, so a stale copy silently degrades that agent's calling patterns.

For failed live probes, doctor takes a bounded diagnostic from stderr, or stdout when stderr is empty; stdout-only quota failures are therefore visible. Claude read-only calls allow built-in read tools and separately deny `mcp__*`, because Claude's `--tools` flag does not restrict configured MCP tools. Full-access workers retain MCP access. [Claude CLI reference](https://code.claude.com/docs/en/cli-reference).

## Sessions

`--session <name>` gives a named durable conversation: the first call creates it, later calls continue it with full context.

```bash
yoyo ask codex --session auth-review --role review --cwd "$PWD" "Review the auth changes."
yoyo ask codex --session auth-review "Is the middleware.py issue you flagged fixed now?"
yoyo sessions list
yoyo sessions rm codex:auth-review
```

The name → backend-session-id mapping lives in `$YOYO_STATE_DIR/sessions.json`; `sessions rm` removes only the mapping. Sessions work with `codex`, `claude`, and `pi`; on-demand and custom agents reject `--session`. Concurrent calls update the registry under a lock and replace it atomically, so two first calls cannot drop each other's mapping and an interrupted write cannot leave invalid JSON. A session is one vendor's memory; to bring a second vendor into the same conversation, use a thread.

## Threads

A thread is a conversation yoyo owns, which several agents can be invited into. It is a local record, not a live channel: each turn is one ordinary `yoyo ask AGENT --thread NAME` call — a fresh agent process that receives a bounded copy of the history — and its answer is recorded for the next speaker. Agents never talk to each other. Nothing an agent writes (`@claude`, "continue", "DONE") schedules a turn; you choose who speaks next, after reading what was said. Because every turn is a fresh process, threads work with every agent, including the one-shot ones `--session` cannot resume.

```bash
yoyo thread create design --agents codex,claude --seed brief.md --cwd "$PWD"
yoyo ask codex  --thread design --background "Propose the migration. Name the riskiest step."
yoyo ask claude --thread design "Find the strongest flaw in codex's plan. Cite file/line."
yoyo thread note design "Verified: the lock path codex flagged is real (bin/yoyo:3453)."
yoyo thread invite design grok            # grok's turns will receive this history
yoyo ask grok --thread design "Break the tie between the two plans."
yoyo thread show design                   # every turn, full text; --json for structure
yoyo thread list
yoyo thread rm design
```

- **Members.** Only invited agents can take a turn; asking anyone else fails before launch and names the invite command. Inviting says that the agent's turns will carry the thread's history to its vendor. `uninvite` removes a member.
- **Brief and notes.** `--seed FILE` opens the thread with a brief (its size and sha256 are recorded). `thread note NAME "..."` (or stdin) adds caller text — a verified fact, a correction — without calling an agent. Notes are allowed while a turn runs.
- **What a turn receives.** The role preamble and skills, then the history, then a line saying which turn this is and who else will read the answer, then your task. The history is packed deterministically, with no model step, inside `--history-bytes` (default 32768 UTF-8 bytes, or `YOYO_THREAD_HISTORY_BYTES`; at least 1024): the opening turn (the brief or first question) whole up to half the budget; then the newest turns, up to six, whole — the newest is always included, cut at a UTF-8 boundary if it must be, and the first older turn that does not fit stops the walk; then first-line excerpts of older turns while room remains, with a count of any left out. It renders in chronological order. Each turn sits between `<<<TURN n nonce>>>` / `<<<END TURN n nonce>>>` lines and the older-turn summaries between `<<<EARLIER nonce>>>` / `<<<END EARLIER nonce>>>`, with a random marker that occurs in none of the text, under a line framing it all as context from the caller and other agents, not instructions — no agent-written byte reaches the prompt outside a fence. That framing is an instruction to a model, not an enforcement boundary — hence the next point. Piped stdin and `--file` contents reach that turn's agent but are not copied into the history; their file names are.
- **Permissions.** A thread turn is **read-only by default**, because its prompt carries other agents' unverified output. `--write` grants full access for that one turn; nothing inherits it. `--thread` rejects `--agent-arg` (a raw backend flag could override the turn's permission mode), `--session`, `--raw`, comma fan-outs, and `--judge`.
- **One speaker at a time.** A turn holds the thread's lock for its whole run. A second turn started meanwhile fails at once, naming the turn, agent, and pid it is waiting on — it does not queue, because it would answer a history it never saw. Each turn runs in the thread's cwd; a conflicting `--cwd` is refused.
- **Records.** `$YOYO_STATE_DIR/threads/NAME/` (directory 0700, files 0600, written atomically) holds `manifest.json` and one `turns/NNNNNN.json` per turn: question, answer, agent, requested model, permission mode, status, exit code, duration, the call's run id, and `context` — which turns were delivered `full`, `truncated`, `excerpted`, or `omitted`, and the delivered byte count. Status is `answered`, `failed`, `timed_out`, `interrupted`, or `running`. A turn whose yoyo process is killed is stamped `interrupted` by the signal handler, or by the next turn if the process died too hard to stamp it; an uncertain turn is never re-run. A turn that is not `answered` exits nonzero, including an agent that exits 0 with an empty answer, which is recorded `failed` and exits 1. `--background` works: `yoyo wait` returns the answer and the turn is recorded by the detached child.

### Following a thread live

```bash
yoyo thread watch design                  # the thread so far, then each turn as it happens; Ctrl-C stops watching
yoyo thread watch design --pane           # the same view in a new pane beside you (tmux or Herdr)
yoyo thread watch design --events         # one line per event, for a caller to follow
yoyo thread watch design --json --from 5  # the events as JSON lines, starting at turn 5
```

`watch` only reads: it takes no lock and never writes a turn, so it can run beside any turn. The default view prints each turn in full, then follows: a turn's question when it starts, its answer and status when it ends, notes as they are recorded, and membership changes. While a turn runs, a codex turn shows one line per command and message, condensed from its live log; agents that send their answer only at the end (claude, pi) show a heartbeat every 30 seconds. A running turn records its call's run id when the call starts, so the watcher finds that log. Agent text is printed without terminal escape sequences, other control characters, or bidi overrides; `thread show` strips them too.

`--events` prints one line per event (`design #3 claude answered (3m10s): <first line>`), and `--json` turns them into JSON lines with `thread`, `event` (`brief`, `note`, `started`, `finished`, `members`, `removed`), `turn`, `agent`, `status`, `duration_s`, and `text`. Turns that already finished replay as `finished` events; `--from N` skips earlier turns. The watch ends when the thread is removed (exit 0), after `--timeout SECONDS`, or on Ctrl-C; `--poll` sets the check interval (default 1s).

`--pane` opens the view next to the caller and returns, leaving focus in the caller. The new pane goes to the right of a wide pane and below a narrow or tall one. It goes right only when the caller has at least twice as many columns as rows (a cell is about twice as tall as wide) and each side keeps 60 columns. If the size cannot be read, it goes right. Inside tmux (`TMUX` set) it reads the size with `tmux display-message`, runs `tmux split-window -h -d` (right) or `-v -d` (below) in the thread's cwd, and titles the pane. Inside Herdr (`HERDR_PANE_ID` set) it reads the size with `herdr pane layout --current`, runs `herdr pane split --current --direction right|down`, starts the watch in the new pane with `--notify`, and renames the pane; `--notify` shows a Herdr notification when a turn finishes. The innermost multiplexer wins when both are set. Otherwise `--pane` exits 1 and prints the command to run in another terminal. A custom `YOYO_STATE_DIR` is passed to the pane explicitly, because a pane starts from the multiplexer's environment.

### Handing a thread to another tool

```bash
yoyo thread export design                       # markdown transcript (default)
yoyo thread export design --format json --out design.json
yoyo thread export design --format brief        # the packed history plus a "continue from here" line,
                                                # ready to paste into any chat app or agent session
yoyo thread create design-2 --from-file design.json --agents claude --cwd "$PWD"
```

Export makes no model call. `--format brief` uses the same packer a turn gets (`--history-bytes` applies). Import validates the export (schema, kinds, string types, at most 1000 turns, at most 2 MB, read with a bounded read so a huge file is refused without being loaded) and rebuilds the turns labeled `imported`; text that is not valid Unicode (a lone `\ud800` escape, undecodable argv bytes in a note) becomes U+FFFD rather than failing a later write; their authorship and status are kept as claims. Imported data cannot set the new thread's members, cwd, permissions, models, or run ids — those come from this machine's flags — and yoyo never opens a path named inside it.

## Image generation

`yoyo imagegen` generates a real raster image with GPT-image (`gpt-image-2`) by delegating to the agent's native image-generation capability — for the default agent (codex) that is the bundled imagegen skill's built-in `image_gen` tool, which runs headlessly in `codex exec` on the signed-in ChatGPT plan (no API key). A run takes ~1–2 minutes:

```bash
yoyo imagegen "Hand-drawn flowchart, four boxes SPEC/BUILD/GATE/REVIEW, bold arrows." --out flow.png --size 1536x1024 --quality high
yoyo imagegen "make the background white" --edit flow.png --out flow-v2.png
```

No `OPENAI_API_KEY` or `uv` needed — codex renders on its own ChatGPT-subscription auth. yoyo verifies the artifact deterministically: the file must exist, have changed, start with the right magic bytes for its extension, and have a plausible size.

## Custom agents

Override or add agents in `~/.config/yoyo/agents.json`:

```json
{
  "agents": {
    "local": { "command": ["python3", "/path/to/agent.py"], "read_only_args": ["--read-only"], "full_access_args": ["--write"] },
    "codex": { "kind": "codex", "command": ["/custom/path/codex", "exec"] },
    "slow": { "command": ["slow-agent"], "buffers_output": true },
    "echoer": "cat"
  }
}
```

`read_only_args`/`full_access_args` are appended based on `--read-only`; a custom agent with no `read_only_args` makes `--read-only` fail loudly rather than pretend. Use `kind` to keep a built-in's flag behavior with a custom path. An override named after a built-in inherits whether that agent buffers its output until it exits; set `"buffers_output": true` or `false` to say so outright, which is what the `--idle-timeout` warning reads. Or set `YOYO_AGENT_<NAME>=<command>` in the environment. Custom agents receive the rendered prompt on stdin. `--agent-arg` appends a raw argument — verify with `--dry-run` when combined with `--read-only`.

## Access model

`yoyo ask` and `yoyo research` default to full access so agent-to-agent calls don't stop for approval:

- Codex: `--sandbox danger-full-access --ask-for-approval never`
- Claude: `--permission-mode bypassPermissions`
- Pi: read, grep, find, ls, bash, edit, write tools
- Muse: `--disable-approval`, with its default sandbox retained

`--read-only` constrains a reviewer — Codex gets `--sandbox read-only`; Claude, Pi, Grok, and Muse get read-oriented tool allowlists; Cursor and agy use native plan policies (agy with permission prompts auto-denied and its terminal sandbox on). It's enforced by the target agent's own mechanism, not by yoyo; treat it as a strong default, not an airtight sandbox. `yoyo doctor --live` checks that a requested write actually fails to land, because a CLI can accept its read-only flags and still let one through. Agent output is not truth — verify it with code, tests, docs, or live state before acting. A reviewer or checker never gates control flow on its own.

## Nested delegation

yoyo installs its skill into every agent home, so a delegate can call yoyo again. Each agent yoyo launches inherits `YOYO_DEPTH` (one more than its caller's), `YOYO_PARENT_TRACE_ID`, and `YOYO_PARENT_RUN_ID`, and every run record stores `depth`, `parent_trace_id`, and `parent_run_id`, so a nested call in the ledger names the call that launched it. `ask`, `loop`, `review`, `research`, `imagegen`, and `doctor --live` refuse to delegate at `YOYO_DEPTH` ≥ `YOYO_MAX_DEPTH` (default 3: you → agent → agent → agent), before spending anything; `--dry-run` and the commands that launch no agent work at any depth. This is a guard against runaway recursion, not a security boundary — anything with a shell can rewrite its own environment.

## Durability

Every call carries a trace ID (in the prompt metadata, JSON result, and stderr). The per-call metadata line rides at the *end* of the prompt so the stable prefix (role, skills, spec/brief, task) can hit vendor prompt caches across repeated calls. Emitted JSON envelopes carry a single `stderr` field — the informative one (on codex success the raw capture is the reasoning transcript and is dropped); the full raw capture stays in the runs ledger for background runs. Subprocess output is captured to temp files and capped by `--max-output-bytes`; stdin + `--file` share `--max-input-bytes`. stdin is read only when input is actually available, so an idle stdin can't hang a call (`--stdin-wait` for slow producers, `--no-stdin` to ignore). Timeouts kill the process group; SIGINT/SIGTERM/SIGHUP and normal exit terminate in-flight agents so an interrupted yoyo doesn't orphan children. If a real review times out, treat it as unavailable — never count it as completed.

## Doctor

`yoyo doctor` checks that agent binaries exist. `yoyo doctor --live` fires a real probe through each agent in both read-only and full-access mode, exercising the exact flag paths yoyo depends on — run it after upgrading a CLI. The read-only probe asks the agent to create a file in a throwaway directory and fails if the file lands (`enforced: false` in `--json`), because a CLI can accept its read-only flags and still let a write through — that is how agy 1.1.27's plan mode under `--dangerously-skip-permissions` was caught. An agent that answers the temptation by calling a tool its read-only mode denies, and so ends the run with no answer (agy does this), still passes with a `note` saying so: no file landed, and that is what a real read-only call that needs a denied tool looks like.

```bash
yoyo doctor --live
yoyo doctor --live --agent codex --timeout 60 --json
yoyo doctor --live --strict   # exit 1 on any failed probe or missing agent
```

## Test

```bash
python3 -m unittest discover -s tests
```
