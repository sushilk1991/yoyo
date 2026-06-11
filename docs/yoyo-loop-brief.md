# Brief: `yoyo loop` — fresh-context iteration runner

**Status:** spec, ready to build
**Repo:** `~/Code/yoyo` (single-file CLI at `bin/yoyo`, ~2,855 lines; tests in `tests/test_yoyo.py`)
**Reference implementation to port and then delete:** `~/Developer/claude-tools/cloop` (standalone, working, tested)

---

## 1. Why (context)

A 2026-06-11 audit of 24h of Claude Code usage found ~$3,000/day API-equivalent burn. Root cause: long-lived agentic sessions (the `/loop` + ScheduleWakeup pattern) grow one session's context monotonically (350k–780k tokens) and re-read it on **every** tool call. ~70% of spend was cache reads, ~20% cache re-writes from the 5-minute prompt-cache TTL expiring during loop sleeps. One overnight 15-hour session (1,585 calls, avg 353k context) cost ~$798. Output tokens were only ~15% of spend.

The fix is structural: **each loop iteration should be a brand-new agent session with empty context**, with continuity carried in a small state file the agent reads and rewrites. Per-iteration cost then stays flat (~$0.10–0.30 baseline) instead of growing without bound.

A standalone prototype (`cloop`) proves the pattern works: `claude -p --output-format json` per iteration + a state-file protocol + stop conditions. We're folding it into yoyo because yoyo already owns "invoke an agent non-interactively as a subprocess" with the right plumbing — agent adapters (`claude` / `codex` / `pi`), `--cwd`, `--role`, `--skill` injection, idle timeouts, byte caps, the run ledger (`yoyo runs`), `--background`/`yoyo wait`. `cloop` duplicates a worse subset of that for one agent.

## 2. Goal

Add a `yoyo loop` subcommand: run the same task as a sequence of **independent fresh-context `ask`-style invocations** against one agent, with a state-file protocol for continuity, robust stop conditions, per-iteration cost reporting (where the agent supports it), and run-ledger integration.

## 3. Non-goals

- No multi-agent loops (one agent per loop; `yoyo workflow` already covers multi-agent orchestration).
- No transcript analysis / cost forensics (`ctok` stays standalone in `~/Developer/claude-tools`).
- No interactive mode (`yoyo chat` exists).
- No `--session` reuse inside a loop — fresh context per iteration is the whole point. Reject the combination loudly if both are passed.

## 4. CLI surface

```
yoyo loop <agent> [task] [options]
yoyo loop claude --role worker --cwd "$PWD" \
  --state .yoyo/loop-state.md --max-iter 30 --interval 60 \
  --budget-usd 20 --skill frontend-design \
  "Fix the failing tests, one per iteration."
```

| Flag | Default | Meaning |
|---|---|---|
| `task` (positional) or `--input FILE` | required | The overall goal. `--input` mirrors `ask`'s file-input convention. |
| `--state PATH` | `.yoyo/loop-state.md` (relative to `--cwd`) | State file. Created with a seed template if absent. |
| `--max-iter N` | `20` | Hard iteration cap. |
| `--interval SECONDS` | `0` | Sleep between iterations. |
| `--budget-usd X` | none | Stop when cumulative reported cost ≥ X. Only enforceable for agents that report cost (see §7); warn once at start if the agent doesn't. |
| `--max-fail N` | `3` | Stop after N **consecutive** failed iterations (non-zero exit / timeout / expect-failure). |
| `--role`, `--cwd`, `--skill`, `--caller`, `--timeout`, `--read-only`, byte-cap flags | same as `ask` | Pass through to each iteration unchanged. |
| `--json` | off | Emit a final JSON summary instead of the human table. |
| `--dry-run` | off | Print the composed iteration-1 prompt and resolved command, run nothing. |

Stop conditions (any one ends the loop, in priority order):
1. `STOP` file exists next to the state file (checked before each iteration).
2. State file contains a line `STATUS: DONE` (checked before each iteration, i.e. after the previous iteration wrote it).
3. `--max-iter` reached.
4. `--budget-usd` exceeded (checked after each iteration).
5. `--max-fail` consecutive failures.

Exit codes: `0` on DONE/STOP/max-iter/budget (normal endings), `1` on max-fail, `2` on usage errors — match existing yoyo conventions.

## 5. The loop protocol (prompt wrapper)

Each iteration's prompt = protocol header + the user task, composed via the existing `build_prompt()` path so `--skill` / `--caller` / `--role` blocks compose exactly as they do for `ask`. Port this header from `cloop` (adjust wording freely, keep the contract):

```
=== LOOP PROTOCOL (yoyo loop iteration {i}/{max_iter}) ===
You are one iteration of a fresh-context loop. You have NO memory of prior
iterations; the state file is your only continuity.

State file: {abs_state_path}
1. Read the state file FIRST. It records the goal, done work, and next step.
2. Do ONE meaningful increment of work this iteration. Keep this session small.
3. Before ending, REWRITE the state file: goal, what is done (terse list),
   exactly what the next iteration should do first, and any gotchas learned.
4. When the overall goal is fully complete and verified, write a line
   containing exactly "STATUS: DONE" in the state file.
=== END LOOP PROTOCOL ===

TASK:
{task}
```

Seed state file when absent:

