---
name: yoyo
description: Delegates tasks to Codex, Claude, Pi — or, on demand, Cursor, Gemini, and Grok — as subagents, reviewers, workers, or second-opinion partners via the yoyo CLI. Use for multi-agent coordination, independent code review, scoped delegation, adversarial checks, fresh-context loops, and agent-to-agent handoffs.
---

# Yoyo Multi-Agent Coordination

`yoyo` calls another CLI agent as a subprocess or interactive session. Treat the target agent's output as evidence to verify, not as an oracle.

## Choosing an agent

Default to the core trio — they support `--session` follow-ups and are the battle-tested paths:

- `codex` — OpenAI Codex. Default reviewer and second opinion; also powers `yoyo imagegen`.
- `claude` — Anthropic. Default worker and the only agent that reports per-call cost, so `yoyo loop --budget-usd` is enforced only here.
- `pi` — lightweight and cheap; good for small scoped worker tasks and quick opinions.

`cursor`, `gemini`, and `grok` are **on-demand only**: one-shot (`--session` is rejected), reach for them when their specific edge fits — not as part of the regular rotation, and never to fan the same question out to every agent.

- `cursor` (Cursor agent CLI) — pro: cross-vendor model picker in one CLI (`--model gpt-5`, `sonnet-4`, ...; `cursor-agent --list-models`), editor-grade code edits. Con: needs `cursor-agent login` or `CURSOR_API_KEY`, bills through your Cursor plan. Use when you want a specific model yoyo's other agents don't expose, or an independent implementation pass.
- `gemini` (Google Gemini CLI) — pro: very large context window, so it absorbs whole-repo dumps and long logs that overflow other agents; distinct vendor for tiebreaks. Con: weaker fit for surgical multi-file edits; output can be verbose. Use for huge-input review/summarization or a third-vendor opinion.
- `grok` (xAI Grok CLI) — pro: a fourth independent vendor for adversarial cross-checks; Claude-Code-style permission modes map cleanly onto `--read-only`. Con: newer, less battle-tested harness; noisy stderr; `yoyo chat grok` takes no initial prompt. Use when you want one more genuinely independent opinion on a contested call.

The decision rule: vendor diversity is the reason these exist. When two agents disagree, a third vendor breaks the tie better than a second call to the same model family — and an adversarial review is more credible from a different vendor than the one that wrote the code.

## Core commands

`yoyo ask` is one-shot and defaults to full-access/no-approval mode where the target agent supports that, so agent-to-agent work doesn't stop at permission prompts; use `--read-only` for reviews or untrusted input. `--role` defaults to `opinion`; set `review` or `worker` explicitly.

```bash
# Second opinion
yoyo ask claude --role opinion --caller codex "Challenge this plan. What can fail?"

# Code review (read-only; the repo, not the prompt, is the source of truth)
yoyo ask codex --role review --read-only --cwd "$PWD" --file src/main.ts --caller claude "Review this change for bugs. Inspect the repo as needed."

# Scoped worker with write access
yoyo ask pi --role worker --cwd "$PWD" --caller codex "Fix the failing test in tests/foo_test.py. Do not touch unrelated files."

# Steered worker: inject a SKILL.md into the prompt when raw output would be
# too unpredictable (discover names with: yoyo skills; missing names fail loudly)
yoyo ask codex --role worker --cwd "$PWD" --skill frontend-design --caller claude "Build the settings page. Keep the change minimal."

# Follow-up session — target keeps its prior context across calls
yoyo ask codex --session auth-review --role review --cwd "$PWD" "Review the auth changes."
yoyo ask codex --session auth-review "Is the middleware.py issue you flagged fixed now?"

# Interactive (only when a human or supervising agent is present to respond)
yoyo chat claude --cwd "$PWD" "Help me debug this repo."
```

## Background runs and timeouts

