# Long runs and quota

Branch: `feat/long-runs-and-quota`. Follow the six ordered items in
`docs/plans/2026-11-17-long-runs-and-quota.md`; one passing commit per item.
No push, tag, release or installation. Bump VERSION only in item6.

Done:
- Item1: `3b221dd`, renewable default deadlines; full535 tests OK.
- Item2: `370441b`, interrupted answers and native continuation; full547 OK.
- Item3: `9b3fec4`, sanitized quota and terminal usage-limit outcomes; full560 OK.
- Every commit passed compile (silent0) and SkillGuard9.
- Real Codex and Claude Yoyo deadline/resume trials retained answers and recalled
  random words without sessions.json. Final native quota command passed.

Done: item4 fallback/aliases and doctor catalogs; remote580 tests, compile0,
SkillGuard9 all passed.

Done: item5 token recording and audit percentiles; remote589 tests,
compile0, SkillGuard9 all passed. Native Codex36,628 and Claude17,503 totals
match both JSON envelope and journal. Latest provider snapshots are used;
resumed Codex can include earlier thread usage, Claude excludes subagents.

Next: item6 automatic raw-stream retention. Parent owns report, plan and
commit; implementation ownership will be assigned after item5 is committed.
Bump VERSION to0.35.0 only in item6 with its expected-version test.

All suites stay REMOTE, explicitly confirmed by user. Use:
`boxrun --box masonry --lane build --mem 2G -- 'env -u XDG_CONFIG_HOME ...'`.
User authorized starting another existing machine after build contention and
new-box quota5/5. Video-verify had only733MB free; restored suspended. Retained
masonry resumed: Python3.13.5, idle Baton, /data2.2GiB free. Separate owned
YoYo checkout `/data/workspace/yoyo-2c067b94`; preserve all Masonry data/services.
Build box remains retained. No local suite ran. Native authenticated vendor
probes run on Mac only; credentials never copied to remote boxes.

Every test/trial has isolated YOYO_STATE_DIR. Never prune/retain real ledger.
Evidence `.yoyo/long-runs/evidence/`; report:
`docs/plans/2026-11-17-long-runs-and-quota-verification.md`.

Muse sandbox could not create boxrun locks; its worker/descendants were stopped.
Native worker completed items1-3. No task-owned native probe process remains.
