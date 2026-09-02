---
name: yoyo-vision
description: "Understand any image - screenshots, error dialogs, UI mockups, diagrams, charts, photos, scanned documents. Delegates the looking to a vision-capable agent (codex, or claude as a fallback) through yoyo, so image pixels never enter your own context. Use whenever you cannot see an image yourself: your read tool reports the model does not support images, the picture is referenced but not rendered, or you want a second pair of eyes on a screenshot before acting on it."
---

# Yoyo Vision

Image understanding for any agent. When a task involves a picture you cannot see — the classic case is an error screenshot and a model without vision — delegate the looking to an agent that has vision and work from its report. Never guess what an image shows, and never pretend to have read one.

## How to ask

The image never enters your context — only the answer travels back. (The delegate does upload the image bytes to its own model provider, so keep that in mind for sensitive screenshots.) Quote absolute paths.

**codex (preferred — takes the image bytes directly):**

```bash
yoyo ask codex --read-only \
  --agent-arg=-i --agent-arg="/path/to/image.png" \
  "Look at the attached image. Transcribe every piece of text exactly, then answer: <your actual question>"
```

`-i`/`--image` is codex's own attach flag. Each `--agent-arg` becomes one raw argv token and dash-prefixed values need the `--agent-arg=-i` form (a space would make argparse read `-i` as yoyo's own flag). Multiple images: repeat the pair.

**claude (fallback — reads the file itself):**

```bash
yoyo ask claude --read-only \
  "Read /path/to/image.png with your Read tool (it renders images). Transcribe every piece of text exactly, then answer: <your actual question>"
```

Claude Code's Read tool renders images, so naming the path in the prompt is enough. If claude reports it cannot access the path, grant the directory: `--agent-arg=--add-dir --agent-arg=/path/to/dir`. Do not use `--file` for images — it injects the file as text and turns a screenshot into mojibake.

## Writing the question

Brief the delegate the way you would brief a person holding the picture:

- Say what to **extract verbatim** (error text, stack traces, numbers, code) versus what to **interpret** (layout, UI state, what the error means).
- Ask for transcription before interpretation — it gives you the raw evidence to reason over, and it is what you can spot-check.
- Name the decision the answer feeds: "I need to decide whether to rerun or fix config" gets a sharper answer than "what is this?"

```bash
run_id=$(yoyo ask codex --read-only --background \
  --agent-arg=-i --agent-arg="/Users/me/Desktop/Screenshot.png" \
  "Transcribe the error dialog exactly, then tell me the most likely cause and the single next command to run.")
yoyo wait "$run_id" --timeout 30    # 124 = still running, wait again
```

## Spot-check

If a load-bearing decision rides on the reading — a fix, a payment amount, a destructive command — run the same question on a second vision-capable agent (codex with `-i`, claude via its Read tool) and compare the transcriptions yourself. Two vision models misreading the same pixels the same way is rare but real; agreement between vendors is strong evidence. (`--judge` does not help here: it compares two completed answers, it does not look at the image.)

## Notes

- `--read-only` is right for pure looking. Drop it only if the delegate must produce files (e.g. cropping).
- Long images, dense terminals, and handwriting are the common failure modes — ask for a region-by-region transcription when the first answer looks truncated.
- For video (a folder of frames, a screen recording), see the `yoyo-watch` skill instead.
- Generating images is a different job: see the `yoyo-imagegen` skill.