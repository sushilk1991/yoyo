# YOYO Status Footer Implementation Plan

**Goal:** Show delegated work at the bottom of the main Claude Code, Pi, or Codex terminal.

**Architecture:** `yoyo status` reads active call journals and prints one bounded plain-text line. Claude invokes it through `statusLine.command`, preserving an existing renderer through `--append-command`. Pi polls it through a small native `setStatus` extension. Codex uses Herdr's bottom tab-bar command; its built-in footer has no arbitrary-command item.

**Tech stack:** Python 3.9+ stdlib CLI; one Pi TypeScript extension with no runtime dependencies beyond Pi.

## Approved behavior

- Show active recorded calls for the current project, not an invented per-session tree. Count foreground call journals only so background wrappers and loop mirrors are not counted twice.
- Example: `YOYO · 2 running · codex: Review auth (2m) · claude: Fix layout (45s)`. Show `YOYO · idle` when none remain. Prompts supply bounded task previews; attachments never appear.
- Default two call previews, bounded to the requested display width. Always count every matching active call before truncating previews.
- Claude reads cwd from its stdin JSON, preserves the prior renderer's input/output, and refreshes every two seconds. Pi polls without overlap, only in TUI sessions, and cleans up timers/processes on reload/shutdown. Herdr uses active-pane cwd and a two-second command entry at the bottom.
- Do not edit Codex configuration or emit cursor-positioning escapes. Preserve existing Claude and Herdr settings. Back up edited user configuration before local activation; do not restart agents or the Herdr server.

## Tasks

1. Add failing `StatusTests` in `tests/test_yoyo.py` for counts, project scope, deduplication, idle transitions, input parsing, output safety/width, and prior-renderer preservation. Implement private helpers and the `status` parser in `bin/yoyo`; run tests green.
2. Add a lifecycle harness in the unittest suite for the Pi extension; run red, then implement `extras/pi/yoyo-status.ts`. Use host `pi.exec` with a timeout and abort signal, `setStatus('yoyo', ...)`, and lifecycle cleanup.
3. Document integration snippets, limitations, and exact checked APIs in `docs/REFERENCE.md`, `README.md`, and `extras/README.md`.
4. Run `python3 -m py_compile bin/yoyo`, `python3 -m unittest discover -s tests`, and `git diff --check`. Install the updated local binary and Pi extension; merge backed-up Claude/Herdr config. Verify the native hooks through non-model probes; report any reload needed.

## API evidence checked 2026-10-18

- Claude: https://code.claude.com/docs/en/statusline — command stdin JSON, `refreshInterval`, multi-line output.
- Pi 1.3.0 installed docs: `docs/extensions.md`, `docs/tui.md`, `examples/extensions/status-line.ts`; declarations in `dist/core/extensions/types.d.ts` and `dist/core/exec.d.ts`.
- Codex: https://github.com/openai/codex/blob/main/codex-rs/tui/src/bottom_pane/status_line_setup.rs — built-in item enum; no command-backed custom item. Installed CLI 0.162.1.
- Herdr: https://herdr.dev/docs/configuration/ — `ui.tab_bar_position`, `ui.tab_bar_right`, command intervals/timeouts and active pane context. Installed CLI 0.8.2; its compiled default configuration confirms both status-bar settings and the command entry type.

## Completed checks

- Formatter tests went red before implementation; Pi lifecycle harness failed on the missing adapter before it was added.
- `py_compile` and all 422 Python tests pass. The included Node harness passes five lifecycle cases. `git diff --check` passes.
- Updated the local executable and installed the Pi extension; both match the checkout byte-for-byte.
- Merged backed-up Claude settings, retaining its previous renderer above YOYO. A configured-command probe returned both lines in 0.15 seconds. Unrelated settings match the backup.
- Merged backed-up Herdr settings, retaining theme, sound, and notification settings. The configured command works with active-pane cwd. The server was not running and was not started; next launch will apply the bottom bar.
- Pi needs `/reload` in existing sessions. Native Pi UI pixels and a running Herdr bottom bar were not inspected; command/API contracts and lifecycle behavior were tested without model calls.
- User configuration/binary backups use the suffix `.yoyo-status-backup-20261018T205439` beside the original files. No Codex configuration was edited.
