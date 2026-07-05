# yoyo reference

The full flag-level reference. For the tour, see the [README](../README.md).

## Agents

| Agent | Command | Notes |
| --- | --- | --- |
| `codex` | `codex exec` | Default reviewer/second opinion; powers `imagegen` |
| `claude` | `claude -p` | Default worker; only agent that reports per-call cost (so `loop --budget-usd` is enforced here) |
| `pi` | `pi -p --mode text` | Lightweight and cheap |
| `cursor` | `cursor-agent -p` | On-demand. Cross-vendor model picker (`--model gpt-5`, `sonnet-4`, …) |
| `agy` | `agy` | On-demand. Google Antigravity (Gemini CLI successor). **Full-access only** — no read-only mode |
| `grok` | `grok` | On-demand. A fourth independent vendor for adversarial cross-checks |

`codex`, `claude`, and `pi` support `--session` follow-ups and are the battle-tested defaults. The on-demand agents are one-shot only — reach for them when a specific edge fits (a model the others don't expose, a third vendor to break a tie). On-demand agents authenticate through their own CLIs.

Built-in agents depend on specific CLI flags (codex's `exec`/`--sandbox`/`--output-last-message`; claude's `-p`/`--permission-mode`/`--tools`; pi's `--mode`/`--tools`). yoyo doesn't detect CLI versions, so a renamed flag surfaces as an agent error — run `yoyo doctor --live` after upgrading a CLI to catch drift early.

## Ask

`yoyo ask` is one-shot and full-access by default (so agent-to-agent calls don't stall on permission prompts). `--role` defaults to `opinion`; pass `review` or `worker` for those behaviors. Use `--read-only` for a bounded reviewer or untrusted input.

```bash
yoyo ask claude --role opinion "Challenge this design and list failure modes."
yoyo ask codex --role review --read-only --cwd "$PWD" --file bin/yoyo "Find correctness bugs and missing tests."
yoyo ask pi --role worker --cwd "$PWD" "Fix the failing test. Don't touch unrelated files."
git diff | yoyo ask claude --role review --cwd "$PWD" "Review this diff against the worktree."
```

**Fan-out (best-of-n).** A comma-separated agent list runs the same prompt on every agent in parallel. Add `--judge <agent>` to have an independent judge compare the answers on correctness/evidence/completeness and recommend the best (or a merge); `--judge-prompt "..."` replaces the judging instructions (used verbatim; the task and candidate answers are appended). Without `--judge`, you get all answers in tagged sections and judge them yourself. The judge always runs **read-only** — its prompt embeds untrusted candidate output — so the judge agent must support a read-only mode (not `agy`). Exit is 0 if at least one agent succeeded and a failed judge falls back to the raw answers; scripted callers should use `--json` and inspect per-result `exit_code`s. Repeating an agent (`codex,codex`) is allowed — that's a self-consistency sample. Prefer a judge that isn't among the candidates: models measurably favor their own answers.

```bash
yoyo ask codex,claude --cwd "$PWD" "Design the rate limiter. Name the riskiest assumption." --judge grok
yoyo ask codex,claude --json "..."   # results array + judge in one envelope
yoyo ask codex,claude,grok --judge cursor --judge-only "..."   # verdict only; raw answers go to files
```

**`--judge-only`** keeps a judged fan-out from flooding the caller's context: the raw candidate answers are written to files under `$YOYO_STATE_DIR/fanout/<trace>/` and only the judge's verdict (plus the file paths) is returned. In JSON mode each result's inline `stdout` is emptied and a `stdout_file` path is added, and the envelope gains `answers_dir`. A failed or skipped judge falls back to returning the raw answers inline — the caller still needs them.

**Steer output with a skill.** `--skill <name>` (repeatable) injects a named `SKILL.md` into the prompt as guidance — the skill says *how*, the prompt says *what*. Names resolve by directory from `YOYO_SKILL_PATH`, then `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills`, `~/.config/opencode/skills`, and Pi's skills dir; a missing skill fails loudly. Discover with `yoyo skills`. A name containing a path separator is treated as an explicit path — a markdown rules file or a directory holding a `SKILL.md` — so overlay rulesets (e.g. a [ponytail](https://github.com/DietrichGebert/ponytail)-style minimal-code ladder as a "senior engineer mode") inject without installing anything: `--skill ./rules/ponytail.md`. Relative skill paths resolve against the process working directory (not `--cwd`) — prefer absolute paths in scripts and background runs. `YOYO_DEFAULT_SKILLS` (comma-separated names) injects skills into every non-`--raw` call without the flag — a standing guidance overlay (e.g. a discipline ruleset for all delegations); duplicates of explicit `--skill` names are dropped, and an unresolvable default is skipped with a stderr warning instead of failing the call.

**Structured findings without a schema.** When the caller wants machine-readable findings, ask for them in the prompt as [TOON](https://github.com/toon-format/toon) rows (`findings[N]{file,line,severity,claim}:` — YAML-style nesting, CSV-style rows, ~40% fewer tokens than JSON on uniform data). This is a prompt convention, not a yoyo feature: yoyo never validates or parses agent output. Use TOON only for tabular lists; prose reads better (and cheaper) as plain markdown.

**Writing good prompts:** put the instruction first; attach context with `--cwd`/`--file`/stdin; state the success criterion, scope, and exclusions; ask reviewers to falsify ("find the strongest reason this is wrong"); replace vague words with observable criteria. Let the worktree be the source of truth.

**Other flags:** `--raw` sends the prompt verbatim (no role/context wrapper) so a leading `/command` reaches the target CLI; `--json` emits a result envelope; `--trace-id` tags a call; `--model` passes a model through; `--max-output-bytes` / `--max-input-bytes` cap output and (stdin + `--file`) input. Calls default to a four-hour timeout — a hung-process deadman guard, not a progress budget (`YOYO_TIMEOUT` or `--timeout` to change; workflow specs can also set `timeout` per job/phase, with no overall workflow cap). A periodic stderr heartbeat keeps a working agent from looking hung (`--quiet` to disable); `--idle-timeout` adds a no-output hang guard for agents that stream — the better stall detector for long-running work.

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
yoyo review --agents codex,claude,grok --json    # three vendors (review needs read-only, so not agy)
yoyo review --base main --pr                     # review committed work, post as a PR comment via gh
```

A dirty tree reviews `git diff HEAD`; a clean tree reviews `<base>...HEAD` (`--base` auto-detects origin HEAD, then `main`, then `master`). The synthesizer (`--synthesizer`, default: first of `--agents`) splits findings into **CONSENSUS** (raised by ≥2 reviewers) and **SINGLE-REVIEWER** (unconfirmed). One reviewer failing is reported and the rest proceed; only all reviewers failing exits non-zero. Untracked files aren't in a git diff, so they're listed in the prompt for reviewers to read.

## Loop

`yoyo loop` runs one task as a sequence of independent fresh-context iterations. A long-lived session re-reads its whole growing context on every tool call, so cost compounds; a loop makes each iteration a brand-new session with empty context — continuity lives in a small state file (default `.yoyo/loop-state.md`) the agent reads first and rewrites before ending.

```bash
yoyo loop claude --cwd "$PWD" --max-iter 30 --budget-usd 20 "Fix the failing tests, one per iteration."
yoyo loop codex,claude --cwd "$PWD" "Refactor module by module."   # rotate vendors across iterations
```

A comma-separated agent list rotates vendors iteration by iteration: each fresh context gets a different model's eyes on the same state file, so one vendor's blind spots don't compound. The loop ends on the first of: a `STOP` file beside the state file, an accepted `STATUS: DONE`, `--max-iter` (default 20), `--budget-usd` (enforced for cost-reporting agents — claude), or `--max-fail` consecutive failures (default 3 — the only exit-1 ending). `--role` (default `worker`), `--skill`, `--read-only`, and byte caps pass through; `--background` detaches the whole loop.

### Work queue (`--queue FILE`)

A markdown checklist turns the loop from one fuzzy goal into N crisply checkable increments:

```bash
yoyo loop claude --cwd "$PWD" --queue tasks.md --gate "pytest -q" "Work through the queue."
```

`tasks.md` holds `- [ ] item` lines (free text around them is ignored; fenced code blocks are skipped). Each iteration is *instructed* to complete exactly ONE unchecked item and mark it `- [x]` in the file — pacing is guidance, not enforced; what is enforced is completion: a `STATUS: DONE` claim is mechanically rejected while any box is unchecked (the rejection lists the remaining items in the state file). Verification reads the whole file uncapped and **fails closed**: an unreadable queue, or one rewritten without any checklist items, rejects DONE rather than passing it. The queue file must exist and contain at least one checklist item at start. Note the worker owns the queue file — it *could* check boxes falsely — so a queue alone never sets `verified` in the loop summary; pair it with `--gate`/`--checker` for independent verification. The summary reports `queue_rejections`.

### Shared brief (`--brief FILE`)

Fresh-context iterations re-derive the same repo knowledge — conventions, layout, build/test commands — every time. A brief stops that:

```bash
yoyo ask claude --read-only --cwd "$PWD" \
  "Write a dense brief for agents working in this repo: layout, conventions, build/test commands, gotchas. Under 150 lines." \
  > .yoyo/brief.md
yoyo loop codex,claude --cwd "$PWD" --brief .yoyo/brief.md --queue tasks.md "Work through the queue."
```

The brief is injected read-only into every iteration (workers are told not to edit it and not to re-derive what it records), re-read each iteration so you can regenerate it between runs, and placed in the stable prompt-prefix region so it can hit vendor prompt caches. Keep it dense — it rides in every iteration's prompt, so its token budget is the design constraint. Regenerating when the repo changes materially is the caller's call, not yoyo's. For one-shot fan-outs (`ask`, `research`), pass the same file with `--file` so parallel agents don't each re-explore the repo.

### Verified completion (opt-in)

By default the agent grades its own `STATUS: DONE`, which drifts toward "done enough". These flags make `DONE` a candidate that must clear an objective check:

- **`--gate CMD`** (repeatable): a shell command (test/lint/build). On `DONE`, every gate runs from `--cwd`; any non-zero exit strips the false `DONE`, appends the failure to the state file, and continues. Gates come only from this flag, never the agent-rewritable state file.
- **`--checker AGENT`**: an independent read-only check, blind to the worker's prose — it gets the goal, spec, and `git diff`, re-derives whether the repo meets the goal, and ends with `VERDICT: PASS`/`FAIL`. It fails closed. `--checker-model` picks a cheaper tier.
- **`--done-policy`** (`worker`/`gate`/`checker`/`gate+checker`) is auto-derived from the flags.
- **`--spec PATH`**: a standing spec re-read every iteration and never rewritten — holds constraints the lossy state rewrite would otherwise drop.

State-file guards: a `.task` sidecar makes reusing a state file recorded for a different task fail loudly; a leftover `STOP` file is cleared at startup; a `.lock` (flock) rejects a second concurrent loop. For claude, iterations run with `--output-format json` so cost is real.

**Cost levers:** for mechanical work pass `--model sonnet` (or keep Opus with `--agent-arg=--effort=low`), and add `--agent-arg=--setting-sources=project` to drop user-level config (one measured setup: $0.94 → $0.15 per iteration).

## Cron

`yoyo cron` schedules any yoyo command through the user's crontab — no daemon, no new runtime. PATH is captured at add time so agent CLIs resolve under cron's minimal environment; output appends to a per-entry log.

```bash
yoyo cron add nightly --schedule "0 2 * * *" --cwd "$PWD" -- loop claude --max-iter 5 --gate "pytest -q" "Work through TODO.md"
yoyo cron list            # entries + whether each is still installed in crontab
yoyo cron run nightly     # run one now, foreground
yoyo cron rm nightly
```

Schedules are five cron fields or a macro (`@hourly`, `@daily`, `@weekly`, …). Entries live in the crontab (tagged `# yoyo-cron:<name>`) plus a registry at `$YOYO_STATE_DIR/cron.json`; logs default to `$YOYO_STATE_DIR/cron/<name>.log`. The scheduled command must itself be a yoyo subcommand — cron runs yoyo, yoyo runs the agents.

The natural pairing is a scheduled loop: the state file carries progress between runs, the flock prevents overlapping runs, and once the task is verified `DONE` later runs are cheap no-ops. Give unattended loops a `--gate` or `--checker`. On macOS, grant `cron` Full Disk Access if scheduled jobs can't read your repo. Crontab edits are read-modify-write: don't run several `yoyo cron add/rm` at the same moment (or edit `crontab -e` concurrently), or an entry can be lost.

## Background runs

A real review or worker task can outlive the caller's tool budget. `--background` detaches the run into a durable ledger and returns immediately:

```bash
run_id=$(yoyo ask codex --role review --cwd "$PWD" --background "Audit the auth module.")
yoyo runs list
yoyo wait "$run_id"            # blocks until done, exits with the run's exit code
yoyo runs show "$run_id" --json
yoyo runs prune --days 7
```

`yoyo wait` exit codes: 124 = still running (wait again); 0 = success (result on stdout); anything else = failed or died. Runs live under `$YOYO_STATE_DIR/runs/<run_id>/` (default `~/.local/state/yoyo`). Argument validation still happens in the foreground, so a bad agent name fails loudly before detaching.

## Sessions

`--session <name>` gives a named durable conversation: the first call creates it, later calls continue it with full context.

```bash
yoyo ask codex --session auth-review --role review --cwd "$PWD" "Review the auth changes."
yoyo ask codex --session auth-review "Is the middleware.py issue you flagged fixed now?"
yoyo sessions list
yoyo sessions rm codex:auth-review
```

The name → backend-session-id mapping lives in `$YOYO_STATE_DIR/sessions.json`; `sessions rm` removes only the mapping. Sessions work with `codex`, `claude`, and `pi` (and `chat`); on-demand and custom agents reject `--session`.

## Image generation

`yoyo imagegen` generates a real raster image with GPT-image via codex's bundled image CLI (`gpt-image-2`), written straight to `--out` in ~15–25s:

```bash
yoyo imagegen "Hand-drawn flowchart, four boxes SPEC/BUILD/GATE/REVIEW, bold arrows." --out flow.png --size 1536x1024 --quality high
yoyo imagegen "make the background white" --edit flow.png --out flow-v2.png
```

Needs `OPENAI_API_KEY` and `uv` on PATH. yoyo verifies the artifact deterministically: the file must exist, have changed, start with the right magic bytes for its extension, and have a plausible size.

## Workflows

`yoyo workflow` runs a saved multi-agent pipeline from a JSON spec — for orchestrations that have stabilized and are worth rerunning by name. It runs phases in order, jobs within a phase in parallel up to `max_concurrency`, and returns a JSON result. For one-off coordination, orchestrate directly with `ask`/`research`/`review` instead — a live orchestrator can branch on results; a spec can't.

```bash
yoyo workflow --list
yoyo workflow cross-review --input "review the current branch diff" --json
yoyo workflow ./workflow.json --input "audit auth routes" --dry-run --json
```

A bare name resolves against `YOYO_WORKFLOW_PATH`, then `~/.config/yoyo/workflows/`, then the source `workflows/` dir. Bundled templates: `cross-review`, `adversarial-audit`, `frontend-impl-review`.

```json
{
  "name": "review-and-cross-check",
  "defaults": { "agent": "claude", "role": "opinion", "read_only": true },
  "phases": [
    { "name": "fanout", "jobs": [
      { "id": "readme", "agent": "codex", "prompt": "Audit README.md.", "files": ["README.md"] },
      { "id": "cli", "prompt": "Audit bin/yoyo for safety issues.", "files": ["bin/yoyo"] }
    ]},
    { "name": "cross-check", "jobs": [
      { "id": "reviewer", "role": "review", "include_previous": true,
        "prompt": "Cross-check the findings. Reject weak claims; list only concrete issues." }
    ]}
  ]
}
```

Common job fields: `agent`, `model`, `role`, `files`, `skill`, `for_each` (expand one template into many), `include_previous` / `include_phases` (feed prior outputs into a later job), `retries` (transient failures), `agent_args`. Phase-level `gates` run shell commands after a phase's jobs succeed — real evidence checks (tests, linters, builds); the first failure stops the workflow. That's the whole deterministic surface: yoyo never grades agent output.

Feeding one agent's output into a write-capable or raw-`agent_args` job requires `allow_untrusted_context: true` — previous output is untrusted text. Misconfigured specs fail loudly at validation, before any tokens are spent.

## Custom agents

Override or add agents in `~/.config/yoyo/agents.json`:

```json
{
  "agents": {
    "local": { "command": ["python3", "/path/to/agent.py"], "read_only_args": ["--read-only"], "full_access_args": ["--write"] },
    "codex": { "kind": "codex", "command": ["/custom/path/codex", "exec"] },
    "echoer": "cat"
  }
}
```

`read_only_args`/`full_access_args` are appended based on `--read-only`; a custom agent with no `read_only_args` makes `--read-only` fail loudly rather than pretend. Use `kind` to keep a built-in's flag behavior with a custom path. Or set `YOYO_AGENT_<NAME>=<command>` in the environment. Custom agents receive the rendered prompt on stdin. `--agent-arg` appends a raw argument — verify with `--dry-run` when combined with `--read-only`.

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
