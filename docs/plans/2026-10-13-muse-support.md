# Muse Support Implementation Plan

**Goal:** Add built-in Muse Code target and caller support while retaining AGY-only Google support and the existing uncommitted compatibility fixes.

**Architecture:** Use `muse exec` with a regular temporary prompt file, not argv or stdin. Workers skip interactive approval while retaining Muse's sandbox. Reviewers expose only `read_file` and `search`, with workspace writes and shell disabled. Reuse native authentication and yoyo's background runs, captures, and ledger; reject named sessions until native session mapping is implemented. Install caller skills in Muse's XDG config home and retain shared discovery.

**Tech Stack:** Existing stdlib Python 3.9+ executable, unittest, Markdown skills.

Work directly in this checkout. Preserve `.lavish/` and existing changes. No commit, push, authentication change, or intentional vendor upgrade. Muse's launcher updated from 1.4.6 to 1.4.7 during initial discovery; subsequent native probes set `MUSE_NO_AUTO_UPDATE=1`.

## 1. Native checks

- [x] Inspect `muse --help`, `muse exec --help`, version, native skills, and Meta's permissions/configuration/extending documentation.
- [x] Establish that missing positional input rejects stdin, `-` is literal, and `--prompt-file /dev/stdin` fails the regular-file requirement.
- [x] Confirm all five caller skills are already discoverable from the shared home.
- [x] Validate the `read_file`/`search` toolset and verify existing-login inference.

## 2. Red, then adapter

**Files:** `tests/test_yoyo.py`, `bin/yoyo`.

Add `MuseTests(CliTestCase)` for registration, native worker flags, read-only tool exclusion, model forwarding, session rejection, regular-file transport, Unicode/large prompts, temporary-file cleanup on success and failure, both doctor modes, native skill lookup, and default/XDG installation. Extend the existing expected built-in set to include Muse while keeping standalone Gemini rejected.

Run `python3 -m unittest tests.test_yoyo.MuseTests`; inspect expected failures before production edits. Add private helpers for Muse arguments and its skill root. Stage the rendered prompt in the existing per-call temporary directory, pass its path through `--prompt-file`, and feed empty stdin. Preserve custom command prefixes and shared `--model`/`--agent-arg` forwarding.

Run focused tests, compilation, and full discovery.

## 3. Caller guidance

**Files:** `README.md`, `docs/REFERENCE.md`, `skills/yoyo/SKILL.md`.

Document Muse's native authentication, regular-file input, sandboxed worker posture, read-only tool restrictions, model passthrough, one-shot sessions, native XDG skill home, and background/poll recipe. Preserve all skill guards. Apply `no-ai-slop` to edited prose.

## 4. Verification and installation

**Files:** `docs/compatibility/2026-10-13-muse.md`; installed CLI and caller skill copies.

Run `python3 -m py_compile bin/yoyo`, `python3 -m unittest discover -s tests`, `python3 -m unittest tests.test_yoyo.SkillGuardTests`, and `git diff --check`; all must exit 0.

Probe native Muse review/write behavior in isolated temporary workspaces. Test Muse as a caller discovering yoyo, launching a read-only child through background/wait, and saving a byte-identical answer. Verify artifacts independently against the ledger. Do not infer success from executable presence or exit zero alone.

Resync the local CLI and skills, compare installed bytes, run strict doctor, and verify native Muse discovery without foreign skill roots. Refresh the active Pi caller after this turn and verify automatically; no user restart step.

Move only newly generated `.know/` retrieval state outside the checkout. Record verified results and limits. Leave changes uncommitted.

## Execution state

- [x] Added thirteen Muse regressions; observed failures before production edits.
- [x] Implemented file transport, native permissions/model flags, and default/XDG skill roots.
- [x] Updated caller guidance; all 378 tests and all 8 skill guards pass.
- [x] Installed CLI and skills; strict doctor and both live Muse modes pass. File-writing worker independently verified.
- [x] Verified native caller discovery and real Codex child launch. Muse's outer sandbox blocks Codex initialization; the outside-parent control succeeds. Recorded the limit without changing policy in `docs/compatibility/2026-10-13-muse.md`.
- [x] Refreshed the active Pi caller with `/reload`; verified the real status line, loaded Muse/AGY-only guidance, matching installed/bundled copies, and strict doctor.
