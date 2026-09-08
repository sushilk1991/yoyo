# yoyo reference

The full flag-level reference. For the tour, see the [README](../README.md).

## Agents

| Agent | Command | Notes |
| --- | --- | --- |
| `codex` | `codex exec` | Default reviewer/second opinion; powers `imagegen` |
| `claude` | `claude -p` | Default worker for scoped edits |
| `pi` | `pi -p --mode text` | Lightweight and cheap |
| `cursor` | `cursor-agent -p --output-format stream-json` | On-demand worker and model picker (`--model composer-2.5`, `cursor-grok-4.5-high`, …) |
| `agy` | `agy` | On-demand. Google Antigravity (Gemini CLI successor). Supports `--read-only` via plan mode (`--mode plan`). yoyo forwards `--timeout` as `--print-timeout` (agy's own default is 5m) |
| `grok` | `grok` | On-demand. A fourth independent vendor for adversarial cross-checks |

`codex`, `claude`, and `pi` support `--session` follow-ups and are the battle-tested defaults. The on-demand agents are one-shot only — reach for them when a specific edge fits (a model the others don't expose, a third vendor to break a tie). On-demand agents authenticate through their own CLIs.

Built-in agents depend on specific CLI flags (codex's `exec`/`--sandbox`/`--output-last-message`; claude's `-p`/`--permission-mode`/`--tools`; pi's `--mode`/`--tools`). yoyo doesn't detect CLI versions, so a renamed flag surfaces as an agent error — run `yoyo doctor --live` after upgrading a CLI to catch drift early.

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

## Ask

`yoyo ask` is one-shot and full-access by default (so agent-to-agent calls don't stall on permission prompts). `--role` defaults to `opinion`; pass `review` or `worker` for those behaviors. Use `--read-only` for a bounded reviewer or untrusted input.

```bash
yoyo ask claude --role opinion "Challenge this design and list failure modes."
yoyo ask codex --role review --read-only --cwd "$PWD" --file bin/yoyo "Find correctness bugs and missing tests."
yoyo ask pi --role worker --cwd "$PWD" "Fix the failing test. Don't touch unrelated files."
git diff | yoyo ask claude --role review --cwd "$PWD" "Review this diff against the worktree."
```

**Fan-out (best-of-n).** A comma-separated agent list runs the same prompt on every agent in parallel. Add `--judge <agent>` to have an independent judge compare the answers on correctness/evidence/completeness and recommend the best (or a merge). The default judge verdict leads with a **convergence/divergence map**: what the answers independently agree on, then every genuine conflict with the narrowest check that would settle it — divergence is the caller's verification work list, and cross-vendor agreement is treated as signal, not proof. `--judge-prompt "..."` replaces the judging instructions (used verbatim; the task and candidate answers are appended). Without `--judge`, you get all answers in tagged sections and judge them yourself. The judge always runs **read-only** — its prompt embeds untrusted candidate output — so the judge agent must support a read-only mode. Exit is 0 if at least one agent succeeded and a failed judge falls back to the raw answers; scripted callers should use `--json` and inspect per-result `exit_code`s. Repeating an agent (`codex,codex`) is allowed — that's a self-consistency sample. Prefer a judge that isn't among the candidates: models measurably favor their own answers.

```bash
yoyo ask codex,claude --cwd "$PWD" "Design the rate limiter. Name the riskiest assumption." --judge grok
yoyo ask codex,claude --json "..."   # results array + judge in one envelope
yoyo ask codex,claude,grok --judge cursor --judge-only "..."   # verdict only; raw answers go to files
```

**Co-citation.** Every fan-out with at least two successful answers ends with a `cited by more than one agent` block listing the `file:line` sites that appear in more than one answer, matched on line and on the path's trailing segments, so `bin/yoyo:1338` and the absolute path count as the same site while `src/auth.py:42` and `tests/auth.py:42` stay separate (`co_cited` in `--json`). Across 88 real fan-outs replayed from the local ledger, 52% had at least one shared site. It says two agents looked at the same line — not that either is right, and not that a site cited once is wrong. Each site carries what is on disk at that line right now — the line's text, `no such file under the run cwd`, or `the file has N lines` — as evidence, never a verdict: yoyo does not grade an answer, and a full-access agent may have edited the file between citing it and the read. Agents spell one site several ways, so when the spelling that resolves is not the one displayed the note names the path it actually read (`read_as`). An answer is untrusted text, so a cited path that resolves outside `--cwd` is reported as not read rather than opened (`disk` in `--json`).

**`--judge-only`** keeps a judged fan-out from flooding the caller's context: the raw candidate answers are written to files under `$YOYO_STATE_DIR/fanout/<trace>/` and only the judge's verdict (plus the file paths) is returned. In JSON mode each result's inline `stdout` is emptied and a `stdout_file` path is added, and the envelope gains `answers_dir`. A failed or skipped judge falls back to returning the raw answers inline — the caller still needs them.

**Steer output with a skill.** `--skill <name>` (repeatable) injects a named `SKILL.md` into the prompt as guidance — the skill says *how*, the prompt says *what*. Names resolve by directory from `YOYO_SKILL_PATH`, then `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills`, `~/.config/opencode/skills`, and Pi's skills dir; a missing skill fails loudly. Discover with `yoyo skills`. A name containing a path separator is treated as an explicit path — a markdown rules file or a directory holding a `SKILL.md` — so overlay rulesets (e.g. a [ponytail](https://github.com/DietrichGebert/ponytail)-style minimal-code ladder as a "senior engineer mode") inject without installing anything: `--skill ./rules/ponytail.md`. Relative skill paths resolve against the process working directory (not `--cwd`) — prefer absolute paths in scripts and background runs. **Inbuilt default: `yoyo-fable-mode`.** yoyo ships a delegate harness inside its own bundle — evidence discipline for one-shot work — and injects it into **every non-`--raw` call by default**, whichever agent is called. It needs no install step: it resolves from the yoyo checkout (or the recorded source of an installed binary). The name is `yoyo-`prefixed deliberately. User skill roots are searched *before* the bundle, so the earlier bare name `fable-mode` was shadowed by whatever personal skill the caller happened to have installed — typically one written for an interactive editor session, which told every delegate it was "running as Opus". `YOYO_DEFAULT_SKILLS` (comma-separated) replaces the default set, and the empty string disables injection; duplicates of explicit `--skill` names are dropped, and an unresolvable default is skipped with a stderr warning rather than failing the call.

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

A comma-separated agent list rotates vendors iteration by iteration: each fresh context gets a different model's eyes on the same state file, so one vendor's blind spots don't compound. The loop ends on the first of: a `STOP` file beside the state file, an accepted `STATUS: DONE`, `--max-iter` (default 20), or `--max-fail` consecutive crashed iterations (default 3, the only exit-1 ending). `--skill`, `--read-only`, `--idle-timeout`, `--model`, and byte caps pass through; `--background` detaches the whole loop.

`--spec PATH` pins a standing spec: re-read every iteration, never rewritten, holding the constraints the lossy state rewrite would otherwise drop.

State-file guards: a `.task` sidecar makes reusing a state file recorded for a different task fail loudly; a leftover `STOP` file is cleared at startup; a `.lock` (flock) rejects a second concurrent loop.

`STATUS: DONE` is whatever the worker writes. Read the diff before acting on it.

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
yoyo wait "$run_id"            # blocks until done, exits with the run's exit code
yoyo runs show "$run_id" --json
yoyo runs prune --days 7
```

`yoyo wait` exit codes: 124 = still running (wait again); 0 = success (result on stdout); anything else = failed or died. The 124 path reports proof of life — the poll expired, not the run — with how long the run has been going and the freshest progress on disk: the last heartbeat line from `log.txt`, else the captured byte counts. Under `--json` those arrive as `run_status`, `elapsed_s`, `last_progress`, `stdout_bytes`, `stderr_bytes` alongside the unchanged `run_id` and `status`. Runs live under `$YOYO_STATE_DIR/runs/<run_id>/` (default `~/.local/state/yoyo`). A run reports `running` only while its recorded pid still belongs to the process that started it: the ledger stores the pid's start time alongside it, because a pid on its own is reused, and a run killed hard enough to skip writing its result would otherwise read as `running` forever — polled by `wait` and skipped by `prune`. The check needs both halves, so a record written before this release, or one on a host where `ps` cannot answer, falls back to the pid alone and stays as permissive as it was. Argument validation still happens in the foreground, so a bad agent name fails loudly before detaching.

## The call journal and autopsy

Foreground agent calls are journaled into the same ledger as they execute (`meta.json` at start, `result.json` at exit, live stdout/stderr captures in the run dir), so a call killed by its caller leaves evidence instead of vanishing. If yoyo receives SIGINT/SIGTERM/SIGHUP mid-call, the journal records which signal and when.

```bash
yoyo runs autopsy              # explain how the most recent run ended
yoyo runs autopsy "$run_id"    # or a specific one; --json for structure
```

When prompt capture is enabled and the parent has the prompt, its record keeps a 64 KB head in `prompt.txt` and the exact full size in metadata as `prompt_bytes`, so the autopsy opens with an `asked:` line. That size measures what was stored: the assembled prompt for a foreground call, the command-line question alone for a background parent, whose assembled prompt is built later in the child. Older records, and runs whose prompt is assembled inside a detached child, have no stored prompt and get no `asked:` line. The line reports the caller's own task or nothing: when injected skill documents fill that head the task heading falls outside it, and the autopsy omits the line rather than quoting skill text as the question. It ends where the question does — piped input and `--file` contents are attachments, and never appear in the preview.

The autopsy states, from recorded evidence, whether the run completed, failed, hit a timeout, was signal-killed (naming the signal and elapsed time), or died hard — plus captured byte counts. A run killed at exit 124 names the clock that fired and the value it fired at (`--timeout`, the caller's budget was too small, or `--idle-timeout`, the stream stalled); `--json` carries the same as `timeout_clock` and `timeout_clock_s`, null when the record cannot tell an agent's own 124 apart. Use it before concluding an agent "timed out with no output": most such reports are the caller's own exec-tool budget killing a healthy run (note: `claude -p` writes stdout only at completion, so zero bytes mid-run is normal). Set `YOYO_NO_CALL_JOURNAL=1` to disable foreground call journals and background-parent prompt capture — a background parent still records the argv it spawned, with the question itself blanked; `yoyo runs prune --days N` trims the ledger.

### `yoyo runs audit`

What the ledger says about a window of real calls, per agent: how many, how they ended, and p50/p90 duration.

```bash
yoyo runs audit               # last 7 days
yoyo runs audit --days 60     # or a wider window; --json for structure
```

The ledger nests — one `ask codex,claude --background` writes a background parent plus one call record per agent, and every loop iteration mirrors the call it wraps — so summing directories counts the same work up to three times. The audit reports each cohort's size for coverage and aggregates outcomes from call records alone.

Outcomes stay separate because they need opposite fixes: `hard-timeout` means the caller's budget was too small, `idle-timeout` means the stream stalled, a bare `exit-124` is the agent's own, and `signal` means the caller killed yoyo rather than the agent failing. A record with no `result.json` is `running` if its process is still alive and `no-result` if it died without stamping itself, and one that records no usable exit code is `unreadable`; none of the three joins a rate that would blame an agent for it. The pid is probed only for records that have no result yet, never for the whole ledger. Durations come only from what a run recorded, and percentiles are nearest-rank, so every number is a duration some call actually took. Empty answers are reported as a count against their denominator, not as failures — codex answers via `--output-last-message`, so zero captured bytes is normal for it. There is no cost or token accounting.

`yoyo doctor` reports the effective default skills and the path each resolves from, marking any path outside yoyo's own bundle. It also verifies that the skill copies installed into agent homes (`~/.claude/skills`, `~/.codex/skills`, …) match the bundled source, and flags both drifted copies and `yoyo`-prefixed directories the bundle no longer ships — the strays a rename leaves behind, still resolvable and still winning lookups. Pi reads `~/.agents/skills` alongside its own skills dir, so yoyo installs pi's copy only through the shared dir; a leftover copy inside the pi home is reported as LEGACY, and `yoyo install-skill` removes it when it is byte-identical to the bundle or moves it aside intact otherwise (a foreign skill there is left alone). `doctor --strict` fails on any of these; `yoyo install-skill` resyncs the drift, and an orphan waits for a human to move it aside — the SKILL.md files are yoyo's interface to calling agents, so a stale copy silently degrades that agent's calling patterns.

## Sessions

`--session <name>` gives a named durable conversation: the first call creates it, later calls continue it with full context.

```bash
yoyo ask codex --session auth-review --role review --cwd "$PWD" "Review the auth changes."
yoyo ask codex --session auth-review "Is the middleware.py issue you flagged fixed now?"
yoyo sessions list
yoyo sessions rm codex:auth-review
```

The name → backend-session-id mapping lives in `$YOYO_STATE_DIR/sessions.json`; `sessions rm` removes only the mapping. Sessions work with `codex`, `claude`, and `pi`; on-demand and custom agents reject `--session`.

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

`--read-only` constrains a reviewer — Codex gets `--sandbox read-only`, Claude and Pi get read-oriented tool allowlists. It's enforced by the target agent's own mechanism, not by yoyo; treat it as a strong default, not an airtight sandbox. Agent output is not truth — verify it with code, tests, docs, or live state before acting. A reviewer or checker never gates control flow on its own.

## Durability

Every call carries a trace ID (in the prompt metadata, JSON result, and stderr). The per-call metadata line rides at the *end* of the prompt so the stable prefix (role, skills, spec/brief, task) can hit vendor prompt caches across repeated calls. Emitted JSON envelopes carry a single `stderr` field — the informative one (on codex success the raw capture is the reasoning transcript and is dropped); the full raw capture stays in the runs ledger for background runs. Subprocess output is captured to temp files and capped by `--max-output-bytes`; stdin + `--file` share `--max-input-bytes`. stdin is read only when input is actually available, so an idle stdin can't hang a call (`--stdin-wait` for slow producers, `--no-stdin` to ignore). Timeouts kill the process group; SIGINT/SIGTERM/SIGHUP and normal exit terminate in-flight agents so an interrupted yoyo doesn't orphan children. If a real review times out, treat it as unavailable — never count it as completed.

## Doctor

`yoyo doctor` checks that agent binaries exist. `yoyo doctor --live` fires a real one-line probe through each agent in both read-only and full-access mode, exercising the exact flag paths yoyo depends on — run it after upgrading a CLI.

```bash
yoyo doctor --live
yoyo doctor --live --agent codex --timeout 60 --json
yoyo doctor --live --strict   # exit 1 on any failed probe or missing agent
```

## Test

```bash
python3 -m unittest discover -s tests
```