```markdown
# yoyo loop state

GOAL:
{task}

DONE:
(nothing yet)

NEXT:
Start from scratch.
```

## 6. Execution model

- Each iteration goes through the same machinery as `ask` (`execute_agent_call`), so idle-timeout, output caps, signal handling, and full-access/read-only resolution are inherited, not reimplemented.
- **Ledger:** record each iteration as a run (reuse the existing run-record functions around `runs_root()` / `run_summary()`). Generate one loop id (reuse `--trace-id` / `generate_run_id()`), and stamp every iteration's run meta with `loop_id` and `iteration`. `yoyo runs list` should make loop iterations identifiable (e.g. a `loop` column or `loop_id:iter` in the label) — follow whatever is cheapest given the current `run_summary()` shape.
- **Per-iteration console line** (human mode):
  `[HH:MM:SS] iter   3  ok    142s   $0.21  (total $1.34)  out=8,412 cache_r=121k cache_w=43k  <last line of agent output, truncated ~120 chars>`
  Omit token/cost fields for agents that don't report them.
- **Final summary:** iterations run, end reason (`done|stop|max-iter|budget|max-fail`), total cost (if known), state file path, loop_id. `--json` emits the same as JSON.

## 7. Cost accounting (per-flavor)

This is the one adapter change. For the `claude` flavor, loop iterations should append `--output-format json` to the agent command. The CLI then prints a single JSON envelope on stdout containing `result` (the agent's text), `total_cost_usd`, and `usage` (`output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`).

- Parse the envelope; use `result` as the iteration's logical stdout (for the ledger, byte caps display, and the tail line) and the cost/usage fields for reporting + `--budget-usd`.
- If stdout isn't valid JSON (crash, truncation), treat the raw text as output, cost unknown, and count the iteration by exit code as usual. Never crash the loop on a parse failure.
- `codex` / `pi` flavors: no cost extraction initially. `--budget-usd` with such an agent → warn once ("agent does not report cost; budget will not be enforced") and continue. Structure it as a per-flavor `extract_usage(stdout) -> (text, cost, usage)|None` hook so codex JSONL support can be added later.
- **Verify during implementation:** `claude -p --no-session-persistence --output-format json` work together (they should — cost comes from the API response, not the transcript). If `--no-session-persistence` ever conflicts, prefer keeping `--output-format json` and document the trade.

## 8. Background composability

`yoyo loop ... --background` should work like `ask --background`: detach via the existing `start_background_run()` / `background_child_argv()` path, return a run id, and let `yoyo wait` block on the **loop** (the parent run). Iterations inside still get their own ledger entries tagged with `loop_id`. If wiring the parent/child run relationship is awkward in the current ledger shape, it's acceptable to ship v1 with `--background` on the loop only (no per-iteration child records) — note the choice in the README.

## 9. Safety / permissions

Inherit `ask`'s model exactly: full-access by default (that's yoyo's documented default for agent-to-agent work), `--read-only` available. No new permission flags. A read-only loop can't write the state file with the same agent — detect `--read-only` + default state path and warn that the agent must be able to write the state file (or the user maintains it manually).

## 10. Tests (match `tests/test_yoyo.py` style — it stubs agent commands with fake scripts)

1. Loop runs N iterations against a stub agent and stops at `--max-iter`.
2. Stub writes `STATUS: DONE` into the state file on iteration 2 → loop exits after 2, end reason `done`, exit 0.
3. `STOP` file present before iteration 1 → zero iterations, exit 0.
4. State file seeded when absent; not clobbered when present.
5. Claude-flavor JSON envelope: stub emits a fake envelope → cost accumulates; `--budget-usd` crossing stops the loop with end reason `budget`.
6. Malformed JSON from claude-flavor stub → iteration still counted, cost unknown, loop continues.
7. `--max-fail`: stub exits non-zero 3× → loop aborts, exit 1; a success between failures resets the counter.
8. Prompt composition: iteration prompt contains protocol header, abs state path, and `--skill` block (reuse existing skill-injection test fixtures).
9. `--session` + `loop` → hard usage error.
10. Ledger: iteration runs appear in `yoyo runs list` with the shared `loop_id`.
11. `--dry-run` prints command + prompt, executes nothing.

## 11. Docs

- README: new `loop` section next to `ask` (mirror the existing tone — short, example-first). State explicitly *why* fresh-context loops beat one long session (one paragraph, can lift from §1).
- `yoyo install-skill` bundled SKILL.md: add a short "Loops" section so calling agents know to reach for `yoyo loop` instead of building their own iteration logic.

## 12. Acceptance criteria

- [ ] `yoyo loop claude "write 'hello' into hello.txt"` in an empty dir completes in 1–2 iterations, prints per-iteration cost, exits 0 with end reason `done`.
- [ ] Each iteration is verifiably a fresh session (no conversational carryover; state file is the only continuity).
- [ ] All tests in §10 pass; existing test suite stays green.
- [ ] `yoyo runs list` shows loop iterations with a shared loop id.
- [ ] `yoyo loop --help` documents every flag.

## 13. Follow-ups after landing (not part of this build)

- Delete `~/Developer/claude-tools/cloop` and its `~/.local/bin/cloop` symlink.
- Update `~/.claude/CLAUDE.md` → "Token Discipline" section: replace `cloop` references with `yoyo loop` (keep `ctok` as-is).
