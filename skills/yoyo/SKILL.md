---
name: yoyo
description: Delegates work to another vendor's coding agent through the yoyo CLI — Codex, Claude, Pi, Cursor, Grok. Use for an independent second opinion, cross-vendor code review, best-of-n comparison, deep research, or a fresh-context loop over a long task.
---

# Yoyo

`yoyo` runs another vendor's agent CLI as a subprocess. **You are the orchestrator**: yoyo hands you one call at a time and you decide the next step from the last result. There is no canned pipeline to find.

Why bother: different vendors fail differently. Ask two and compare — **agreement is signal, disagreement is your work list.** Treat every answer as evidence to check, never as an oracle.

## Call it in the background

A delegate is a full agentic session. Real calls here take **4 minutes at the median and 13 at the 90th percentile**. If your own exec tool yields in tens of seconds, a foreground call will be killed and look like a hang.

So make this the default shape of every call:

```bash
run_id=$(yoyo ask codex --role review --read-only --cwd "$PWD" --background "Find bugs that block shipping. Cite file/line.")
yoyo wait "$run_id" --timeout 25    # 124 = still running, call wait again
                                    # 0   = done, output printed
                                    # else = real failure
```

`--background` detaches into its own session (ppid 1), so it survives your turn ending, your tool timing out, and the PTY teardown that kills processes spawned inside it. Repeat the short `wait` as many times as it takes. Raising your own tool timeout instead is the move that fails.

`--background` works on `ask`, `loop`, `research`, and `review`.

A call your harness cut off is **unavailable**, never *passed*. When you are unsure what happened to one, `yoyo runs autopsy` reconstructs the most recent run from recorded evidence — finished, failed, signal-killed, or still running.

## Agents

| Agent | Vendor | Reach for it when |
| --- | --- | --- |
| `codex` | OpenAI | Default reviewer and second opinion; also the strongest at browser and computer use |
| `claude` | Anthropic | Default worker for scoped edits |
| `pi` | Pi | Small, cheap, fast questions |
| `cursor` | Cursor | A worker on a different model family (needs `cursor-agent login`) |
| `grok` | xAI | A fourth vendor to break a tie |
| `agy` | Google | Full-access only — it cannot take `--read-only`, so never as a reviewer |

Each agent runs whatever model its own CLI is configured for. Check availability with `yoyo agents`, health with `yoyo doctor --live`.

**Pick a different vendor than the one that wrote the code.** A model is a soft judge of its own output, and Cursor running Grok is not independent of native Grok — for best-of-n they are one sample, not two.

`--model` forwards to any agent, but the model IDs drift by account and CLI version, so ask the target CLI (`cursor-agent --list-models`) rather than trusting a remembered name. Four things a lookup will not tell you:

- **Default-first quality rule.** For complex, ambiguous, multi-file, architectural, security, or release work, omit `--model` and take the target CLI's configured default. Reach for a smaller tier only on bounded tasks that have an objective check. Never trade correctness or completeness for lower cost or latency.
- A `-fast` variant means **lower latency, not lower cost**.
- Cursor's `--read-only` is its plan mode, not an OS sandbox. Pair any Cursor write with a focused test and read the diff yourself.
- One `ask` fan-out shares a single `--model` across its candidates. Use separate calls when the agents need different models.

## The primitives

```bash
# One call. --role: opinion (default) | review | worker. --read-only for anything you didn't ask to edit.
yoyo ask codex --role review --read-only --cwd "$PWD" --background "..."

# Best-of-n: same prompt, several vendors in parallel, optional independent judge
yoyo ask codex,claude --cwd "$PWD" --background "Design the migration. Name the riskiest step." --judge grok

# Consensus review of the current git diff
yoyo review --cwd "$PWD" --background            # codex + claude, read-only, synthesized
yoyo review --stance unanimous                   # precision: only findings every reviewer raised
yoyo review --stance any                         # recall: every distinct finding, tagged with reviewer count

# Deep research: parallel perspectives → decision brief. Lenses are yours to define.
yoyo research --cwd "$PWD" --background "Should we move the engine to Rust?"

# Fresh-context loop: each iteration is a new session reading a small state file,
# so cost stays flat instead of compounding with session length.
yoyo loop claude --cwd "$PWD" --max-iter 20 --background "Work through TODO.md, one item per iteration."
```

Composable on any call: `--skill <name>` injects a SKILL.md as guidance (a path works too, for a house-rules overlay); `--file` attaches context; `--session <name>` keeps a named conversation across `ask` calls; `--json` gives a machine-readable envelope; `--raw` passes a leading `/command` through untouched.

