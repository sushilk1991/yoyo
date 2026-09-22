# Yoyo advice research

Run `~/.local/share/yoyo/decider/venv/bin/python extras/autoresearch/run.py --help`.

Improve the existing local classifier with a bounded prompt search. Keep the 0.8B 4-bit weights fixed. Search only question wording, choice descriptions/order, and evidence rendering. Do not edit coding-model routing, retry limits, DONE checks, or raw review evidence.

Freeze development and holdout files before running. Development starts with the previous 30 cases. Holdout uses 60 new authored cases and must not influence selection. Do not edit labels or repeatedly select against holdout results. These authored labels are smoke evidence, not an independent production benchmark.

Rank candidates by raw correct labels. Require no development regression in any task kind, no increase in wrong issued advice, and no loss of correct issued advice. Abstaining on everything must not win. Evaluate only the selected candidate and baseline on holdout, once. Promote only if holdout improves overall without a task regression or worse advice errors/coverage. Otherwise retain the installed baseline.

Use one inference child at a time and the same lock as `yoyo advise`. The default starts only under normal macOS memory pressure. Explicit `--pressure warning` also permits warning-level pressure, while stopping at critical pressure. Bound each child to 30 seconds and the entire search to 180 seconds. Stop if pressure exceeds the selected policy, sampled process RSS exceeds 1 GiB, or reported MLX allocation exceeds 768 MiB. Sampling can detect a transient overshoot after it occurs; reject the completed trial if its reported peak exceeds either budget. Limit MLX cache to 32 MiB and each input to 1,024 tokens. MLX's allocation setting is not a total-process hard limit; the parent must monitor RSS and reap stopped children. Never stop unrelated user processes.

Record candidate configuration, source/data hashes, measured scores, latency, peak memory, failures, and keep/discard result. A completed ledger is the resume context. Do not install candidates automatically; run focused regression tests and a real CLI smoke before promoting a passing candidate.

## Repeatable commands

```sh
~/.local/share/yoyo/decider/venv/bin/python extras/autoresearch/run.py --out .yoyo/research-first
~/.local/share/yoyo/decider/venv/bin/python extras/autoresearch/run.py --dataset-dir extras/autoresearch/round2 --out .yoyo/research-second
```

Each output directory must be new. `--pressure warning` explicitly allows an already busy Mac to run under the monitored limits; critical or unreadable pressure stops the trial. This is a bounded search over agent-proposed candidates with automatic per-task recombination, not a full GEPA implementation or model training.

Round two retires the first 60-case holdout into the 90-case development set. Its final holdout has 45 new authored cases. Once a holdout result influences the next proposal, retire that holdout and create a new one before evaluating again. Do not present repeatedly tested data as unseen evidence.

[Round three](round3/program.md) uses 135 development cases and 60 fresh holdout cases. Normal pressure is required by the user; do not use the warning override for further experiments. Five follow-up proposals extend the original seven. The previous best won again: 44/60 correct raw labels versus the installed baseline's 41/60, with 16 correct suggestions versus 15 and zero wrong suggestions versus one. No new proposal improved on that candidate while meeting the selection rules. This holdout passed; the prior 45-case coverage regression remains recorded. Stop prompt search here and evaluate independently labelled task traces before installation.

## Research sources

- [Karpathy autoresearch](https://github.com/karpathy/autoresearch): bounded experiments and measured keep/discard decisions. Its original training implementation targets NVIDIA GPUs; this experiment uses the existing MLX inference model.
- [GEPA](https://github.com/gepa-ai/gepa): use failure traces to propose textual changes and select measured improvements.
- [Multiple-choice order sensitivity](https://arxiv.org/abs/2309.03882): changing option positions can change answers, so order is an explicit test variable.
- [MLX memory limit](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_memory_limit.html) and [cache limit](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_cache_limit.html): allocation guidance and cache control need separate process monitoring.
