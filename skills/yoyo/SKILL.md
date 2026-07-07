---
name: yoyo
description: Delegates tasks to Codex, Claude, Pi — or, on demand, Cursor, Antigravity (agy), and Grok — via the yoyo CLI. Use for multi-agent coordination, best-of-n comparison, independent review, deep research, fresh-context loops, scheduled/continuous work, and agent-to-agent handoffs.
---

# Yoyo Multi-Agent Coordination

`yoyo` calls other CLI agents as subprocesses. **You are the orchestrator**: yoyo gives you primitives — one call, a parallel fan-out, a loop, a schedule — and you compose them one step at a time, deciding each next step from the last result. Don't look for a canned pipeline; build the smallest next call, read it, and decide.

Why multiple agents at all: different vendors fail differently. Fanning the same task out and comparing (best-of-n with a judge, self-consistency, cross-model review) reliably beats a single sample — agreement across vendors is signal, and disagreement marks exactly what to verify. Treat any agent's output as evidence to check, not an oracle.

## Agents

Core trio (battle-tested, support `--session` follow-ups):

- `codex` — OpenAI. Default reviewer/second opinion; powers `yoyo imagegen`.
- `claude` — Anthropic. Default worker; the only agent that reports per-call cost (so `loop --budget-usd` is enforced only here).
- `pi` — lightweight and cheap; small scoped tasks and quick opinions.