Every non-raw delegation carries the bundled `yoyo-fable-mode` harness — evidence discipline for one-shot work. `YOYO_DEFAULT_SKILLS` replaces it; the empty string turns it off.

## Orchestrate step by step

The power is in you sitting between small calls, not in one big command.

1. **Fan out** the genuinely uncertain question — `ask a,b` or `research`.
2. **Read the results yourself.** Agreement means move on. Disagreement is the work list.
3. **Settle each disagreement with the narrowest check available**: read the code, run the test, or ask a *different* vendor about the one contested claim — not the whole question again.
4. **Delegate the now-well-defined work** to a worker, then verify it yourself.
5. Repeat. Each step's shape comes from the last result.

Be the judge yourself when you hold the decision context; use `--judge` when you want an independent one. `--no-synthesis` on research returns raw perspectives for you to synthesize.

**You are the message bus.** Agents do not talk to each other directly, and that is the point — you filter, verify, and decide what crosses. Wire them together and you have an unsupervised loop, which is exactly where delegation drifts.

## Write the prompt so the answer is checkable

- **Instruction first**, then the success criterion, the scope, and the exclusions. Attach the worktree with `--cwd` and let it be the source of truth.
- **Ask reviewers to falsify**: "find the strongest reason this is wrong" beats "review my plan".
- **Demand artifacts.** "Return the failing input, the patch, or the counterexample" beats a status report. Ask for the exact commands run and their output, and for file/line pointers — reviewing captured evidence beats re-verifying prose.
- **Name the return contract**: "return only when X holds and survives your own adversarial check; otherwise return the strongest verified partial result and its exact remaining gap."
- **Give a persistence budget.** Codex especially calibrates effort to the prompt: "keep working until the tests pass; do not stop because the first approach failed" produces materially deeper runs. Pair a generous budget with `--background` and `--idle-timeout`, rather than shortening the ask.
- **Enumerate the failure modes you want checked.** "Check for A, B, C" bites where "check your work" does not.
- **Replace adjectives with observables.** Point at an exemplar in the repo ("follow the pattern in `HotDogWidget.php`") instead of describing "clean".
- **Give a worker a check it can run** and ask it to iterate until the check passes. Otherwise "looks done" is its only stop signal.
- **Split big asks**: get the plan and file list first, read it, then send "implement your plan" with `--session`.

Share context instead of paying for it repeatedly: write a dense repo brief once (`yoyo ask claude --read-only "Write a brief: layout, conventions, commands, gotchas" > .yoyo/brief.md`) and pass it with `--file` to fan-outs, `--brief` to loops.

## Loops

`yoyo loop` runs a task as independent fresh-context iterations. Each one reads the state file (default `.yoyo/loop-state.md`), does one increment, and rewrites it. A comma-separated agent list rotates vendors across iterations.

It stops on `STATUS: DONE` in the state file, a `STOP` file, `--max-iter`, or `--max-fail` consecutive crashed iterations (which exits 1 — a human should look).

Three files shape a good loop, and each earns its place:

- `--queue tasks.md` — a `- [ ] item` checklist. Each iteration completes one item and checks it off, and DONE is rejected while any box is open. This is what makes a long task tractable.
- `--brief FILE` — shared repo knowledge injected read-only every iteration, so fresh contexts stop re-deriving it.
- `--spec FILE` — immutable constraints re-read every iteration, so the lossy state rewrite cannot drift the goal.

DONE is what the worker says it is. Read the diff before you believe it.

## Verify, then trust

1. Define the success criterion before delegating, and pick the narrowest role.
2. `--read-only` for reviews and untrusted input. It is enforced by the target's own sandbox — a strong default, not airtight.
3. Spot-check the artifacts yourself: diffs, tests, logs, live behavior. When two agents disagree, find the factual claim that settles it and look at it directly.
4. A reviewer reports findings; it never decides control flow on its own. Present delegated output as confirmed only after you confirmed it.
5. Workers do irreversible things — releases, credential changes, destructive git — only when the human asked for them.

## More

- `yoyo imagegen "<prompt>" --out file.png` — real raster images; see the `yoyo-imagegen` skill.
- Video (YouTube, Loom, screen recordings) — see the `yoyo-watch` skill.
- `yoyo research` in depth — see the `yoyo-research` skill.
- `yoyo runs list` / `autopsy` / `prune`, `yoyo sessions`, `yoyo agents`, `yoyo skills`, `yoyo doctor --live`, `yoyo update`.
