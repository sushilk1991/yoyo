---
name: yoyo
description: Use this skill when an agent should call Codex, Claude, Pi, or another configured CLI agent as a subagent, worker, reviewer, or second-opinion partner using the yoyo command-line tool. Use it for multi-agent coordination, independent review, scoped delegation, adversarial checks, and agent-to-agent handoffs.
---

# Yoyo Multi-Agent Coordination

Use `yoyo` to call another CLI agent as a bounded subprocess. Treat the other agent as a worker or reviewer, not as an oracle. Its output is evidence to verify.

## Quick Commands

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
yoyo ask pi --role worker --write --cwd "$PWD" --caller codex "Fix the failing test in tests/foo_test.py. Do not touch unrelated files."
```

Inspect setup:

```bash
yoyo doctor
yoyo agents
```

## Coordination Protocol

1. Define the success criterion before delegating.
2. Pick the narrowest role:
   - `opinion`: challenge reasoning or architecture.
   - `review`: find concrete bugs and missing tests.
   - `worker`: do a bounded implementation task.
3. Pass only the context needed. Prefer `--file` for exact artifacts and a short prompt for the ask.
4. Keep default read-only mode unless the target agent must edit files. Use `--write` only for scoped worker tasks.
5. Verify the result yourself. Run tests, inspect diffs, and reconcile disagreements before acting.

## Good Delegation Prompts

Ask for falsification:

```bash
yoyo ask claude --role opinion --file plan.md "Find the strongest reason this plan is wrong. Do not rewrite it."
```

Ask for focused implementation:

```bash
yoyo ask codex --role worker --write --file tests/test_cli.py "Make this test pass with the smallest production change."
```

Ask for review after edits:

```bash
git diff -- src tests | yoyo ask pi --role review "Review this diff for correctness issues."
```

## Guardrails

- Do not delegate deterministic work that a script or test can answer.
- Do not ask multiple agents broad open-ended questions and average the answers.
- Do not let a worker perform irreversible operations, releases, credential changes, or destructive git commands unless the human explicitly asked for that and you can verify every step.
- If agents disagree, identify the factual claim that would settle it, then inspect code, docs, tests, or live state.
- If the target agent fails, times out, or lacks credentials, report that directly and continue with the best local verification path.

## Synthesis Pattern

After a subagent returns, summarize:

- What it claimed.
- Which claims were verified.
- Which claims were rejected or remain uncertain.
- What changed in your plan because of it.

Never present subagent output as confirmed-current unless you independently verified it.
