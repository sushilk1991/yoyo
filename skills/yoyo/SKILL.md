---
name: yoyo
description: Delegates tasks to Codex, Claude, Pi — or, on demand, Cursor, Antigravity (agy), and Grok — via the yoyo CLI. Use for multi-agent coordination, best-of-n comparison, independent review, deep research, fresh-context loops, scheduled/continuous work, and agent-to-agent handoffs.
---

# Yoyo Multi-Agent Coordination

`yoyo` calls other CLI agents as subprocesses. **You are the orchestrator**: yoyo gives you primitives — one call, a parallel fan-out, a loop, a schedule — and you compose them one step at a time, deciding each next step from the last result. Don't look for a canned pipeline; build the smallest next call, read it, and decide.

Why multiple agents at all: different vendors fail differently. Fanning the same task out and comparing (best-of-n with a judge, self-consistency, cross-model review) reliably beats a single sample — agreement across vendors is signal, and disagreement marks exactly what to verify. Treat any agent's output as evidence to check, not an oracle.

## Agents

Core trio (battle-tested, support `--session` follow-ups):

- `codex` — OpenAI. Default reviewer/second opinion; powers `yoyo imagegen`. Notably strong at computer use and browser automation — prefer it for tasks that need driving a browser, verifying live UI flows, or operating desktop tooling.
- `claude` — Anthropic. Default worker; the only agent that reports per-call cost (so `loop --budget-usd` is enforced only here).
- `pi` — lightweight and cheap; small scoped tasks and quick opinions.

