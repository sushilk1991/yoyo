# Long runs and quota

Release: `v0.35.0`. All six ordered plan items were completed on
`feat/long-runs-and-quota`, one passing commit each. VERSION changed once in
item6. The user authorized commit, push and release on2026-11-18, superseding
the original no-publishing scope. Local installation is outside this release.

Done:
- Item1 `3b221dd`: renewable default deadlines; remote535 tests OK.
- Item2 `370441b`: interrupted answers and continuation; remote547 OK.
- Item3 `9b3fec4`: quota visibility and terminal limit classification; remote560 OK.
- Item4 `35be36e`: explicit fallback/aliases and doctor catalogs; remote580 OK.
- Item5 `81d4a3d`: reported tokens and audit percentiles; remote589 OK.
- Item6 (final branch commit): daily raw-stream retention; remote601 OK.
- Every item passed compilation and SkillGuard9. Final retention/version13 OK.
- Native Codex and Claude deadline/resume trials retained answers and recalled
  random words without sessions.json. Live sanitized quota passed. Native token
  totals matched envelope+journal: Codex36,628 and Claude17,503.
- Independent review of items5–6 found no unresolved material defects.

Decisions/limits:
- Explicit timeouts stay hard; implicit active deadlines renew to a12h ceiling.
- Quota routing and model choices are caller-controlled. No dollar figures.
- Usage is provider-reported: resumed Codex can include prior thread totals;
  Claude final usage excludes subagents. Unreported model/usage stays unknown.
- Retention removes only old regular raw streams, at most daily under a
  nonblocking lock; active/ambiguous/symlink states and all history stay.
- Every test/trial used isolated YOYO_STATE_DIR. Real ledger never pruned.

Verification placement:
User explicitly required all suites REMOTE and authorized another machine.
Items1–3 used retained build; items4–6 used retained masonry via boxrun and
remote Baton. No local suite ran. Native authenticated probes ran on Mac only;
credentials were not copied. Video-verify failed disk preflight and was restored
suspended; no new VM was created at the account limit. All owned jobs completed,
no detached copies/containers/ports exist, and retained machines/caches remain.

Evidence: ignored `.yoyo/long-runs/evidence/`; committed report:
`docs/plans/2026-11-17-long-runs-and-quota-verification.md`.
Next: no required implementation or verification work remains. No publishing.
Python3.9-specific pathlib API regression passed under3.13; an actual3.9
interpreter run was not performed. Test/source/skill SHA256 matched remote.

Follow-up complete: user requested committing and testing once with Muse.
Six implementation commits were already clean through4f256c9. Fresh Masonry
verification: compile0, full601 in57.290s, SkillGuard9, version1, help0;
CLI/test/skill hashes match. Muse read-only review20261117T165926-4d6d0785
exited0 with no material findings after reading code and completed evidence.
Parent ran remote commands because Muse's sandbox cannot create boxrun's
outside-workspace lock; no sandbox bypass. All review points inspected.
Raw --agent-arg flags are not replayed in continuation commands, and custom
profile continuation remains unverified; no speculative flag replay patch.
Evidence `.yoyo/muse-final/evidence`; full output and boundaries recorded in
verification report. Program/test/skill unchanged. Remote Baton idle; no
owned jobs/copies remain. Next: no outstanding task work. No publishing.


Release preparation (2026-11-18): fetched origin/main and tags; origin/main
remains v0.34.0 and the feature branch fast-forwards it. Current CLI/test/skill
SHA256 values exactly match the601-test Muse-reviewed remote gate inputs.
Prepared user-facing0.35.0 notes in `.yoyo/release-0.35.0/notes.md`. Integrate by
fast-forward to main, create an annotated tag, push main+tag atomically, then
publish GitHub latest release. Read back remote main/tag, release state and
published CLI bytes. No code or skill changes after the verified inputs.
