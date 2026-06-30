# yoyo

`yoyo` is a tiny, dependency-free CLI for calling one coding agent from another. Codex, Claude, Pi (and configured custom agents) can ask each other for second opinions, reviews, scoped work, deep research, and interactive sessions. Cursor, Antigravity (`agy`), and Grok are built in as on-demand, one-shot agents for when cross-vendor diversity is the point.

The design is deliberately boring: one Python script, subprocess calls, explicit prompts. No daemon, no package manager. Requires Python 3.9+.

## Commands at a glance

| Command | What it does |
| --- | --- |
| `yoyo ask <agent> "..."` | One-shot call: opinion, review, or scoped worker task |
| `yoyo research "..."` | Fan a topic out to agents under different lenses, synthesize a decision brief |
| `yoyo review` | Cross-vendor consensus review of the current git diff |
| `yoyo loop <agent> "..."` | Run a task as repeated fresh-context iterations at flat cost |
| `yoyo chat <agent>` | Interactive session |
| `yoyo workflow <name>` | Run a saved multi-agent workflow from a JSON spec |
| `yoyo imagegen "..." --out f.png` | Generate a real image with GPT-image |
| `yoyo runs` / `wait` / `sessions` / `doctor` / `agents` / `skills` | Manage runs, sessions, and setup |

Output is verifiable, not authoritative: treat any agent's answer as evidence to check, not an oracle.

## Install

```bash
git clone https://github.com/sushilk1991/yoyo.git
cd yoyo
./install.sh
```

This installs `~/.local/bin/yoyo`, the bundled skills (for Codex, Claude, Pi, OpenCode, and compatible skill dirs), workflow templates at `~/.config/yoyo/workflows/`, the `yoyo-imagegen` skill (only when codex is on PATH), and a source pointer for `yoyo update`. Make sure `~/.local/bin` is on `PATH`.

Update later with `yoyo update` (fetch + ff-only pull + reinstall), or `yoyo update --no-pull` to reinstall from the current checkout.

## Agents

| Agent | Command | Notes |
| --- | --- | --- |
| `codex` | `codex exec` | Default reviewer/second opinion; powers `imagegen` |
| `claude` | `claude -p` | Default worker; only agent that reports per-call cost (so `loop --budget-usd` is enforced here) |
| `pi` | `pi -p --mode text` | Lightweight and cheap |
| `cursor` | `cursor-agent -p` | On-demand. Cross-vendor model picker (`--model gpt-5`, `sonnet-4`, …) |
| `agy` | `agy` | On-demand. Google Antigravity (Gemini CLI successor). **Full-access only** — no read-only mode |
| `grok` | `grok` | On-demand. A fourth independent vendor for adversarial cross-checks |

