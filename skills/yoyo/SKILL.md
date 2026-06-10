---
name: yoyo
description: Use this skill when an agent should call Codex, Claude, Pi, or another configured CLI agent as a subagent, worker, reviewer, or second-opinion partner using the yoyo command-line tool. Use it for multi-agent coordination, independent review, scoped delegation, adversarial checks, and agent-to-agent handoffs.
---

# Yoyo Multi-Agent Coordination

Use `yoyo` to call another CLI agent as a subprocess or interactive session. Treat the other agent as a worker or reviewer, not as an oracle. Its output is evidence to verify.

## Quick Commands

`yoyo ask` is one-shot by default. It also defaults to full-access/no-approval mode where the target agent supports that, so agent-to-agent work does not stop for permission prompts. Use `--read-only` for constrained review.

`--role` defaults to `opinion`. Set `--role review` or `--role worker` explicitly when you want bug finding or delegated implementation.

Second opinion:

```bash
yoyo ask claude --role opinion --caller codex "Challenge this plan. What can fail?"
```

Code review with explicit context:

```bash
yoyo ask codex --role review --cwd "$PWD" --file src/main.ts --caller claude "Review this change for bugs. Inspect the repo as needed."
```

Scoped worker with write access:

```bash
yoyo ask pi --role worker --cwd "$PWD" --caller codex "Fix the failing test in tests/foo_test.py. Do not touch unrelated files."
```

Steered worker (skill injection):

```bash
yoyo ask codex --role worker --cwd "$PWD" --skill frontend-design --caller claude "Build the settings page. Keep the change minimal."
```

`--skill <name>` (repeatable) injects a named `SKILL.md` into the delegated prompt. Use it whenever the target agent's raw output is too unpredictable for the task: the skill pins down conventions, quality bars, and output shape while your prompt states the task. Discover names with `yoyo skills`. Skills resolve from `YOYO_SKILL_PATH`, then the standard agent skill directories (`~/.claude/skills`, `~/.codex/skills`, ...); a missing skill fails loudly before any tokens are spent.

Background delegation (when your own tool budget is shorter than the task):

```bash
run_id=$(yoyo ask codex --role review --cwd "$PWD" --background "Audit the auth module.")
yoyo wait "$run_id"        # or poll later: yoyo runs show "$run_id" --json
```

Prefer `--background` + `yoyo wait`/`yoyo runs show` over raising `--timeout` and blocking, and over abandoning a long review. The run ledger (`yoyo runs list`) keeps every background result auditable after the fact.

Follow-up session (continue a prior delegation with full context):

```bash
yoyo ask codex --session auth-review --role review --cwd "$PWD" "Review the auth changes."
yoyo ask codex --session auth-review "Is the middleware.py issue you flagged fixed now?"
```

Use `--session <name>` whenever you expect to ask the same agent follow-up questions; it avoids re-sending context and the target agent keeps its prior reasoning. `yoyo sessions list` shows recorded sessions.

Interactive session:

```bash
yoyo chat claude --cwd "$PWD" "Help me debug this repo."
```

Inspect setup:

```bash
yoyo doctor
yoyo doctor --live   # real probes through every agent's read-only and full-access flag paths; run after CLI upgrades
yoyo agents
yoyo skills
```

Update the installed CLI and skills from the recorded source checkout:

```bash
yoyo update
```

Use `yoyo update --no-pull` to reinstall from the current recorded checkout without fetching.

Run a reusable multi-agent workflow (path or bundled template name):

```bash
yoyo workflow --list
yoyo workflow cross-review --input "review the current branch diff" --json
yoyo workflow ./workflow.json --input "audit this change" --json
```

Trace/debug a delegation:

```bash
yoyo ask claude --trace-id "$USER-auth-review" --json "Review this plan."
```

## Timeout Discipline

Agent calls default to a one-hour wall-clock timeout. It exists only to prevent orphaned or truly hung subprocesses; it is not a progress budget for real agent work.

Do not add short ad hoc timeouts to real reviews, audits, or worker delegations. A three-minute cap can turn a valid long-running review into a false failure. Use short `--timeout` values only for deterministic smoke tests with fake or trivial agents.

Use `--timeout` or `YOYO_TIMEOUT` only when the task has an explicit operational reason for a shorter or longer cap. If a real review times out, report it as an unavailable review, not as a passed or failed review.

### Liveness, hangs, and "stuck" calls

