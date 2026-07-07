# yoyo

**Let your coding agents call each other.**

`yoyo` is a tiny CLI that lets Claude Code, Codex, Pi — any agent CLI — delegate to, review, and cross-check one another. Ask two or three vendors the same question, compare, and keep the best answer. One Python file, zero dependencies, no daemon.

```bash
# Claude wrote it — let Codex tear it apart
yoyo ask codex --role review --read-only --cwd "$PWD" "Find bugs that would block shipping. Cite file/line."

# Same task, two vendors — a third one judges the winner
yoyo ask codex,claude "Design the rate limiter. Name the riskiest assumption." --judge grok
```

## Why

Different models fail differently. Run the same task across vendors and **agreement is signal, disagreement is your work list** — the cross-model version of self-consistency and best-of-n sampling, with an LLM judge (or you) picking the winner. It's the cheapest quality upgrade an agent workflow can get, and every piece of yoyo exists to make it one command:

- **Second opinions** that aren't the same model agreeing with itself.
- **Code review** by a vendor that didn't write the code.
- **Research** as parallel adversarial perspectives, not one confident answer.
- **Loops and cron** so all of it can keep running without you.

yoyo doesn't grade or constrain agent output — you (or your agent) stay the orchestrator, composing calls step by step and verifying what matters.

## Install

```bash
git clone https://github.com/sushilk1991/yoyo.git && cd yoyo && ./install.sh
```

Installs `~/.local/bin/yoyo` plus skills that teach Claude Code, Codex, Pi, and OpenCode how to use it. Requires Python 3.9+ and at least one agent CLI (`codex`, `claude`, or `pi`) on PATH. Update later with `yoyo update`.

## The 60-second tour

```bash
# Cross-vendor consensus review of your current git diff
yoyo review --cwd "$PWD"

# Deep research: 5 lenses (for/against/facts/prior-art/execution) across vendors,
# synthesized into a decision brief that surfaces the disagreements
yoyo research --cwd "$PWD" "Should we move the core engine to Rust?"

# Your lenses, your synthesis — nothing is canned
yoyo research --lens "Investigate only the licensing risk, citing license texts" --no-synthesis "..."

# Fresh-context loop: each iteration is a new session reading a small state file,
# so cost stays flat. Rotate vendors so blind spots don't compound.
yoyo loop codex,claude --cwd "$PWD" --max-iter 30 --gate "pytest -q" "Fix the failing tests, one per iteration."

# Queue mode: a markdown checklist becomes N verifiable increments — one item per
# iteration, DONE rejected while any box is unchecked. --brief injects shared
# repo knowledge so fresh contexts stop re-deriving it.
yoyo loop claude --cwd "$PWD" --queue tasks.md --brief .yoyo/brief.md --gate "pytest -q" "Work the queue."

# Schedule it. The loop picks up where it left off, every night, until it's verifiably done.
yoyo cron add nightly --schedule "0 2 * * *" --cwd "$PWD" -- loop claude --max-iter 5 --gate "pytest -q" "Work through TODO.md"

# Long call? Detach and keep working.
run_id=$(yoyo ask claude --role review --cwd "$PWD" --background "Audit the auth module.")
yoyo wait "$run_id"

# Bonus: real images via GPT-image
yoyo imagegen "Hand-drawn architecture diagram, four boxes, bold arrows" --out arch.png
```

## Agents

| Agent | Vendor | Role it plays best |
| --- | --- | --- |
| `codex` | OpenAI | Default reviewer & second opinion |
| `claude` | Anthropic | Default worker; reports cost, so loop budgets are enforced |
| `pi` | Pi | Cheap, fast, small scoped tasks |
| `cursor` | Cursor | On-demand: cross-vendor model picker in one CLI |
| `agy` | Google | On-demand: Gemini-family tiebreaker (full-access only) |
| `grok` | xAI | On-demand: fourth vendor for adversarial cross-checks |

Custom agents are a JSON entry away. Check everything works with `yoyo doctor --live`.

## The pieces

| Command | What it does |
| --- | --- |
| `yoyo ask <agent(s)> "..."` | One call — or a parallel best-of-n fan-out with `--judge` |
| `yoyo review` | Cross-vendor consensus review of the current git diff (`--stance unanimous\|any` precision/recall dial) |
| `yoyo research "..."` | Parallel perspectives (lenses fully yours to define) → decision brief |
| `yoyo loop <agent(s)> "..."` | Fresh-context iterations at flat cost, with opt-in gates/checkers |
| `yoyo cron add/list/rm/run` | Schedule any yoyo command via crontab — no daemon |
| `yoyo workflow <name>` | Rerun a saved multi-agent pipeline by name |
| `yoyo chat` / `--session` / `--background` | Interactive, durable, and detached calls (`--background` on every long-running command) |
| inbuilt `fable-mode` skill | Reasoning-discipline harness injected into every delegation by default (`YOYO_DEFAULT_SKILLS=""` disables) |

Full flags, the access model, custom agents, and design details: **[docs/REFERENCE.md](docs/REFERENCE.md)**.

## Philosophy

- **Boring on purpose.** One Python script, subprocess calls, explicit prompts. Nothing to babysit.
- **The agent is the orchestrator.** yoyo provides primitives, not pipelines — the calling agent (or you) decides each next step from the last result. The only deterministic checks are ones you opt into: shell gates (tests, builds) and independent checkers.
- **Output is evidence, not truth.** Reviews report findings; they never gate control flow on their own. Verify before you trust.

## Test

```bash
python3 -m unittest discover -s tests
```

MIT licensed.
