---
name: yoyo
description: Delegates work to another vendor's coding agent through the yoyo CLI — Codex, Claude, Pi, Cursor, Antigravity (agy), Grok. Use for an independent second opinion, cross-vendor code review, best-of-n comparison, deep research, or a fresh-context loop over a long task.
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

### The two timeouts are not the same flag

`yoyo wait --timeout 25` bounds **your poll**. It is safe: 124 means still running, and you call it again. `yoyo ask --timeout N` bounds **the agent's entire task**, and at N seconds kills it mid-thought.

So do not pass `--timeout` or `--idle-timeout` to `ask`, `review`, or `research`. Their defaults sit deliberately far above any real call. In this machine's run ledger 145 of the 186 killed calls died on a hard `--timeout` a caller had typed — 300s, 180s, 600s, 900s, always a round number — and not one died on the default. Against a p90 call length of 13 minutes, a 5-minute budget is not a safety net, it is the failure.

`--background` does not protect you from this: the detached child inherits the `--timeout` you passed and dies on schedule, having captured nothing.

If an outer contract genuinely forces a bound, put it above the p99 — 1800s or more — and never below it because the call "should be quick".

A call your harness cut off is **unavailable**, never *passed*. When you are unsure what happened to one, `yoyo runs autopsy` reconstructs the most recent run from recorded evidence — finished, failed, signal-killed, or still running.

## Agents

| Agent | Vendor | Reach for it when |
| --- | --- | --- |
| `codex` | OpenAI | Default reviewer and second opinion; also the strongest at browser and computer use |
| `claude` | Anthropic | Default worker for scoped edits |
| `pi` | Pi | Small, cheap, fast questions |
| `cursor` | Cursor | A worker on a different model family (needs `cursor-agent login`) |
| `grok` | xAI | A fourth vendor to break a tie; `--read-only` gives it read tools only, no shell, web, or MCP |
| `agy` | Google | Gemini-family tiebreaker; `--read-only` is its plan mode with writes and unlisted shell commands auto-denied |

Each agent runs whatever model its own CLI is configured for. Check availability with `yoyo agents`, health with `yoyo doctor --live`.

**Pick a different vendor than the one that wrote the code.** A model is a soft judge of its own output, and Cursor running Grok is not independent of native Grok — for best-of-n they are one sample, not two.

`--model` forwards to any agent. Discover current choices with `yoyo models codex --json`, `yoyo models claude --json`, or `cursor-agent --list-models`. Use the same working directory as the task. These constraints still apply:

- **Default-first quality rule.** For complex, ambiguous, multi-file, architectural, security, or release work, omit `--model` and take the target CLI's configured default. Reach for a smaller tier only on bounded tasks that have an objective check. Never trade correctness or completeness for lower cost or latency.
- A `-fast` variant means **lower latency, not lower cost**.
- Cursor's and agy's `--read-only` are their plan mode, not an OS sandbox; grok's is a tool allowlist with no shell, web, or MCP. Under agy read-only any shell command it has not allowlisted itself is denied, and a denied call ends the run with no answer, which yoyo reports as exit 1 — keep read-only agy asks to reads (a review of a diff in the prompt is fine), give it full access when the job needs a shell, and treat a one-line agy answer as no answer. Pair any Cursor write with a focused test and read the diff yourself.
- agy's own 5-minute print timeout is overridden by yoyo automatically; there is nothing to work around. Its prompt travels in argv, so a call with megabytes of `--file` context fails loudly on agy — send big context to codex, claude, or pi.
- One `ask` fan-out shares a single `--model` across its candidates. Use separate calls when the agents need different models.

## Choose a subscribed model

You already know the task and its context. Make the selection yourself; do not launch another model just to route it.

1. Honor the user's agent/model choice. Otherwise prefer their existing subscription. Read `yoyo models <agent> --json` once per provider in this task, then reuse the result. Refresh after a login/configuration change or a model-access error. Codex reports `account.type: chatgpt`; Claude reports `claude.ai` with `provider: firstParty` for its native subscription login. An API-key login is a different billing path. Discovery does not check remaining quota or extra-usage billing.
2. Match the work to a model in that returned list. Bounded extraction, a small documented edit, or a test with a known expected result can use an inexpensive tier such as Codex Luna. Ordinary implementation with clear scope and focused tests can use Terra or Claude Sonnet. These are examples, not an allowlist: use the native descriptions and returned IDs, never a remembered ID absent from the catalog.
3. For uncertain requirements, security, releases, architectural changes, or broad debugging, retain the CLI's configured default unless the user chose another model. A short prompt is not evidence of a simple task. If discovery fails, keep the configured default and report that model availability was not checked.
4. Execute with the existing `--model` flag and verify the requested outcome. A process exit of zero is not an acceptance test. On a repeated verified failure, carry the failure evidence and existing state to a stronger available model; do not restart exploration or repeat the same prompt. Auth, quota, and missing-tool failures require fixing access, not a stronger model.
5. Keep one worker active for sequential work. Do not silently switch provider, start parallel candidates, add an API bill, or load a local model to save time. Cross-vendor review remains useful when independent judgment is the task.

Model discovery exchanges CLI metadata only, closes its child processes, and never sends a user turn. The CLI has no automatic model router: the calling agent makes the contextual choice and the command records the selected model.

