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
yoyo ask codex --role review --file src/main.ts --caller claude "Review this change for bugs."
```

Scoped worker with write access:

```bash
yoyo ask pi --role worker --cwd "$PWD" --caller codex "Fix the failing test in tests/foo_test.py. Do not touch unrelated files."
```

Interactive session:

```bash
yoyo chat claude --cwd "$PWD" "Help me debug this repo."
```

Inspect setup:

```bash
yoyo doctor
yoyo agents
```

Update the installed CLI and skills from the recorded source checkout:

```bash
yoyo update
```

Use `yoyo update --no-pull` to reinstall from the current recorded checkout without fetching.

Run a reusable multi-agent workflow:

```bash
yoyo workflow ./workflow.json --input "audit this change" --json
```

Trace/debug a delegation:

```bash
yoyo ask claude --trace-id "$USER-auth-review" --json "Review this plan."
```

## Timeout Discipline

Agent calls default to a one-hour timeout. The timeout exists only to prevent orphaned or truly hung subprocesses; it is not a progress budget for real agent work.

Do not add short ad hoc timeouts to real reviews, audits, or worker delegations. A three-minute cap can turn a valid long-running review into a false failure. Use short `--timeout` values only for deterministic smoke tests with fake or trivial agents.

Use `--timeout` or `YOYO_TIMEOUT` only when the task has an explicit operational reason for a shorter or longer cap. If a real review times out, report it as an unavailable review, not as a passed or failed review.

## Coordination Protocol

1. Define the success criterion before delegating.
2. Pick the narrowest role:
   - `opinion`: challenge reasoning or architecture.
   - `review`: find concrete bugs and missing tests.
   - `worker`: do a bounded implementation task.
3. Pass only the context needed. Prefer `--file` for exact artifacts and a short prompt for the ask.
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
git diff -- src tests | yoyo ask pi --role review "Review this diff for correctness issues."
```

## Guardrails

- Do not delegate deterministic work that a script or test can answer.
- Do not ask multiple agents broad open-ended questions and average the answers.
- Do not let a worker perform irreversible operations, releases, credential changes, or destructive git commands unless the human explicitly asked for that and you can verify every step.
- Use `yoyo chat` only when a human or supervising agent is available to interact. Use `yoyo ask` for autonomous one-shot delegation.
- Use `--read-only` for built-in agents when reviewing untrusted diffs/files. For custom agents, configure `read_only_args`; yoyo fails loudly if read-only cannot be enforced.
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
