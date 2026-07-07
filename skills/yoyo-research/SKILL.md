---
name: yoyo-research
description: Deep research on a question or decision via yoyo research — parallel cross-vendor lenses (proponent/skeptic/analyst/explorer/pragmatist or your own) synthesized into a decision brief that surfaces disagreement. Use for "should we X?", technology choices, roadmap questions, and any decision worth multiple independent perspectives.
---

# Yoyo Research

`yoyo research` investigates one question from several angles at once: each lens runs as an independent agent call (spread across vendors), then a synthesizer merges them into a decision brief — convergence, tension (the most useful part), key evidence, open questions, options. Expect several minutes of wall clock; run it in the background for anything long.

## Command

```bash
yoyo research --cwd "$PWD" "Should we move the core engine to Rust?"
yoyo research --lenses proponent,skeptic,analyst --agents codex,claude,cursor "..."
yoyo research --lens "Investigate only the licensing implications, citing actual license texts" "..."
yoyo research --no-synthesis --json "..."     # raw perspectives; you synthesize
run_id=$(yoyo research --background "...")    # detach; poll with `yoyo wait "$run_id"`
```

- Phrase the question with its decision context: what is being decided, constraints, what evidence would settle it. A vague topic gets vague lenses.
- Default lenses are `proponent,skeptic,analyst,explorer,pragmatist`; defaults agents are `codex,claude,pi`. Lenses are yours: unknown single words become ad-hoc angles (`--lenses regulatory,market`), repeatable `--lens` takes full free-text instructions, duplicates land on different vendors (best-of-n).
- `--file FILE` gives every researcher shared context (a brief, a spec, the relevant doc) so they don't each re-derive it.
- Prefer `--no-synthesis` when you hold the decision context yourself — read the raw perspectives and synthesize with everything else you know. `--synthesis-prompt "..."` replaces the brief format.
- Long question? Detach with `--background`: it prints a run id immediately and records the run in the ledger — `yoyo wait <run_id>` blocks until the brief lands (exit 124 = still running, wait again), `yoyo runs show <run_id> --json` fetches the full envelope.

## Reading the result

The tension section is the work list: where lenses disagree is exactly what to verify before deciding. Verify load-bearing claims yourself (code, docs, tests, primary sources) — the brief is evidence to check, not an oracle. Lens output can cite stale or wrong facts; the synthesis surfaces disagreement rather than resolving it.
