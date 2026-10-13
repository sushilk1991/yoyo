# CLI Compatibility Implementation Plan

**Goal:** Make current Antigravity, Cursor, Claude Code, and Codex usable through yoyo with discoverable caller instructions and useful failure diagnostics. User's final scope: keep `agy` as the only built-in Google target.

**Architecture:** Keep `agy` in the existing adapter registry; remove standalone Gemini support. Reuse recorded bundle resolution for installation, preserve existing skill precedence, and add native search locations after existing user roots. Doctor accepts shared-root discovery, reports completely missing skills, and falls back to stdout for errors. Claude read-only excludes all MCP tools.

**Tech Stack:** One stdlib Python 3.9+ executable, unittest, Markdown skills.

---

Approved scope: `docs/compatibility/2026-10-12.md`. Execute directly in this checkout; leave `.lavish/` untouched. Do not commit, push, change authentication, or upgrade global vendor CLIs.

## 1. Regression tests

**Files:** `tests/test_yoyo.py`.

Add `CliCompatibilityTests(CliTestCase)` covering the unchanged six-agent set, rejection of standalone Gemini, and exclusion of its native skill home. Cover Cursor and Antigravity native roots, shared-root precedence, Antigravity's installation target, recorded-source skill installation, explicit source override failure, missing-skill diagnosis, skipped dependency skills, stdout failure fallback, and Claude MCP exclusion. Add guards requiring AGY recipes and excluding standalone Gemini commands.

Run `python3 -m unittest tests.test_yoyo.CliCompatibilityTests`. Record failures before changing production code or the skill.

## 2. Native adapter and permissions

**Files:** `bin/yoyo`, `tests/test_yoyo.py`.

Remove standalone Gemini's adapter, approval constants, command helper, and model/read-only handling. Preserve Antigravity's native prompt, permissions, timeout, and model behavior; leave other agents unchanged.

Use named constants for Claude read tools and the `mcp__*` deny rule; append the deny rule only for read-only calls.

Run the focused tests, then `python3 -m unittest discover -s tests`.

## 3. Skill resolution and doctor

**Files:** `bin/yoyo`, `tests/test_yoyo.py`.

Search `~/.cursor/skills` and `~/.gemini/antigravity-cli/skills` without changing existing user-root precedence. Install bundled caller skills into Antigravity CLI's documented global home, retaining its legacy home and shared roots.

Make `install-skill` reuse `bundled_skills_source()` for an installed executable, honoring explicit `YOYO_SKILL_SOURCE`. Test a copied-binary installation with a recorded checkout and an isolated HOME. Doctor reports a skill missing only when no supported installed home has it; skip dependency-gated skills when their CLI is absent. On a failed live probe, use bounded stderr or, when empty, bounded stdout.

Run `python3 -m unittest tests.test_yoyo.CliCompatibilityTests` and the full suite.

## 4. Caller instructions

**Files:** `skills/yoyo/SKILL.md`, `docs/REFERENCE.md`, `README.md`.

Use Antigravity (`agy`) for Google delegation through its existing login. Remove standalone Gemini's target row, setup instructions, and launch recipes. Teach discovery, worker versus read-only access, `--background`/`wait` polling, and skill resync. Document native shared-root coverage and new Antigravity location. Preserve every `SkillGuardTests.LOAD_BEARING_ANCHORS` string. Keep documented model selection contextual; add no hard-coded new model catalog.

Run `python3 -m unittest tests.test_yoyo.SkillGuardTests`. Apply the `no-ai-slop` final pass to edited prose.

## 5. Verification and local installation

**Files:** `docs/compatibility/2026-10-12.md`; installed CLI and bundled caller skill copies.

Run `python3 -m py_compile bin/yoyo` and `python3 -m unittest discover -s tests`; both must exit 0. Run the separate skill guard suite. Exercise a temporary-prefix installation and installed-binary `install-skill` repair before resyncing the user's installation.

Retain the recorded vendor and nested-delegation checks. After removing standalone Gemini, smoke-test installed AGY through its existing login with `GEMINI_API_KEY` absent; verify the saved answer and ledger. Claude inference remains blocked if the weekly limit persists; verify native command construction and report that limit explicitly.

Install only this checkout's CLI and skills with `./install.sh`, then run installed `yoyo install-skill` and non-live `yoyo doctor --strict`. Refresh the audit note with post-fix evidence. Leave all changes uncommitted.

## Execution state

- [x] Observed compatibility failures before production fixes and removal failures before removing Gemini. Final suite retains 18 compatibility tests.
- [x] Removed standalone Gemini support at the user's request; preserved AGY and Claude MCP restrictions.
- [x] Repaired recorded-source installation, native paths, existing overrides, and doctor diagnostics.
- [x] Updated caller guidance for AGY-only Google delegation; all 8 skill guards pass.
- [x] Passed all 365 tests, compilation, local resync, and strict doctor. Keyless installed AGY run `20261012T183348-68829545` returned `AGY_ONLY_OK`; saved answer and ledger match. Claude's quota remains an unrelated verification limit. No commit, push, authentication change, or global vendor upgrade.