On demand, one-shot only (`--session` rejected): `cursor` (cross-vendor model picker, needs Cursor auth), `agy` (Google Antigravity — **full-access only**, can't be a reviewer/checker or take `--read-only`), `grok` (xAI — a fourth vendor for adversarial tiebreaks). Reach for these when vendor diversity is the point: a third vendor breaks a tie better than a second call to the same model family, and an adversarial review is more credible from a vendor that didn't write the code.

Each agent runs whatever model its own CLI is configured to use (codex reads `~/.codex/config.toml`, claude reads `~/.claude/settings.json`); yoyo adds no model flag unless you pass `--model`, which forwards to any agent.

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
yoyo review --stance unanimous                # precision: only findings ALL reviewers raised
yoyo review --stance any                      # recall: every distinct finding, tagged with reviewer count

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

Everything composes: `--skill <name>` injects a SKILL.md as guidance (the skill says *how*, the prompt says *what*) — a path (`--skill ./rules/ponytail.md`) injects any rules file as an opt-in overlay, e.g. a minimal-code "senior engineer" ladder for workers; `--session <name>` keeps a named conversation across `ask` calls; `--json` gives you a machine-readable envelope; `--raw` passes a leading `/command` through verbatim. The inbuilt `fable-mode` skill — a lean reasoning-discipline harness (contract, evidence, done gate) — is injected into every non-raw delegation by default; `YOYO_DEFAULT_SKILLS` swaps the default set, and the empty string disables it.

## Orchestrate step by step

The powerful pattern is not one big command — it's you in the loop between small ones:

1. **Fan out** the genuinely uncertain question (`ask a,b,c` or `research`), in the background if long-running.
2. **Read the results yourself.** Where vendors agree, move on. Where they disagree, that disagreement is your work list.
3. **Settle each disagreement** with the narrowest possible check: read the code, run the test, or ask a *different* vendor the specific contested claim — not the whole question again.
4. **Delegate the now-well-defined work** to a worker (`ask --role worker`, or `loop` if it's iterative), then verify with `review` or a gate command.
5. Repeat. Each step's shape comes from the previous step's result, not from a plan fixed up front.

Prefer being the judge yourself when you hold the decision context; use `--judge` when you want an independent one. For research, `--no-synthesis` returns the raw perspectives so you synthesize them with everything else you know.

### Hard problems: multi-round search discipline

When the task is a genuine search — a bug nobody can find, a design with no obvious route, "make X work at all" — one wave of fan-out is not enough. Run rounds, and as the root orchestrator:

- **Track approach families, not agents.** Classify each delegate's attempt by the underlying idea, not its wording. When several converge on one family, redirect the next calls toward underexplored formulations instead of sampling the same basin again.
- **Don't let the elegant route dominate.** An approach that reduces the problem to a sub-problem of equal difficulty has made no progress; say so in the state you carry between rounds.
- **Mark blocked routes and keep them blocked.** Once a route stalls on a hard missing piece, assign no more agents to it unless someone proposes a materially new mechanism — not a rewording of the old one.
- **Keep incompatible routes alive across rounds.** Cross-pollinate only after independent development has exposed each route's real strengths and gaps; merging too early collapses the diversity that made the fan-out worth paying for.
- **Don't stop after the first wave fails.** Synthesize, challenge, redirect, launch the next round. Failed waves narrow the space — that's the product.

### Prompting a codex (or any) delegate hard

The per-call prompt patterns that make the above work:

- **Demand concrete artifacts, ban status reports.** "Return the failing input, the patch, or the counterexample — not progress notes." Reject "this part is routine", vague optimism, and unproved claims stated as done.
- **State the return contract explicitly.** "Return only when X is achieved and survives your own adversarial check; otherwise return the strongest verified partial result and its exact remaining gap" — never a best-effort summary. Without this, delegates return early with plausible prose.
- **Give an explicit persistence budget.** Codex especially calibrates effort to the prompt: "keep working until the gate passes; do not return because the first approaches failed" produces materially longer, deeper runs than an unadorned ask. Pair generous budgets with `--background` and an `--idle-timeout` guard rather than shortening the ask.
- **Enumerate the known failure modes as an adversarial checklist.** Generic "check your work" is weak; "check for A, B, C" (the specific ways this class of answer goes wrong — off-by-one at boundaries, warm-vs-cold path, both themes) is what makes an adversarial pass bite.
- **Scope external search.** Allow lookup of background/standard material, forbid searching for the answer itself when you want independent reasoning you can compare across vendors.

### Prompting a claude delegate

`claude -p` runs a full agentic session per call — it explores, edits, runs commands, and stops when the work *looks* done. Prompt it like a work order, not a chat message:

- **Scope precisely.** Name the files/dirs, the scenario, and the exclusions: "write a test for foo.py covering the logged-out edge case; avoid mocks" beats "add tests for foo.py". Point to an exemplar in the repo ("follow the pattern in HotDogWidget.php") instead of describing conventions.
- **Give it a check it can run, and ask it to iterate until the check passes.** "Looks done" is claude's only stop signal unless you hand it one: a test command, a build, a script that diffs output against a fixture. This is what `--gate` mechanizes in loops; inline it in the prompt for one-shot asks.
- **For bugs: symptom, likely location, and what "fixed" looks like** — and ask for a failing test that reproduces the issue before the fix.
- **Demand evidence, not assertions**: the exact commands run and their output, file/line pointers, the test results. Reviewing evidence beats re-verifying, and it's how you catch a delegate that stopped at "plausible".
- **Big asks: split explore/plan from implement.** Ask for the plan and file list first, read it, then send "implement your plan" as the follow-up (`--session` keeps the context). One-sentence-diff tasks skip the plan.
- **Cost/latency**: every `claude -p` call re-reads the user's full harness context (~40k tokens here). For mechanical work, `--agent-arg=--setting-sources=project` and/or `--model sonnet` cut most of it.

### Callers with short tool budgets

If you are an agent calling yoyo through an exec tool with its own timeout (codex's shell tool yields in tens of seconds; a claude delegate legitimately runs many minutes), a foreground `yoyo ask` will outlive your budget and look hung. Two facts: `claude -p` **buffers all stdout until completion** — zero bytes mid-run is normal, not a hang — and yoyo's 20s heartbeat goes to stderr, which your harness may not surface. Never conclude "timed out, no output" from a killed foreground call. Instead:

```bash
run_id=$(yoyo ask claude --cwd "$PWD" --background "...")
yoyo wait "$run_id" --timeout 25   # exit 124 = still running: call wait again
                                   # exit 0 = done, output printed; other = real failure
```

Repeat the short `wait` as many times as needed; don't raise your own tool timeout, don't kill the run, and report a cut-off review as *unavailable*, never as passed.

**Agent-to-agent communication is you.** Agents don't need a direct channel to each other — the orchestrator is the message bus, and that's a feature: you filter, verify, and decide what crosses. The plumbing: `--session <name>` holds a durable bilateral conversation with one agent; a state/brief file is the shared memory two agents both read (`--file` it into the next call); piping one call's output into the next (`yoyo ask a ... | yoyo ask b --read-only "critique this"`) is a handoff with you able to inspect the seam. Wire agents directly to each other and you've built an unsupervised loop — exactly where delegation drifts.

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

`--critic codex` adds a cross-vendor write → review → revise cycle: after each iteration, an independent read-only critic (prefer a different vendor than the worker) reviews that iteration's diff and appends concrete findings to the state file, which the next fresh iteration is instructed to address before new work. Advisory only — findings never gate DONE; keep `--gate`/`--checker` for verified completion. This is the default reach when the goal is maximum code quality rather than just completion.

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