On demand, one-shot only (`--session` rejected): `cursor` (cross-vendor model picker, needs Cursor auth), `agy` (Google Antigravity — **full-access only**, can't be a reviewer/checker or take `--read-only`), `grok` (xAI — a fourth vendor for adversarial tiebreaks). Reach for these when vendor diversity is the point: a third vendor breaks a tie better than a second call to the same model family, and an adversarial review is more credible from a vendor that didn't write the code.

Probe health with `yoyo doctor --live --agent <name>`.

## The primitives

```bash
# One call (role: opinion | review | worker; default opinion, full-access)
yoyo ask codex --role review --read-only --cwd "$PWD" "Find bugs that would block shipping. Cite file/line."

# Best-of-n: same prompt, several vendors in parallel, optional judge
# (prefer a judge that isn't a candidate — models favor their own answers;
# the judge runs read-only, so it can't be agy)
yoyo ask codex,claude --cwd "$PWD" "Design the migration. Name the riskiest step." --judge grok
# --judge-only returns just the verdict; raw answers go to files you can read on demand

# Consensus code review of the current git diff
yoyo review --cwd "$PWD"                      # codex + claude, read-only, synthesized

# Deep research: parallel perspectives, then a decision brief
yoyo research --cwd "$PWD" "Should we move the engine to Rust?"

# Fresh-context loop (state file carries continuity; flat per-iteration cost)
yoyo loop claude --cwd "$PWD" --max-iter 30 --gate "pytest -q" "Fix the failing tests, one per iteration."

# Queue + brief: N checkable increments, shared knowledge injected each iteration
yoyo loop codex,claude --cwd "$PWD" --queue tasks.md --brief .yoyo/brief.md --gate "pytest -q" "Work the queue."

# Schedule anything on cron; a scheduled loop continues from its state file
yoyo cron add nightly --schedule "0 2 * * *" --cwd "$PWD" -- loop claude --max-iter 5 "Work through TODO.md"

# Long call? Detach it and keep working — --background works on ask, loop,
# research, review, workflow, and imagegen alike
run_id=$(yoyo ask claude --role review --cwd "$PWD" --background "...")
run_id=$(yoyo research --background "...")
yoyo wait "$run_id" --timeout 25    # 124 = still running, wait again; 0 = done
```

Everything composes: `--skill <name>` injects a SKILL.md as guidance (the skill says *how*, the prompt says *what*) — a path (`--skill ./rules/ponytail.md`) injects any rules file as an opt-in overlay, e.g. a minimal-code "senior engineer" ladder for workers; `--session <name>` keeps a named conversation across `ask` calls; `--json` gives you a machine-readable envelope; `--raw` passes a leading `/command` through verbatim.

## Orchestrate step by step

The powerful pattern is not one big command — it's you in the loop between small ones:

1. **Fan out** the genuinely uncertain question (`ask a,b,c` or `research`), in the background if long-running.
2. **Read the results yourself.** Where vendors agree, move on. Where they disagree, that disagreement is your work list.
3. **Settle each disagreement** with the narrowest possible check: read the code, run the test, or ask a *different* vendor the specific contested claim — not the whole question again.
4. **Delegate the now-well-defined work** to a worker (`ask --role worker`, or `loop` if it's iterative), then verify with `review` or a gate command.
5. Repeat. Each step's shape comes from the previous step's result, not from a plan fixed up front.

Prefer being the judge yourself when you hold the decision context; use `--judge` when you want an independent one. For research, `--no-synthesis` returns the raw perspectives so you synthesize them with everything else you know.

## Making each call good

- Put the instruction first; state the success criterion, scope, and exclusions. Attach context with `--cwd`/`--file`/stdin and let the worktree be the source of truth.
- Prompt reviewers to falsify, not validate: "find the strongest reason this is wrong" beats "review my plan".
- Replace vague words ("good", "clean") with observable criteria; ask for file/line pointers and the commands run.
- Research lenses are yours to define: `--lenses regulatory,market` for named angles, repeatable `--lens "full free-text instructions"` for exact ones, and duplicate lenses across `--agents` for a deliberate best-of-n sample.
- Shared context beats re-derivation: generate a dense repo brief once (`yoyo ask claude --read-only "Write a brief: layout, conventions, commands, gotchas" > .yoyo/brief.md`) and pass it to fan-outs with `--file` and to loops with `--brief`, so parallel agents and fresh iterations stop re-exploring the same ground.
- For tabular findings, ask for TOON rows in the prompt (`findings[N]{file,line,severity,claim}:` — leaner than JSON); keep prose as markdown. A convention you set per call, never something yoyo enforces.
- Don't delegate what a script or test answers deterministically, and don't average contradictory answers — resolve them.

## Loops and schedules

`yoyo loop` runs a task as independent fresh-context iterations: each one reads the state file (default `.yoyo/loop-state.md`), does one increment, rewrites the state. Cost stays flat instead of compounding with session length. `yoyo loop codex,claude ...` rotates vendors across iterations so one model's blind spots don't compound. Stop conditions: accepted `STATUS: DONE`, a `STOP` file, `--max-iter`, `--budget-usd` (claude only), or `--max-fail`.

For itemizable work, `--queue tasks.md` (a `- [ ] item` checklist) makes each iteration do exactly one item and check it off; DONE is mechanically rejected while any box is unchecked. The worker owns the queue file, so pair it with a gate or checker for independent verification. `--brief FILE` injects a caller-written shared-knowledge brief (repo map, conventions, commands) read-only into every iteration so fresh contexts stop re-deriving it — keep it dense, it rides in every prompt.

For unattended loops, make DONE earn it: `--gate "pytest -q"` (repeatable; closed-form evidence only) and/or `--checker codex` (independent read-only verdict, blind to the worker's prose). `--spec FILE` pins immutable constraints that the lossy state rewrite would otherwise drop. Cost levers for claude workers: `--model sonnet` or `--agent-arg=--effort=low`, plus `--agent-arg=--setting-sources=project`.

`yoyo cron` makes any yoyo command recurring via the user's crontab — no daemon. The natural pairing is a scheduled loop: state file carries progress between runs, the flock prevents overlap, and a verified DONE makes later runs no-ops. `yoyo cron list` / `rm <name>` / `run <name>` manage entries; output lands in the entry's log file. Only schedule work the human asked to keep running, and give it a gate or checker — unattended self-grading drifts.

## Verify, then trust

1. Define the success criterion before delegating; pick the narrowest role.
2. Use `--read-only` for reviews and untrusted input (enforced by the target's own sandbox/allowlists — strong default, not airtight).
3. Spot-check artifacts yourself: diffs, tests, logs, live behavior. If two agents disagree, find the factual claim that settles it and inspect directly.
4. A reviewer or checker reports findings; it never gates control flow on its own. Never present delegated output as confirmed unless you verified it.
5. A timed-out review is *unavailable*, never *passed*. The default four-hour timeout is a hang guard, not a progress budget — don't shorten it for real work, and never kill a still-running background call (poll with `yoyo wait`). Long tasks are welcome: give big work to a worker or workflow job and let it run.
6. Workers must not do irreversible things (releases, credential changes, destructive git) unless the human explicitly asked.

## More

- `yoyo workflow <name|path> --input "..."` — a *saved* multi-phase pipeline (JSON) for orchestrations you've stabilized and want to rerun; orchestrate dynamically first, freeze later. See the `yoyo-workflow` skill.
- `yoyo imagegen "<prompt>" --out file.png` — real raster images via GPT-image; see the `yoyo-imagegen` skill.
- Video understanding (YouTube/Loom/local recordings): see the `yoyo-watch` skill — run the watch pipeline yourself or delegate with `yoyo ask <agent> --skill watch "..."` to keep frames out of your context.
- `yoyo runs list` / `sessions` / `agents` / `skills` / `doctor --live` / `update` — ledger, discovery, health, upgrades.
