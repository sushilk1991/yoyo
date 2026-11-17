# Long runs and quota

Branch: `feat/long-runs-and-quota`. Six ordered items in
`docs/plans/2026-11-17-long-runs-and-quota.md`; one passing commit per item;
no push, tag, release or installation.

Done: item1 renewable default deadlines; item2 interrupted answers/native
per-run sessions/continuation, bounded Claude SIGINT before TERM/KILL.
Verified: item1 535 tests; item2 547 tests; compile silent0; SkillGuard9OK.
Both actual Yoyo vendor timeouts exited124 with answers and resumed to recall
a random word, without creating sessions.json. Native picker observations and
full gate outputs in verification report. No smaller behavior shipped.

Done: item3 quota reporting/classification, 13 regressions; native quota
passed against finalcode. Remote full560testsOK, SkillGuard9OK, compile0.
User requires ALL SUITES REMOTE. Additional boxcreation denied5/5; user
explicitly authorized starting another existingbox. Videoverify diskfull,
restoredsuspended. Masonry retainedbox resumed, /data2.2Gfree, Batonidle;
use explicit `boxrun --box masonry` remainingitems, preserve itsfiles.

Next: item4 fallback. Draft tests staged in ignored `.yoyo/long-runs/item3-tests.txt`
through item6; adapt before applying. Quota map: only native unmodified adapters;
Codex init+rateLimits only for quota, reuse models pipe; explicit allowance
beats utilization; Claude latest event/mtime, root documented fields fallback;
classify terminal nonzero errors only, preserve timeout/signal precedence.

Testing: boxrun on retained build, unset XDG_CONFIG_HOME for fixture homes.
All trials use isolated YOYO_STATE_DIR; never prune/retain real ledger.
Evidence: `.yoyo/long-runs/evidence/`. Report:
`docs/plans/2026-11-17-long-runs-and-quota-verification.md`.

Muse sandbox could not create boxrun locks; stopped its worker and descendants.
Native deadline worker completed items1-2. Task-owned native probe processes exited.
