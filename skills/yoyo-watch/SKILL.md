---
name: yoyo-watch
description: Understand any video (URL or local file) — summarize it, answer questions about it, diagnose a bug from a screen recording, break down someone's content. Runs the bundled watch pipeline (yt-dlp + ffmpeg + captions/Whisper) directly, or delegates the whole watch to a yoyo agent so frames never enter your context. Use when a task involves a video: YouTube/Loom/TikTok/X links, .mp4/.mov/.mkv/.webm files, screen recordings.
---

# Yoyo Watch

Video understanding for any agent. The runtime is the `watch` skill from [bradautomates/claude-video](https://github.com/bradautomates/claude-video), installed at `~/Code/claude-video/skills/watch` and symlinked into every agent host's skills dir (`~/.claude/skills/watch`, `~/.codex/skills/watch`, `~/.agents/skills/watch`, `~/.config/opencode/skills/watch`, `~/.pi/agent/skills/watch`). One script downloads the video, extracts deduplicated frames as JPEGs, and pulls a timestamped transcript (native captions first, Whisper API fallback). You decide what to do with the result — there is no fixed output format.

Two ways to use it. Pick by whose context should hold the frames.

## Mode 1 — Watch it yourself (you have vision, you want the detail)

Run the extractor, then Read the frames and transcript it prints:

```bash
python3 ~/Code/claude-video/skills/watch/scripts/watch.py "VIDEO_URL_OR_PATH" [flags]
```

The script prints frame paths with `t=MM:SS` markers and a timestamped transcript. Read each frame path with the Read tool (they render as images), combine with the transcript, answer grounded in what's actually on screen. Delete the printed work dir when done.

Useful flags:

- `--detail transcript|efficient|balanced|token-burner` — `transcript` is captions-only (no download, ~5s, 0 image tokens); `efficient` is fast keyframes (cap 50); `balanced` (default) is scene-aware (cap 100); `token-burner` is uncapped.
- `--start M:SS --end M:SS` — focus on a window; denser frames, far cheaper than a sparse full pass. Use whenever the question names a moment.
- `--timestamps T1,T2,…` — grab frames at exact moments (read the transcript first, then target what the presenter flags).
- `--max-frames N` / `--resolution 1024` — tighter token budget / readable on-screen text (slides, terminals, code).

For the full protocol (preflight, first-run setup, long-video strategy), read `~/Code/claude-video/skills/watch/SKILL.md`.

## Mode 2 — Delegate the watching (keep frames out of your context)

A 10-minute video is ~80 frames ≈ 16k image tokens plus transcript. When you only need conclusions — or you're mid-task and shouldn't burn context on frames — have a yoyo agent watch it and report:

```bash
yoyo ask claude --skill watch --timeout 420 "Watch this video and <your actual question>. Video: VIDEO_URL_OR_PATH"
```

- `--skill watch` injects the watch skill so the delegate knows the pipeline; claude and codex both have vision and can Read the frames.
- Write the question the way you'd brief a person: what to look for, what to report back, at what depth. The delegate controls its own detail mode; name one only if you have a reason (e.g. "use --detail transcript" for a pure what-was-said question).
- Fan out for independent readings of ambiguous footage: `yoyo ask codex,claude --skill watch --judge claude "..."`.
- Spot-check: if the answer is load-bearing, verify one claim yourself (Mode 1 with `--start/--end` around the claimed moment is cheap).

## Notes

- Captioned videos are fully free. No Whisper key is configured on this machine, so caption-less videos (most local files, TikToks) come back **frames-only** — say so instead of inventing audio. To enable transcription, add `GROQ_API_KEY` or `OPENAI_API_KEY` to `~/.config/watch/.env`.
- Requires `ffmpeg` and `yt-dlp` on PATH (installed via brew).
- Long videos: prefer `--detail transcript` first, then a focused frame pass on the sections that matter.
