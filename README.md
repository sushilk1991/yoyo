# yoyo

**Let your coding agents call each other.**

`yoyo` is a tiny CLI that lets Claude Code, Codex, Pi — any agent CLI — delegate to, review, and cross-check one another. Ask two or three vendors the same question, compare, and keep the best answer. One Python file, zero dependencies, no daemon.

```bash
# Claude wrote it — let Codex tear it apart
run_id=$(yoyo ask codex --role review --read-only --cwd "$PWD" --background "Find bugs that would block shipping. Cite file/line.")
yoyo wait "$run_id"

# Same task, two vendors — a third one judges the winner
yoyo ask codex,claude "Design the rate limiter. Name the riskiest assumption." --judge grok
```

## Why

Different models fail differently. Run the same task across vendors and **agreement is signal, disagreement is your work list** — the cross-model version of self-consistency and best-of-n sampling, with an LLM judge (or you) picking the winner. It's the cheapest quality upgrade an agent workflow can get, and every piece of yoyo exists to make it one command:

- **Second opinions** that aren't the same model agreeing with itself.
- **Code review** by a vendor that didn't write the code.
- **Research** as parallel adversarial perspectives, not one confident answer.
- **Loops** so long work can keep going at flat cost.

yoyo doesn't grade or constrain agent output — you (or your agent) stay the orchestrator, composing calls step by step and verifying what matters.

## Install

```bash
git clone https://github.com/sushilk1991/yoyo.git && cd yoyo && ./install.sh
```

Installs `~/.local/bin/yoyo` plus caller skills for Claude Code, Codex, Cursor, Antigravity, Pi, OpenCode, Grok, and Muse Code. Requires Python 3.9+ and at least one supported target CLI (`codex`, `claude`, `pi`, `cursor-agent`, `agy`, `grok`, or `muse`) on PATH, signed in through its native CLI. Antigravity (`agy`) is the only built-in Google target.

Any caller with an allowed shell tool can launch a target using the background/wait recipe below. Restart the caller after installing skills. Update with `yoyo update`; repair caller skills with `yoyo install-skill`, including from the installed binary. `yoyo doctor` reports missing guidance, stale copies, and directories left behind by a rename.

## Run it in the background

A delegate is a full agentic session — median call here is about four minutes, and the slowest tenth run past thirteen. If the caller is an agent whose exec tool yields in seconds, a foreground call gets killed and looks like a hang. So detach it:

```bash
run_id=$(yoyo ask claude --role review --cwd "$PWD" --background "Audit the auth module.")
yoyo wait "$run_id" --timeout 25    # 124 = still running, wait again; 0 = done
```

The detached run gets its own session, so it survives the caller's turn ending. `--background` works on `ask`, `loop`, `research`, and `review`.

## Status in your main terminal

```text
YOYO · 2 running · codex: Review auth (2m) · claude: Fix layout (45s)
```

`yoyo status` prints a compact summary of active recorded calls in the current project. Claude Code can append it to its existing status line; Pi can show it through the bundled footer extension. Codex uses Herdr's bottom bar because its native footer has no command-backed custom field. No split is required. See [footer setup](docs/REFERENCE.md#status-footers).

## Watch delegated agents

```bash
yoyo watch                                   # read-only live dashboard
yoyo watch --cwd "$PWD"                      # this project's runs
yoyo watch --open ghostty                     # separate macOS window
yoyo watch --open herdr --split right         # run inside a Herdr pane
```

Select runs with Up/Down, switch output streams with Tab, scroll with Page Up/Down, show the stored prompt with `p`, and detach with `q`. Closing the viewer leaves agents running. `yoyo watch --once` prints a snapshot; `--json` returns structured data.

