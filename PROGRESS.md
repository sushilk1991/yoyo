# Long runs and quota

Branch: `feat/long-runs-and-quota`. Six ordered items in
`docs/plans/2026-11-17-long-runs-and-quota.md`; one commit per item;
no push, tag, release or installation.

Done: item1 soft renewal, extension metadata, quiet/ceiling diagnostics and
caller-budget propagation. 9 new regressions failed before code and pass.
Full remote gates: 535 tests OK; compile silent exit0; SkillGuard 9 OK.
Three existing skill-integrity fixtures isolate binary availability on Linux.

Native probes: both vendors interrupted mid sleep90 and recalled random word
after explicit-ID resume. Claude SIGINT emits a result; Codex does not. Codex
exec sessions absent from interactive list and present in exec-filtered list.
Claude safe-mode picker showed no conversations for probe, despite transcript
and working explicit-ID resume. See verification report.

Next: item2. Staged prospective tests in ignored `.yoyo/long-runs/item2-tests.txt`
through item6; adapt before applying. Item2 shared-state test selection needs
fix. Recovery map: auto sessions only args.command == ask (other synthesized
Namespaces omit it); attach session/answer before finishing journal. Codex JSON
affects watcher, which must retain legacy human-log support.

Testing: boxrun on retained build, unset XDG_CONFIG_HOME for fixture homes.
All trials use isolated YOYO_STATE_DIR; never prune/retain real ledger.
Evidence: `.yoyo/long-runs/evidence/`. Report:
`docs/plans/2026-11-17-long-runs-and-quota-verification.md`.

Muse sandbox could not create boxrun locks; stopped its worker and descendants.
Native deadline worker completed item1. No task-owned processes remain from Muse.
