---
name: yoyo-research
description: Deep research on a decision via yoyo research — parallel cross-vendor lenses synthesized into a brief that surfaces where they disagree. Use for "should we X?", technology choices, and roadmap questions worth more than one perspective.
---

# Yoyo Research

Each lens runs as an independent agent call spread across vendors, then a synthesizer merges them into a brief: convergence, **tension**, key evidence, open questions, options.

Research fans out several multi-minute calls, so always detach it:

```bash
run_id=$(yoyo research --cwd "$PWD" --background "Should we move the core engine to Rust?")
yoyo wait "$run_id" --timeout 30     # 124 = still running, wait again
```

## Shaping the question

Phrase it with its decision context: what is being decided, the constraints, and what evidence would settle it. A vague topic produces vague lenses — that failure happens at the prompt, not in the synthesis.

`--file FILE` gives every researcher the same brief or spec, so they do not each re-derive it.

## Shaping the lenses

Defaults are `proponent,skeptic,analyst,explorer,pragmatist` across `codex,claude,pi`. Override freely:

```bash
yoyo research --lenses regulatory,market --agents codex,claude,cursor "..."   # ad-hoc angles by name
yoyo research --lens "Investigate only the licensing implications, citing actual license texts" "..."
yoyo research --no-synthesis --json "..."                                     # raw perspectives
```

Repeat `--lens` for full free-text instructions. Duplicate a lens to land it on different vendors for a best-of-n sample. `--synthesis-prompt "..."` replaces the brief format.

Reach for `--no-synthesis` when you hold the decision context — read the raw perspectives and synthesize against everything else you know.

## Reading the result

**The tension section is the work list.** Where lenses disagree is what to verify before deciding; the synthesis surfaces disagreement rather than resolving it. Lenses can cite stale or wrong facts, so check the load-bearing claims against the code, the docs, or a primary source yourself.