## Optional local advice

For users who installed the optional local backend, use `yoyo advise --file evidence.json` when comparing suspected repeated unsuccessful approaches, classifying unfamiliar failure evidence, or checking overlapping review findings. Batch focused checks into one call; do not run it for every prompt or obvious errors. The model loads only for that batch. If unavailable, busy, or timed out, continue the existing workflow.

The input is `{"checks":[{"id":"unique-id","kind":"repeat|failure|duplicate","state":...}]}`. A repeat state has `previous` and `latest` strings containing approaches and observed results. A failure state is the error text. A duplicate state has `first` and `second` strings containing original findings with file/line and cause. Compare plausible pairs, retain both finding IDs and original review text, and treat `same` only as a suggested grouping. Limit input to 32 checks and 64 KiB; the helper abstains above 1,024 tokens per check.

Use only the filtered `choice` as a suggestion, never the raw `candidate` as a decision. Scores are not verified certainty. Inspect the evidence before changing an approach, repairing access, or grouping findings. Repetition advice cannot certify progress. No advice can authorize completion, stop a loop, erase evidence, change retry budgets, or override an explicit model choice. Full setup and examples are in `extras/README.md` in the recorded Yoyo checkout.

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
- **Give a persistence budget.** Codex especially calibrates effort to the prompt: "keep working until the tests pass; do not stop because the first approach failed" produces materially deeper runs. Pair a generous budget with `--background`, rather than shortening the ask. The budget belongs in the prose; a `--timeout` is a kill, not a budget.
- **Enumerate the failure modes you want checked.** "Check for A, B, C" bites where "check your work" does not.
- **Replace adjectives with observables.** Point at an exemplar in the repo ("follow the pattern in `HotDogWidget.php`") instead of describing "clean".
- **Give a worker a check it can run** and ask it to iterate until the check passes. Otherwise "looks done" is its only stop signal.
- **Split big asks**: get the plan and file list first, read it, then send "implement your plan" with `--session`.

Share context instead of paying for it repeatedly: write a dense repo brief once (`yoyo ask claude --read-only "Write a brief: layout, conventions, commands, gotchas" > .yoyo/brief.md`) and pass it with `--file` to fan-outs, `--brief` to loops.

## Loops

`yoyo loop` runs a task as independent fresh-context iterations. Each one reads the state file (default `.yoyo/loop-state.md`), does one increment, and rewrites it. A comma-separated agent list rotates vendors across iterations.

It stops on `STATUS: DONE` in the state file, a `STOP` file, `--max-iter`, or `--max-fail` consecutive crashed iterations (which exits 1 — a human should look).

A loop is the one place a runtime bound earns its keep: `--timeout` bounds a single iteration, and without one a wedged iteration holds the whole run for the four-hour default while `--max-fail` waits on a return that never comes. Set it well above a normal iteration — 1800s or more — never at one.

Three files shape a good loop, and each earns its place:

- `--queue tasks.md` — a `- [ ] item` checklist. Each iteration completes one item and checks it off, and DONE is rejected while any box is open. This is what makes a long task tractable.
- `--brief FILE` — shared repo knowledge injected read-only every iteration, so fresh contexts stop re-deriving it.
- `--spec FILE` — immutable constraints re-read every iteration, so the lossy state rewrite cannot drift the goal.

DONE is what the worker says it is. Read the diff before you believe it.

For work with an executable acceptance criterion, add `--verify 'COMMAND'`. Yoyo runs it in `--cwd` after DONE and after the queue passes. A failed check removes DONE and writes bounded failure evidence into the state file for the next iteration. `--max-fail` also bounds failed completion checks; with `--verify`, only verified completion exits zero. The check uses the caller's permissions and `--timeout`, so choose a focused test that is safe to repeat.

Use `--max-stall 3` to stop after three iterations leave both state and queue unchanged. It is off by default and reads only those files. This detects missing continuity updates, not semantic progress: rewording the state can evade it, and research without a state update can trigger it. On `state-unchanged` or repeated failed checks, inspect the state and captured evidence before escalating or changing the approach.

## Verify, then trust

1. Define the success criterion before delegating, and pick the narrowest role.
2. `--read-only` for reviews and untrusted input. It is enforced by the target's own sandbox — a strong default, not airtight.
3. Spot-check the artifacts yourself: diffs, tests, logs, live behavior. When two agents disagree, find the factual claim that settles it and look at it directly.
4. A reviewer reports findings; it never decides control flow on its own. Present delegated output as confirmed only after you confirmed it.
5. Workers do irreversible things — releases, credential changes, destructive git — only when the human asked for them.

## More

- `yoyo imagegen "<prompt>" --out file.png` — real raster images; see the `yoyo-imagegen` skill.
- Images you cannot see (screenshots, error dialogs) — see the `yoyo-vision` skill.
- Video (YouTube, Loom, screen recordings) — see the `yoyo-watch` skill.
- `yoyo research` in depth — see the `yoyo-research` skill.
- `yoyo runs list` / `autopsy` / `prune`, `yoyo sessions`, `yoyo agents`, `yoyo skills`, `yoyo doctor --live`, `yoyo update`.
