---
name: yoyo-imagegen
description: Generate real raster images (diagrams, explainer illustrations, plan visuals, mockups, posters) through yoyo imagegen, which renders with GPT-image (gpt-image-2). Use when a document, plan, HTML report, or explanation would land better with a visual, or when the user asks for an image, diagram, illustration, or mockup.
---

# Yoyo Imagegen

`yoyo imagegen` generates a real model-rendered image with GPT-image. For the default agent (codex) it delegates to a headless `codex exec` run that uses the bundled imagegen skill's built-in `image_gen` tool (backed by `gpt-image-2`): codex renders the image, copies it to `--out`, and reports size and dimensions, typically in ~1–2 minutes. Yoyo verifies the artifact deterministically: the file must exist, have changed, carry correct magic bytes for its extension, and have a plausible size — which rejects non-image junk, a stale leftover, or a renamed SVG. It does not prove the bytes are a model render rather than a code-drawn PNG (a PIL- or matplotlib-saved PNG passes these checks), so always Read the result yourself before trusting it; the delegation prompt hard-forbids code-drawn output.

Requirements: none beyond a signed-in codex CLI — the built-in `image_gen` tool renders on the ChatGPT-subscription login, so **no `OPENAI_API_KEY` is needed**. (Codex's separate `image_gen.py` CLI fallback does need a key; yoyo's prompt explicitly forbids codex from using it.)

## Command

```bash
yoyo imagegen "IMAGE PROMPT" --out diagram.png --size 1536x1024 --quality high
yoyo imagegen "make the background white" --edit diagram.png --out diagram-v2.png
yoyo imagegen "..." --out hero.png --json   # machine-readable result with verified byte count
```

- `--out` is required; `.png`, `.jpg`, `.jpeg`, `.webp`. Relative paths resolve against `--cwd`.
- `--size WIDTHxHEIGHT`: `1536x1024` for wide doc/plan images, `1024x1024` for icons and square art, up to `3840x2160`.
- `--quality low` for fast drafts and iteration; `high` for finals.
- `--edit existing.png` switches to edit mode with that image as the reference.
- After generation, Read the image yourself before embedding it. Yoyo verifies it is a real image; only you can verify it is the right image. One or two refine-and-regenerate passes are normal; more means the prompt is wrong, so rewrite it instead of rerolling.

## Writing Image Prompts That Work

Structure every prompt as: **subject, style, composition, palette, text policy**. Vague prompts produce generic art; the recipe below is what separates a usable explainer from slop.

1. **Subject first, concretely.** Name every element that must appear and its relationship: "three boxes connected left to right by arrows, labeled PLAN, BUILD, REVIEW" beats "a workflow diagram".
2. **Pick one named style.** "Flat vector", "hand-drawn black marker on whiteboard", "isometric 3D", "handwritten note on paper with doodles", "blueprint schematic, white lines on blue". Unstated style is where generic AI-art looks come from.
3. **State the background.** "Plain white background" for document embeds, "near-black" for posters. Never leave it to chance.
4. **Keep rendered text minimal and quoted.** GPT-image renders short labels well and paragraphs badly. Five or fewer labels, each three words or fewer, each in quotes in the prompt. End with "no other text" or stray words will appear.
5. **Constrain the palette.** "Monochrome with one red accent" reads better in documents than unconstrained color.

## Recipes

Flow diagram for a plan or README:

```bash
yoyo imagegen "Hand-drawn flowchart in black marker on a plain white background, sketch style. Four rounded boxes left to right labeled 'SPEC', 'BUILD', 'GATE', 'REVIEW', connected by bold arrows. A small loop arrow returns from 'REVIEW' to 'SPEC'. One red accent circling 'GATE'. No other text." --out flow.png --size 1536x1024 --quality high
```

Whimsical explainer (handwritten-note style, good for making a hard concept friendly):

```bash
yoyo imagegen "A whimsical handwritten study note on slightly yellowed paper, doodle style. A cheerful stick-figure robot hands a heavy box labeled 'TYPING' to a second robot, keeping a small glowing box labeled 'THINKING'. Hand-drawn arrows, a few star doodles, warm pencil colors. No other text." --out explainer.png --quality high
```

Architecture sketch:

```bash
yoyo imagegen "Isometric technical illustration, flat vector, plain white background. A small CLI box labeled 'YOYO' in the center routing lines to three terminal windows labeled 'CODEX', 'CLAUDE', 'PI'. Thin gray lines, one blue accent. No other text." --out arch.png --size 1536x1024
```

## Embedding in Plans and HTML Reports

When writing a plan, report, or HTML document that explains a flow, lifecycle, architecture, or before/after, generate the visual instead of describing it in prose:

1. Save next to the document: `--out assets/<doc-name>-flow.png`.
2. Reference it relatively (`![build flow](assets/plan-flow.png)` or an `<img>` tag with alt text).
3. Draft at `--quality low`, regenerate the final at `high` once the composition is right.
4. Use `--edit` for revisions so the layout stays stable between versions.

## Guardrails

- Never draw images with code as a fallback; if generation fails, report it and continue without the image.
- Images explain; they do not carry load-bearing facts. Keep the authoritative details in text.
- This skill ships with yoyo but installs only when codex is present, since codex bundles the imagegen skill with the built-in `image_gen` tool it drives. If generation fails, surface the error and continue without the image. `--agent` can target another configured agent that has its own real image-generation capability.