- **Progress heartbeat.** Long calls print a periodic `yoyo: still running, Ns elapsed, M bytes captured` line to stderr so a working agent is not mistaken for a hung one. Disable with `--quiet` or `YOYO_HEARTBEAT_SECS=0`; change the interval with `YOYO_HEARTBEAT_SECS=<seconds>`.
- **Idle-timeout hang guard.** `--idle-timeout <seconds>` (or `YOYO_IDLE_TIMEOUT`) kills the agent if it produces no output for that long. This is a better "truly hung" detector than the wall-clock cap, but only enable it for agents that stream output incrementally; an agent that buffers all output until the end would be killed falsely.
- **stdin never blocks the caller.** yoyo reads stdin as context only when data is actually available, so an open-but-idle stdin (common when one agent shells out to another) can no longer hang the call before the agent starts. For a slow producer you genuinely want to pipe, use `--stdin-wait <seconds>`. Use `--no-stdin` to ignore stdin entirely.
- **No orphans.** If yoyo is interrupted or killed (SIGINT/SIGTERM/SIGHUP), it terminates the nested agent's process group instead of leaving it running and burning tokens.
- **Caller tool budgets.** A heartbeat does not extend the timeout of whatever tool invoked yoyo. If your own shell/tool budget is shorter than the review needs, use `yoyo ask --background` and collect the result with `yoyo wait <run_id>` or `yoyo runs show <run_id>`, rather than raising `--timeout` and blocking. If a real review is cut off, report it as unavailable — never as passed.

## Coordination Protocol

1. Define the success criterion before delegating.
2. Pick the narrowest role:
   - `opinion`: challenge reasoning or architecture.
   - `review`: find concrete bugs and missing tests.
   - `worker`: do a bounded implementation task.
3. Pass only the context needed. Prefer `--file` for exact artifacts and a short prompt for the ask.
   For quality-sensitive work (frontend, design, testing conventions), add `--skill <name>` so the worker follows a known playbook instead of improvising.
4. Use default full access for trusted automation or worker tasks. Use `--read-only` for reviews, second opinions, or untrusted prompts.
5. Verify the result yourself. Run tests, inspect diffs, and reconcile disagreements before acting.
6. For long or noisy tasks, set `--trace-id` and consider `--max-output-bytes` so failures are auditable and output cannot grow without bound. Do not set short timeouts for real agent review.
7. For large diffs or generated files, use `--max-input-bytes`; yoyo applies it as an aggregate cap across stdin and `--file` context.
8. For many related agent calls, prefer `yoyo workflow` with a checked spec over ad hoc manual fan-out. Dry-run the workflow before spending tokens.

## Good Delegation Prompts

Ask for falsification:

```bash
yoyo ask claude --role opinion --file plan.md "Find the strongest reason this plan is wrong. Do not rewrite it."
```

Ask for focused implementation:

```bash
yoyo ask codex --role worker --file tests/test_cli.py "Make this test pass with the smallest production change."
```

Ask for review after edits:

```bash
git diff -- src tests | yoyo ask pi --role review --cwd "$PWD" "Review this diff for correctness issues. Use the current worktree as the source of truth."
```

## Guardrails

- Do not delegate deterministic work that a script or test can answer.
- Do not ask multiple agents broad open-ended questions and average the answers.
- Do not let a worker perform irreversible operations, releases, credential changes, or destructive git commands unless the human explicitly asked for that and you can verify every step.
- Use `yoyo chat` only when a human or supervising agent is available to interact. Use `yoyo ask` for autonomous one-shot delegation.
- Use `--read-only` for built-in agents when reviewing untrusted diffs/files. For custom agents, configure `read_only_args`; yoyo fails loudly if read-only cannot be enforced.
- For repo review, pass `--cwd "$PWD"` and ask the subagent to inspect the current worktree. Pipe `git diff` only as supplemental focus, not as the only context.
- Yoyo is intentionally powerful. Do not remove `--agent-arg` or full-access paths just to make misuse impossible; document and verify the actual capability being requested.
- Workflow jobs default to read-only. Do not feed one agent's output into a write-capable workflow job or a job with raw `agent_args` unless the workflow explicitly sets `allow_untrusted_context: true` and the risk is justified.
- Treat the full-access warning on stdin/`--file` as meaningful; switch to `--read-only` unless the input and task are trusted.
- If agents disagree, identify the factual claim that would settle it, then inspect code, docs, tests, or live state.
- If the target agent fails, times out, or lacks credentials, report that directly and continue with the best local verification path.

## Synthesis Pattern

After a subagent returns, summarize:

- What it claimed.
- Which claims were verified.
- Which claims were rejected or remain uncertain.
- What changed in your plan because of it.

Never present subagent output as confirmed-current unless you independently verified it.
