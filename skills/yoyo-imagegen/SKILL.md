---
name: yoyo-imagegen
description: Generate real raster images — diagrams, explainer illustrations, mockups, posters — through yoyo imagegen. Use when a plan, README, or HTML report would land better with a visual, or when the user asks for an image, diagram, or mockup.
---

# Yoyo Imagegen

`yoyo imagegen` delegates to a headless `codex exec` run that renders through codex's built-in `image_gen` tool (`gpt-image-2`), copies the result to `--out`, and reports its size. It needs a signed-in codex CLI and **no `OPENAI_API_KEY`** — the tool renders on the ChatGPT subscription login. Expect 1–2 minutes.

Yoyo verifies the artifact deterministically: the file exists, changed, carries correct magic bytes for its extension, and has a plausible size. That rejects junk, a stale leftover, and a renamed SVG — but a matplotlib-drawn PNG would also pass. **Read the image yourself before embedding it.** Yoyo proves it is a real image; only you can tell whether it is the right one.

```bash
yoyo imagegen "IMAGE PROMPT" --out diagram.png --size 1536x1024 --quality high
yoyo imagegen "make the background white" --edit diagram.png --out diagram-v2.png
```

- `--out` is required: `.png`, `.jpg`, `.jpeg`, `.webp`, resolved against `--cwd`.
- `--size WIDTHxHEIGHT` — `1536x1024` for doc and plan images, `1024x1024` for icons, up to `3840x2160`.
- `--quality low` while iterating, `high` for the final.
- `--edit existing.png` keeps the layout stable across revisions.

One or two refine-and-regenerate passes are normal. A third means the prompt is wrong — rewrite it instead of rerolling.

## Writing a prompt that works

Structure every prompt as **subject, style, composition, palette, text policy**. This recipe is what separates a usable explainer from generic AI art.

1. **Subject first, concretely.** Name every element and its relationship: "three boxes connected left to right by arrows, labeled PLAN, BUILD, REVIEW" beats "a workflow diagram".
2. **Pick one named style.** "Flat vector", "hand-drawn black marker on whiteboard", "isometric 3D", "blueprint schematic, white lines on blue". An unstated style is where the generic look comes from.
3. **State the background.** "Plain white background" for document embeds, "near-black" for posters.
4. **Keep rendered text short and quoted.** Five or fewer labels, three words or fewer each, each quoted in the prompt. End with "no other text" or stray words appear.
5. **Constrain the palette.** "Monochrome with one red accent" reads better in a document than open color.

Worked example:

```bash
yoyo imagegen "Hand-drawn flowchart in black marker on a plain white background, sketch style. Four rounded boxes left to right labeled 'SPEC', 'BUILD', 'GATE', 'REVIEW', connected by bold arrows. A small loop arrow returns from 'REVIEW' to 'SPEC'. One red accent circling 'GATE'. No other text." --out flow.png --size 1536x1024 --quality high
```

## Embedding in a document

Generate the visual instead of describing a flow, lifecycle, or architecture in prose. Save it beside the document (`--out assets/<doc-name>-flow.png`), reference it relatively with alt text, draft at `--quality low`, and regenerate at `high` once the composition is right.

Images explain; keep the load-bearing facts in text. When generation fails, report the failure and continue without the image — drawing a substitute in code is the one thing this skill forbids.
