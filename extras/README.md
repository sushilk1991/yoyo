# Optional local advice

```sh
yoyo advise --file extras/advice-example.json
```

Experimental checks for repeated unsuccessful attempts, unfamiliar failure categories, and possible duplicate review findings. Uses `Mapika/decider-0.8b` converted to 4-bit MLX on Apple Silicon. Every result is advisory. Existing Yoyo commands stay unchanged.

## Install once

From the Yoyo checkout, with `uv` installed:

```sh
uv venv --python 3.12 ~/.local/share/yoyo/decider/venv
uv pip install --python ~/.local/share/yoyo/decider/venv/bin/python 'mlx-lm==0.31.3' 'mlx==0.32.2'
~/.local/share/yoyo/decider/venv/bin/python extras/decider_mlx.py --prepare ~/.local/share/yoyo/decider/model
```

Setup downloads the pinned 1.5 GB source checkpoint and converts it. The prepared weights occupy about 424 MB, plus the tokenizer and runtime. Conversion temporarily needs more memory than inference. The source remains in the Hugging Face cache. Setup refuses to overwrite an existing model directory.

`yoyo advise` finds the helper through Yoyo's recorded source checkout; run `./install.sh` first or set `YOYO_SOURCE_ROOT` to that checkout. `YOYO_DECIDER_HOME` changes the default installation directory; it must contain `venv/bin/python` and `model/`.

Inference reads local files only. No daemon, network inference, background polling, or default model load. One batch loads one model and exits; overlapping calls return a busy error. The default and maximum deadline is 30 seconds.

## Supply evidence

Use a JSON object with a `checks` array. IDs must be unique. All three kinds can share one batch:

| Kind | `state` | Accepted advice |
| --- | --- | --- |
| `repeat` | `{"previous":"…", "latest":"…"}` | `repeated` or `unclear` |
| `failure` | Failure text, including the relevant error | `account`, `environment`, `implementation`, or `unclear` |
| `duplicate` | `{"first":"…", "second":"…"}` | `same`, `different`, or `unclear` |

Include attempted changes and observed results in repeat checks. Include file/line and cause in both findings for duplicate checks. Give each compared pair an ID linking back to the original findings; a `same` result suggests grouping that pair, never deleting either finding or treating it as verified consensus.

Inputs are limited to 64 KiB, 32 checks, and 1,024 tokens per check including its fixed question. Overlong checks abstain without truncating their evidence. Empty evidence abstains without loading the model. Keep full logs and reviews in their existing files; pass focused excerpts explicitly.

The JSON response contains `advisory: true`, model revision, `decisions`, and timing. `choice` is the filtered suggestion; `candidate` and `scores` expose the raw model output for inspection. Scores below 0.90 abstain. This threshold is a conservative policy, **not calibrated accuracy**. Repetition checks never issue a progress certificate, even when the raw model predicts new evidence.

Exit 0 means the advice request ran, including abstentions. Exit 2 means unavailable, busy, invalid, failed, or timed out. Neither exit code describes whether the underlying coding task succeeded. Keep the normal workflow when advice is unavailable.

## Limits and measurements

On this Mac's M4 Max, a fixed 30-case smoke set gave NanoJev 12/30 and Decider 0.8B MLX 4-bit 23/30 before filtering. These are small, author-written fixtures, not production accuracy estimates. NanoJev classified every review pair as a duplicate, including contradictions, so it is not used.

The Decider benchmark measured about 21 ms per loaded decision. A fresh CLI process took 3.75 seconds for 30 checks after the first launch; that first launch took 26.83 seconds. Peak RSS was about 767 MB. Active model memory was about 424 MB; RSS and GPU allocations overlap on unified memory and must not be added together. Other development tools were active during these runs.

The helper follows the publisher's state-first option-token readout with the MLX Qwen3.5 backbone. BF16 MLX and the publisher's PyTorch runner chose identical labels on all 30 fixtures; their probabilities differed by up to 0.041. Quantization changed some decisions. This is a local adaptation, not an upstream MLX release or proof of general equivalence.

Sources checked September 22, 2026: [NanoJev](https://huggingface.co/C-Tianyu/NanoJev), [Decider 0.8B](https://huggingface.co/Mapika/decider-0.8b), [MLX LM](https://github.com/ml-explore/mlx-lm).

## Bounded research

[The research runner](autoresearch/program.md) searches prompt wording and evidence formatting with fixed weights. It freezes development and holdout data, records every candidate, monitors memory, and keeps the installed helper unchanged. The September 22 follow-up improved a new 45-case holdout from 31 to 36 correct raw labels with similar memory use. It returned 13 correct suggestions instead of 14 while removing one wrong suggestion, so the preset coverage gate rejected automatic promotion. The candidate remains a research result.

A third round required normal memory pressure and tested 60 fresh cases. No new prompt beat that candidate under the existing rules. The same candidate scored 44/60 versus the installed baseline's 41/60, issued 16 correct suggestions versus 15, and no wrong suggestions versus one. Peak process RSS across all 16 trials was 767 MB. This holdout passed the gate, but it does not erase the earlier coverage regression or establish production accuracy. Installed behavior remains unchanged.
