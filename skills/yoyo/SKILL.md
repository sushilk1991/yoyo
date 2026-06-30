---
name: yoyo
description: Delegates tasks to Codex, Claude, Pi — or, on demand, Cursor, Antigravity (agy), and Grok — as subagents, reviewers, workers, or second-opinion partners via the yoyo CLI. Use for multi-agent coordination, independent code review, scoped delegation, adversarial checks, fresh-context loops, and agent-to-agent handoffs.
---

# Yoyo Multi-Agent Coordination

`yoyo` calls another CLI agent as a subprocess or interactive session. Treat the target agent's output as evidence to verify, not as an oracle.

## Choosing an agent

Default to the core trio — they support `--session` follow-ups and are the battle-tested paths:

- `codex` — OpenAI Codex. Default reviewer and second opinion; also powers `yoyo imagegen`.
- `claude` — Anthropic. Default worker and the only agent that reports per-call cost, so `yoyo loop --budget-usd` is enforced only here.
- `pi` — lightweight and cheap; good for small scoped worker tasks and quick opinions.

`cursor`, `agy`, and `grok` are **on-demand only**: one-shot (`--session` is rejected), reach for them when their specific edge fits — not as part of the regular rotation, and never to fan the same question out to every agent.

- `cursor` (Cursor agent CLI) — pro: cross-vendor model picker in one CLI (`--model gpt-5`, `sonnet-4`, ...; `cursor-agent --list-models`), editor-grade code edits. Con: needs `cursor-agent login` or `CURSOR_API_KEY`, bills through your Cursor plan. Use when you want a specific model yoyo's other agents don't expose, or an independent implementation pass.
- `agy` (Google Antigravity CLI — the Gemini CLI successor; Gemini CLI stops serving consumers 2026-06-18) — pro: Google's Gemini 3.x models plus a hosted Claude/GPT-OSS picker (`agy models`), large context, distinct vendor for tiebreaks; signs in via browser on first model call. Con: **full-access only — no headless read-only mode**, so it can't be a `review` or loop `--checker` agent or take `--read-only` (use codex/claude/pi there); takes the prompt as the `-p` value (handled internally); `yoyo chat agy` takes no initial prompt. Use for a full-access worker pass or a third-vendor opinion where editing is acceptable.
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

