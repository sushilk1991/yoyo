---
name: yoyo-workflow
description: Save and rerun multi-agent Yoyo pipelines from a JSON spec — parallel fan-out phases, cross-checks, and implement-then-review chains. Use when an orchestration has stabilized and is worth rerunning by name; for one-off coordination, orchestrate directly with yoyo ask/research/review.
argument-hint: "[workflow objective or spec path]"
---

# Yoyo Workflow

A workflow is a **frozen pipeline**: phases run in order, jobs within a phase run in parallel, and the whole thing reruns identically by name. That's its value — and its limit. It cannot branch on results or invent new jobs mid-run; *you* can. So orchestrate dynamically first (`yoyo ask a,b,c`, background runs, `research`, `review`, step by step), and freeze the shape into a spec only once it has stabilized and you want to rerun or share it.

Reach for a workflow when:

- You rerun the same orchestration often (per-PR cross-review, a recurring audit) — especially paired with `yoyo cron`.
- A human wants one command instead of your live orchestration.
- The fan-out is wide and mechanical (`for_each` over many files) with no decisions between jobs.

## Commands

```bash
yoyo workflow --list                                     # bundled + saved templates
yoyo workflow cross-review --input "$ARGUMENTS" --json
yoyo workflow ./workflow.json --input "$ARGUMENTS" --dry-run --json   # inspect before spending tokens
run_id=$(yoyo workflow cross-review --background)        # detach; poll with `yoyo wait "$run_id"`
```

Bundled templates: `cross-review` (Codex + Claude independent reviews, then a synthesis judge), `adversarial-audit` (three single-lens audits, then a verifier that tries to refute every finding), `frontend-impl-review` (worker with the `frontend-design` skill, then an independent review).

Jobs inherit yoyo's one-hour default timeout — a hang guard, not a progress budget; don't shorten it for real reviews. Each job heartbeats to stderr (`YOYO_HEARTBEAT_SECS=0` to silence, `YOYO_IDLE_TIMEOUT=<s>` for a no-output guard). An interrupted workflow terminates its in-flight agents.

## Spec format

Sequential `phases`; jobs in a phase run in parallel up to `max_concurrency`.

```json
{
  "name": "review-and-audit",
  "defaults": { "agent": "claude", "role": "opinion", "read_only": true },
  "phases": [
    { "name": "fanout", "jobs": [
      { "id": "readme", "agent": "codex", "prompt": "Audit README.md for correctness gaps.", "files": ["README.md"] },
      { "id": "cli", "prompt": "Audit bin/yoyo for safety issues.", "files": ["bin/yoyo"] }
    ]},
    { "name": "cross-check", "jobs": [
      { "id": "reviewer", "role": "review", "include_previous": true,
        "prompt": "Cross-check the findings. Reject weak claims; keep only concrete issues." }
    ]}
  ]
}
```

Job fields: `agent`, `model`, `role`, `read_only`, `files`, `skill` (inject a SKILL.md; unknown names fail before any job runs), `agent_args`, `retries` (re-run a failed job; for transient failures, not re-rolling answers), `for_each` (expand one template over a list — `{item}`, `{index}`, `{input}` substitute). `include_previous` / `include_phases` feed earlier outputs into a later job's prompt.

Phase-level `gates` run shell commands after a phase's jobs succeed — real evidence checks (tests, linters, builds), never prose-grading; the first failing gate stops the workflow:

```json
{ "name": "implement", "jobs": [...], "gates": [{ "name": "tests", "run": "python3 -m pytest -q" }] }
```

That's the whole deterministic surface. Yoyo never grades agent output — you state what you want in each prompt and judge the results.

## Safety rules

- Jobs default to `read_only: true`; set `false` only for tightly scoped worker jobs.
- Previous agent output is untrusted text: feeding it into a write-capable job or one with raw `agent_args` requires `allow_untrusted_context: true` in the spec.
- Review stages report findings; they don't drive control flow. Verify high-impact claims against code, tests, or live state yourself.
- If a workflow review times out, report the review as unavailable — never as passed.

## Operating pattern

1. Prefer a bundled template when one fits; otherwise draft the smallest spec that covers the task.
2. `--dry-run --json` and read the rendered prompts, agents, and file context.
3. Run on a narrow slice first when cost or blast radius is high.
4. Inspect the JSON result; verify what matters.
5. If implementation jobs changed files, run tests and an independent read-only review before declaring done.