A real review or worker call takes minutes. If your own tool budget yields sooner (codex's exec tool yields after ~10–30s), detach instead of running it foreground — and never kill a still-running call (that wastes every token it already spent) or report it as "timed out":

```bash
run_id=$(yoyo ask claude --role review --cwd "$PWD" --background "...")   # returns in <1s
yoyo wait "$run_id" --timeout 25
```

`yoyo wait` exit codes: 124 = still running, wait again; 0 = success, result on stdout; anything else = the run failed or died — inspect `yoyo runs show <run_id> --json`, do not keep waiting. A `claude -p` target emits nothing until it finishes, so a heartbeat reading "0 bytes captured" is normal, not a hang. Every background run lands in the ledger (`yoyo runs list`).

The default `--timeout` is one hour of wall clock — an orphan guard, not a progress budget. A short timeout turns valid long-running work into a false failure; if a review is cut off, report it as unavailable, never as passed. `--idle-timeout` kills on output silence and suits only agents that stream incrementally (a buffering target like `claude -p` would be killed falsely).

## Loops (iterative work at flat cost)

```bash
yoyo loop claude --cwd "$PWD" --max-iter 30 --budget-usd 10 --caller codex "Fix the failing tests, one per iteration."
```

Use `yoyo loop` instead of grinding many rounds inside one ever-growing session: a long session re-reads its whole context on every call and cost compounds; a loop runs each iteration as a brand-new fresh-context session. Continuity lives in a state file (default `.yoyo/loop-state.md`) the worker reads first and rewrites before ending. The loop stops on `STATUS: DONE` in the state file, a `STOP` file beside it, `--max-iter`, `--budget-usd` (enforced for claude, which reports per-iteration cost), or `--max-fail` consecutive failures. `--background` detaches the whole loop; poll with `yoyo wait` as above.

Worker cost levers: a fresh `claude -p` session re-reads tens of thousands of tokens of harness context on every API call. For mechanical iteration work pass `--model sonnet` (Sonnet 4.6) or keep Opus and lower thinking with `--agent-arg=--effort=low`; add `--agent-arg=--setting-sources=project` to drop user-level config from workers already steered by `--role`/`--skill` (one measured setup: $0.94 → $0.15 per iteration — expect the ratio, not the numbers, to transfer).

## Delegation protocol

1. Define the success criterion before delegating, and pick the narrowest role: `opinion` to challenge reasoning, `review` to find bugs, `worker` for a bounded implementation task.
2. Pass only the context needed: `--file` for exact artifacts, `--cwd "$PWD"` for repo work (pipe `git diff` only as supplemental focus, not the only context), `--skill` for quality-sensitive output.
3. Prompt for falsification, not validation: "Find the strongest reason this plan is wrong" beats "review my plan".
4. Verify the result yourself — run tests, inspect diffs. If two agents disagree, identify the factual claim that would settle it and inspect code, docs, or live state directly.
5. After a target returns, note what it claimed, what you verified, and what you rejected; never present its output as confirmed unless independently verified.

## Guardrails

- Don't delegate deterministic work a script or test can answer, and don't ask several agents the same broad question and average the answers.
- Use `--read-only` for untrusted diffs, files, or prompts; the full-access warning on stdin/`--file` input is meaningful. Yoyo is intentionally powerful — request the capability the task needs rather than removing capabilities to prevent misuse.
- A worker must not perform irreversible operations (releases, credential changes, destructive git) unless the human explicitly asked and every step is verifiable.
- Workflow jobs default to read-only; feeding one agent's output into a write-capable job or a job with raw `agent_args` requires the workflow to set `allow_untrusted_context: true` with justified risk.
- If the target agent fails or lacks credentials, report that directly and continue with the best local verification path.
- For long or noisy tasks set `--trace-id`, `--max-output-bytes`, and `--max-input-bytes` (an aggregate cap across stdin and `--file`) so failures are auditable and output is bounded.

## More

- `yoyo doctor [--live]`, `yoyo agents`, `yoyo skills`, `yoyo update` — setup, health probes, discovery.
- `yoyo workflow <name|path> --input "..." --json` (`--list` to discover) — reusable multi-agent workflows; prefer one checked spec over ad hoc fan-out, and dry-run before spending tokens.
- `yoyo imagegen "<prompt>" --out file.png` — real verified image via codex's GPT-image tool; see the `yoyo-imagegen` skill for prompt recipes.
- Liveness tuning (heartbeat interval, stdin wait, env vars): `yoyo ask --help` or the README.