# Slash-command pass-through: --raw sends the prompt verbatim (no role/context
# wrapper) so the target CLI sees the leading /command and can expand it
yoyo ask claude --raw "/goal ship the release"
```

## Consensus review

```bash
yoyo review --cwd "$PWD" --caller claude                 # codex + claude review the current diff in parallel
yoyo review --agents codex,claude,grok --base main --json   # review needs read-only, so not agy
```

`yoyo review` reviews the current git diff (uncommitted changes if the tree is dirty, otherwise `--base...HEAD`) with each named agent independently, read-only, in parallel, then a synthesizer agent (default: first of `--agents`) merges them into CONSENSUS findings (raised by ≥2 reviewers — the trustworthy ones) and SINGLE-REVIEWER findings (unconfirmed). Prefer it over hand-rolled fan-out when reviewing a diff before commit/PR; `--pr` posts the result as a GitHub PR comment via `gh`. Treat consensus findings as worth verifying first, not as automatically true.

## Deep research

```bash
yoyo research --cwd "$PWD" --caller claude "Should we move the core engine to Rust?"
yoyo research --lenses proponent,skeptic,analyst --agents codex,claude --json "Is WebGPU ready for our renderer?"
yoyo research --lenses regulatory,market,security --file rfc.md "Adopt passkeys for login?"
```

Use `yoyo research` to gather **diverse perspectives before deciding what to do next** — not to find one right answer. Each lens runs as one parallel agent call investigating from a single angle, then a synthesizer (default: first of `--agents`) produces a decision brief: where perspectives converge, where they're in TENSION (the most useful section), key evidence, open questions, and options for next steps. Lenses are assigned to `--agents` round-robin, so the for/against split lands on different vendors by default — a genuine disagreement, not the same model arguing with itself.

Default lenses: `proponent,skeptic,analyst,explorer,pragmatist` (case FOR / case AGAINST / first-principles facts / prior art & alternatives / execution path). Unknown lens names become ad-hoc angles, so `--lenses regulatory,market,security` works for domain-specific research. Each lens is one call — subset `--lenses` to spend less.

Unlike `review`, research defaults to **full-access** so agents can use web search, fetch, and code execution to investigate; `--read-only` restricts them but limits those tools on some agents. The synthesis surfaces disagreement rather than averaging it — verify the load-bearing claims yourself before acting, and don't read consensus as proof.

## Writing good yoyo prompts

Strong delegated prompts are specific about the outcome and narrow about the scope. Put the instruction first, then separate context with `--file`, stdin, or clear tags. Prefer concrete output contracts over vague quality words: "List correctness bugs with file/line and a reproduction path" beats "review carefully." Say what the agent should do, not only what it should avoid.

Use this checklist before delegating:

1. Name the role and success criterion: opinion, review, worker; what would count as done or useful.
2. Bound the surface: repo `--cwd`, exact files, target branch/diff, allowed edits, and explicit exclusions.
3. Provide relevant evidence: logs, failing command, user symptom, screenshots, API docs, or `--file` context.
4. Request falsification for reviews/opinions: "Find the strongest reason this is wrong" or "Prioritize bugs that would change the ship decision."
5. Specify the output format only as much as needed: findings first, file/line pointers, commands run, or `VERDICT: PASS/FAIL` for checkers.
6. Add examples only when format matters; otherwise keep the prompt short and let the repo be the source of truth.
7. For code work, include the verification path: tests to run, gate command, browser check, or live proof expected.

Skills are the main way to steer *how* work is done. Prefer explicit `--skill name` when you know the skill, because missing explicit skills fail loudly. If another agent wants a delegated call to use a skill, have it produce or call the yoyo command with `--skill`; do not rely on skill names written in prose to change yoyo behavior.

Prompt templates:

```bash
yoyo ask codex --role review --read-only --cwd "$PWD" --file src/auth.ts --caller claude \
  "Find correctness or security bugs that would block shipping. Cite file/line and explain the failing path. Ignore style-only issues."

yoyo ask claude --role worker --cwd "$PWD" --skill frontend-design --caller codex \
  "Implement the account settings empty state. Match existing components, touch only the settings surface, and run the focused frontend test."

yoyo ask pi --role opinion --read-only --cwd "$PWD" --skill api-design-principles --caller codex \
  "Challenge this API shape; list assumptions, risks, and the smallest better alternative."
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

Use `yoyo loop` instead of grinding many rounds inside one ever-growing session: a long session re-reads its whole context on every call and cost compounds; a loop runs each iteration as a brand-new fresh-context session. Continuity lives in a state file (default `.yoyo/loop-state.md`) the worker reads first and rewrites before ending. The loop stops on an accepted `STATUS: DONE` in the state file, a `STOP` file beside it, `--max-iter`, `--budget-usd` (enforced for claude, which reports per-iteration cost), or `--max-fail` consecutive failures. `--background` detaches the whole loop; poll with `yoyo wait` as above.

Verified completion (opt-in): by default the worker grades its own `STATUS: DONE`. For unattended loops add an objective check so it can't quietly exit half-done. `--gate "pytest -q"` (repeatable) only accepts `STATUS: DONE` when the command exits 0; a failed gate strips the false DONE, appends the failure to the state file, and continues. `--checker <agent>` adds an independent read-only checker (blind to the worker's prose; judges goal + git diff + repo, ends in `VERDICT: PASS/FAIL`); pair with `--checker-model <cheap-tier>`. `--done-policy worker|gate|checker|gate+checker` (auto-derived from the flags) controls which apply. yoyo never grades prose — gates are closed-form evidence only. `--spec VISION.md` adds an immutable spec re-read every iteration (never rewritten) to hold constraints that the lossy state rewrite would otherwise drop. The `--json` summary reports `verified`, `gate_failures`, and `checker_rejections`.

State-file guards: reusing a state file recorded for a different task fails loudly (pass a fresh `--state` path per task, or delete the state file and its `.task` sidecar to start over); a leftover `STOP` file is cleared at startup; a flock-held `.lock` file rejects a second concurrent loop on the same state file and releases automatically when the holding process exits.

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