`codex`, `claude`, and `pi` support `--session` follow-ups and are the battle-tested defaults. The on-demand agents are one-shot only — reach for them when a specific edge fits (a model the others don't expose, a third vendor to break a tie), not as a default rotation. On-demand agents authenticate through their own CLIs. Probe any agent with a real call: `yoyo doctor --live --agent <name>`.

Built-in agents depend on specific CLI flags (codex's `exec`/`--sandbox`/`--output-last-message`; claude's `-p`/`--permission-mode`/`--tools`; pi's `--mode`/`--tools`). yoyo doesn't detect CLI versions, so a renamed flag surfaces as an agent error — run `yoyo doctor --live` after upgrading a CLI to catch drift early.

## Ask

`yoyo ask` is one-shot and full-access by default (so agent-to-agent calls don't stall on permission prompts). `--role` defaults to `opinion`; pass `review` or `worker` for those behaviors. Use `--read-only` for a bounded reviewer or untrusted input.

```bash
# Second opinion
yoyo ask claude --role opinion "Challenge this design and list failure modes."

# Read-only review (the repo, not the prompt, is the source of truth)
yoyo ask codex --role review --read-only --cwd "$PWD" --file bin/yoyo "Find correctness bugs and missing tests."

# Scoped worker with write access
yoyo ask pi --role worker --cwd "$PWD" "Fix the failing test. Don't touch unrelated files."

# Pipe context
git diff | yoyo ask claude --role review --cwd "$PWD" "Review this diff against the worktree."

# Break a tie with a different vendor
yoyo ask agy --role opinion --cwd "$PWD" "codex and claude disagree on this migration. Decide, with reasons."
```

**Steer output with a skill.** `--skill <name>` (repeatable) injects a named `SKILL.md` into the prompt as guidance — the main lever for making an unpredictable agent produce consistent results: the skill says *how*, the prompt says *what*. Names resolve by directory from `YOYO_SKILL_PATH`, then `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills`, `~/.config/opencode/skills`, and Pi's skills dir; a missing skill fails loudly. Discover with `yoyo skills`.

```bash
yoyo ask codex --role worker --cwd "$PWD" --skill frontend-design "Build the settings page. Keep it minimal."
```

**Writing good prompts:** put the instruction first; attach context with `--cwd`/`--file`/stdin; state the success criterion, scope, and exclusions; ask reviewers to falsify ("find the strongest reason this is wrong"); replace vague words ("good", "clean") with observable criteria. Let the worktree be the source of truth.

**Other flags:** `--raw` sends the prompt verbatim (no role/context wrapper) so a leading `/command` reaches the target CLI; `--json` emits a result envelope; `--trace-id` tags a call; `--model` passes a model through; `--max-output-bytes` / `--max-input-bytes` cap output and (stdin + `--file`) input. Calls default to a one-hour timeout — a hung-process guard, not a progress budget; don't shorten it for real work. A periodic stderr heartbeat keeps a working agent from looking hung (`--quiet` to disable); `--idle-timeout` adds a no-output hang guard for agents that stream.

## Research

`yoyo research` gathers **diverse perspectives before you decide what to do next** — it does not hunt for one right answer. Each lens runs as one parallel agent call investigating from a single angle; a synthesizer then writes a decision brief: where perspectives converge, where they're in **tension** (the most useful part), key evidence, open questions, and options for next steps.

```bash
yoyo research --cwd "$PWD" "Should we move the core engine to Rust?"
yoyo research --lenses proponent,skeptic,analyst --agents codex,claude --json "Is WebGPU ready for our renderer?"
yoyo research --lenses regulatory,market,security --file rfc.md "Adopt passkeys for login?"
```

Default lenses are `proponent,skeptic,analyst,explorer,pragmatist` — the case for, the case against, first-principles facts, prior art and alternatives, and the execution path. Lenses are assigned to `--agents` round-robin, so by default the for/against split lands on different vendors — a real disagreement, not one model arguing with itself. Unknown lens names become ad-hoc angles, so `--lenses regulatory,market,security` works for domain-specific research. Each lens is one call, so subset `--lenses` to spend less.

Unlike `review`, research defaults to **full-access** so agents can use web search, fetch, and code execution to investigate; `--read-only` restricts them but limits those tools on some agents. `--file` adds shared context to every researcher. The synthesis surfaces disagreement rather than averaging it — verify the load-bearing claims yourself before acting.

## Review

`yoyo review` runs a cross-vendor consensus review of the current git diff: each agent reviews the same diff independently, in parallel, read-only, then a synthesizer merges the results.

```bash
yoyo review --cwd "$PWD"                         # codex + claude in parallel
yoyo review --agents codex,claude,grok --json    # three vendors (review needs read-only, so not agy)
yoyo review --base main --pr                      # review committed work, post as a PR comment via gh
```

A dirty tree reviews `git diff HEAD`; a clean tree reviews `<base>...HEAD` (`--base` auto-detects origin HEAD, then `main`, then `master`). The synthesizer (`--synthesizer`, default: first of `--agents`) splits findings into **CONSENSUS** (raised by ≥2 reviewers — the trustworthy signal) and **SINGLE-REVIEWER** (unconfirmed). One reviewer failing is reported and the rest proceed; only all reviewers failing exits non-zero. Untracked files aren't in a git diff, so they're listed in the prompt for reviewers to read (`git add` them to review their contents). Reviewers must support read-only (built-ins do; custom agents need `read_only_args`).

## Loops

`yoyo loop` runs one task as a sequence of independent fresh-context iterations against a single agent:

```bash
yoyo loop claude --cwd "$PWD" --max-iter 30 --budget-usd 20 "Fix the failing tests, one per iteration."
```

A long-lived session re-reads its whole growing context on every tool call, so cost compounds. A loop makes each iteration a brand-new session with empty context; continuity lives in a small state file (default `.yoyo/loop-state.md`) the agent reads first and rewrites before ending. Per-iteration cost stays flat. `--role` (default `worker`), `--skill`, `--cwd`, `--read-only`, and byte caps pass through. The loop ends on the first of: a `STOP` file beside the state file, an accepted `STATUS: DONE`, `--max-iter` (default 20), `--budget-usd`, or `--max-fail` consecutive failures (default 3 — the only exit-1 ending).

### Verified completion (opt-in)

By default the agent grades its own `STATUS: DONE`, which drifts toward "done enough" over many iterations. These flags make `DONE` a *candidate* that must clear an objective check — yoyo never grades prose, you supply the closed-form evidence:

```bash
yoyo loop claude --cwd "$PWD" --gate "pytest -q" "Fix the failing auth tests."
yoyo loop claude --checker codex --checker-model <cheap-model> "Migrate the DB layer."
yoyo loop claude --gate "make ci" --checker codex "..."   # both — the strongest mode
```

- **`--gate CMD`** (repeatable): a shell command (test/lint/build). On `DONE`, every gate runs from `--cwd`; any non-zero exit strips the false `DONE`, appends the failure to the state file so the next iteration sees why, and continues. Gates come only from this flag, never the agent-rewritable state file.
- **`--checker AGENT`**: an independent read-only check, blind to the worker's prose — it gets the goal, spec, and `git diff`, re-derives whether the repo meets the goal, and ends with `VERDICT: PASS`/`FAIL`. It fails closed. `--checker-model` picks a cheaper tier (but not Haiku — judging is itself a judgment task).
- **`--done-policy`** (`worker`/`gate`/`checker`/`gate+checker`) is auto-derived from the flags, so you rarely set it.
- **`--spec PATH`**: a standing spec re-read every iteration and never rewritten — holds constraints ("don't touch `src/payments/`") that the lossy state rewrite would otherwise drop.

State-file guards: a `.task` sidecar makes reusing a state file recorded for a *different* task fail loudly; a leftover `STOP` file is cleared at startup; a `.lock` (flock) rejects a second concurrent loop and releases when the process exits. For claude, iterations run with `--output-format json` so cost is real and `--budget-usd` is enforced; other agents get a one-time no-budget warning. `--background` detaches the whole loop; `--session` is rejected (fresh context is the point).

**Cost levers:** a fresh `claude -p` session loads tens of thousands of tokens of harness context per call. For mechanical work, pass `--model sonnet` (or keep Opus with `--agent-arg=--effort=low`), and add `--agent-arg=--setting-sources=project` to drop user-level config from already-steered workers (one measured setup: $0.94 → $0.15 per iteration).

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

The name → backend-session-id mapping lives in `$YOYO_STATE_DIR/sessions.json`; `sessions rm` removes only the mapping, not the backend's stored conversation. Sessions work with `codex`, `claude`, and `pi` (and `chat`); on-demand and custom agents reject `--session`.

## Image generation

`yoyo imagegen` generates a real raster image with GPT-image via codex's bundled image CLI (`gpt-image-2`), written straight to `--out` in ~15–25s:

```bash
yoyo imagegen "Hand-drawn flowchart, four boxes SPEC/BUILD/GATE/REVIEW, bold arrows. No other text." --out flow.png --size 1536x1024 --quality high
yoyo imagegen "make the background white" --edit flow.png --out flow-v2.png
```

Needs `OPENAI_API_KEY` (bills via the OpenAI Images API) and `uv` on PATH. yoyo verifies the artifact deterministically: the file must exist, have changed, start with the right magic bytes for its extension (`.png`/`.jpg`/`.jpeg`/`.webp`), and have a plausible size. `--quality low|medium|high|auto` and `--size WxH` pass through. The bundled `yoyo-imagegen` skill teaches when and how to use it.

## Doctor

`yoyo doctor` checks that agent binaries exist. `yoyo doctor --live` fires a real one-line probe through each agent in both read-only and full-access mode, exercising the exact flag paths yoyo depends on — run it after upgrading a CLI to catch flag drift.

```bash
yoyo doctor --live
yoyo doctor --live --agent codex --timeout 60 --json
yoyo doctor --live --strict   # exit 1 on any failed probe or missing agent
```

## Workflows

`yoyo workflow` runs a saved multi-agent workflow from a JSON spec — for when one call isn't enough: fan research across files, ask different agents for independent audits, or run an implement-then-review pipeline. It reads the spec, runs phases in order, runs jobs within a phase in parallel up to `max_concurrency`, and returns a JSON result.

```bash
yoyo workflow --list
yoyo workflow cross-review --input "review the current branch diff" --json
yoyo workflow ./workflow.json --input "audit auth routes" --dry-run --json
```

A bare name resolves against `YOYO_WORKFLOW_PATH`, then `~/.config/yoyo/workflows/`, then the source `workflows/` dir. Bundled templates:

- `cross-review` — Codex and Claude review in parallel, then a judge verifies each finding against the code.
- `adversarial-audit` — three single-lens audits (correctness, security, maintainability), then a verifier that keeps only findings it can't refute.
- `frontend-impl-review` — a Codex worker implements with the `frontend-design` skill, then an independent review checks the result.

Minimal spec:

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

Common job fields: `agent`, `model`, `role`, `files`, `skill`, `for_each` (expand one template into many), `include_previous` / `include_phases` (feed prior outputs into a later job), `retries`, and `expect` (a stdout marker check). Phase-level `gates` run shell commands after a phase's jobs succeed. Jobs default to read-only; set `read_only: false` only for tightly scoped worker jobs.

### Verification hooks (opt-in)

yoyo does not grade agent output by default — the calling agent states what it wants and judges the result. Three opt-in hooks exist for cases where *code*, not a model, is the right verifier:

- **Gates** run real checks (tests, linters, builds) after a phase's jobs succeed; the first failure stops the workflow. Use them for closed-form questions ("do the tests pass?").
- **`retries`** re-runs a failed job up to N times (transient failures).
- **`expect`** checks stdout for a `contains` string or `regex`. Use only for a marker the prompt explicitly demanded (e.g. `VERDICT: PASS`); never to grade free-form prose, which produces false failures.

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

`read_only_args`/`full_access_args` are appended based on `--read-only`; a custom agent with no `read_only_args` makes `--read-only` fail loudly rather than pretend. Use `kind` to keep a built-in's flag behavior with a custom path. Or set `YOYO_AGENT_<NAME>=<command>` in the environment. Custom agents receive the rendered prompt on stdin. `--agent-arg` appends a raw argument — powerful; verify with `--dry-run` when combined with `--read-only`.

## Access model

`yoyo ask` and `yoyo research` default to full access so agent-to-agent calls don't stop for approval:

- Codex: `--sandbox danger-full-access --ask-for-approval never`
- Claude: `--permission-mode bypassPermissions`
- Pi: read, grep, find, ls, bash, edit, write tools

`--read-only` constrains a reviewer — Codex gets `--sandbox read-only`, Claude and Pi get read-oriented tool allowlists. It's enforced by the target agent's own mechanism (a real sandbox for codex, allowlists for claude/pi), not by yoyo; treat it as a strong default, not an airtight sandbox. This is intentionally powerful. Agent output is not truth — verify it with code, tests, docs, or live state before acting. A reviewer or checker never gates control flow on its own.

## Durability

Every call carries a trace ID (in the prompt metadata, JSON result, and stderr). Subprocess output is captured to temp files and capped by `--max-output-bytes`; stdin + `--file` share `--max-input-bytes`. stdin is read only when input is actually available, so an idle stdin can't hang a call (`--stdin-wait` for slow producers, `--no-stdin` to ignore). Timeouts kill the process group; SIGINT/SIGTERM/SIGHUP and normal exit terminate in-flight agents so an interrupted yoyo doesn't orphan children. If a real review times out, treat it as unavailable — never count it as completed.

## Test

```bash
python3 -m unittest discover -s tests
```