This shows recorded tasks and available captures, not the native interactive agent screen. Buffered providers can stay quiet until completion; streaming adapter changes are not included. Ghostty automation requires 1.3+ on macOS and Automation permission. Herdr launchers require its CLI and a compatible running server. See [watch reference](docs/REFERENCE.md#watch).

## The 60-second tour

```bash
# Cross-vendor consensus review of your current git diff
yoyo review --cwd "$PWD" --background

# Deep research: 5 lenses (for/against/facts/prior-art/execution) across vendors,
# synthesized into a decision brief that surfaces the disagreements
yoyo research --cwd "$PWD" --background "Should we move the core engine to Rust?"

# Your lenses, your synthesis — nothing is canned
yoyo research --lens "Investigate only the licensing risk, citing license texts" --no-synthesis "..."

# Fresh-context loop: each iteration is a new session reading a small state file,
# so cost stays flat. Rotate vendors so blind spots don't compound.
yoyo loop codex,claude --cwd "$PWD" --max-iter 30 --background "Fix the failing tests, one per iteration."

# Queue mode: a markdown checklist becomes N verifiable increments — one item per
# iteration, DONE rejected while any box is unchecked. --brief injects shared
# repo knowledge so fresh contexts stop re-deriving it.
yoyo loop claude --cwd "$PWD" --queue tasks.md --brief .yoyo/brief.md --background "Work the queue."

# Bonus: real images via GPT-image
yoyo imagegen "Hand-drawn architecture diagram, four boxes, bold arrows" --out arch.png
```

## Agents

| Agent | Vendor | Role it plays best |
| --- | --- | --- |
| `codex` | OpenAI | Default reviewer & second opinion; strongest at browser and computer use |
| `claude` | Anthropic | Default worker for scoped edits |
| `pi` | Pi | Cheap, fast, small scoped tasks |
| `cursor` | Cursor | On-demand worker on a different model family |
| `agy` | Google | Antigravity CLI; reuses its native Google login; `--read-only` is its plan mode with writes auto-denied |
| `grok` | xAI | On-demand: fourth vendor for adversarial cross-checks; `--read-only` is a read-tools-only allowlist |
| `muse` | Meta | Muse Code; unattended sandboxed workers, file-read/search-only reviews |

Custom agents are a JSON entry away. Check everything works with `yoyo doctor --live`.

Use `yoyo models codex --json` or `yoyo models claude --json` to discover the native account type and current model choices without inference. The calling agent selects an available subscription model with `--model`: a smaller tier for bounded, checkable work; the configured default for complex or ambiguous work. No local model, routing call, or daemon is added. Other providers retain their native discovery commands, such as `cursor-agent --list-models`.

For loops, `--verify 'COMMAND'` checks a DONE claim and feeds failures into the next iteration. `--max-stall 3` stops repeated iterations that leave the state and queue unchanged. Both are opt-in; existing commands retain their behavior.

Optional Apple Silicon advice is available through `yoyo advise --file evidence.json`: suspected repeated attempts, failure categories, and possible duplicate findings. It loads a small MLX model only for that batch and returns suggestions without changing jobs or evidence. [Setup, input format, and measured limits](extras/README.md).

## The pieces

| Command | What it does |
| --- | --- |
| `yoyo ask <agent(s)> "..."` | One call — or a parallel best-of-n fan-out with `--judge` |
| `yoyo review` | Cross-vendor consensus review of the current git diff (`--stance unanimous\|any` precision/recall dial) |
| `yoyo research "..."` | Parallel perspectives (lenses fully yours to define) → decision brief |
| `yoyo loop <agent(s)> "..."` | Fresh-context iterations at flat cost, with `--queue`, `--brief`, and `--spec` |
| `yoyo wait` / `runs autopsy` | Poll a detached run; reconstruct one that died |
| `yoyo runs audit` | Per-agent outcomes and p50/p90 over a window of the ledger |
| `yoyo models codex\|claude` | Native account and model discovery, with no inference |
| `yoyo advise --file evidence.json` | Optional local advisory checks; requires separate MLX setup |
| `yoyo imagegen "..."` | Real raster images via GPT-image |
| `--session` / `--background` | Durable and detached calls |
| inbuilt `yoyo-fable-mode` skill | Evidence discipline injected into every delegation (`YOYO_DEFAULT_SKILLS=""` disables) |

Full flags, the access model, custom agents, and design details: **[docs/REFERENCE.md](docs/REFERENCE.md)**.

## Philosophy

- **Boring on purpose.** One Python script, subprocess calls, explicit prompts. Nothing to babysit.
- **The agent is the orchestrator.** yoyo provides primitives, not pipelines — the calling agent (or you) decides each next step from the last result.
- **Output is evidence, not truth.** Reviews report findings; they never gate control flow on their own. Verify before you trust.
- **Subtract on sight.** Surface that nobody reaches for is a tax on every caller who reads `--help`. Flags earn their place by being used.

## Test

```bash
python3 -m unittest discover -s tests
```

MIT licensed.
