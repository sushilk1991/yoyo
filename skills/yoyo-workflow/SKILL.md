---
name: yoyo-workflow
description: Run dynamic multi-agent Yoyo workflows for fan-out work, independent audits, cross-checking, and reusable agent orchestration. Use when the user asks for a workflow, multi-agent implementation/review, or Claude Code-style dynamic workflows.
argument-hint: "[workflow objective or spec path]"
---

# Yoyo Workflow

Use `yoyo workflow` when a task benefits from a repeatable orchestration script: fan-out research, parallel audits, migration sweeps, implementation followed by independent review, or several agents using different models.

Success criterion before running: define what final evidence proves the workflow succeeded, such as tests passing, a clean review, or a cited report.

## Command

Run a saved workflow spec:

```bash
yoyo workflow ./workflow.json --input "$ARGUMENTS" --json
```

Dry-run before spending model calls:

```bash
yoyo workflow ./workflow.json --input "$ARGUMENTS" --dry-run --json
```

Workflow jobs inherit Yoyo's one-hour default timeout unless the spec, `--timeout`, or `YOYO_TIMEOUT` overrides it.

The timeout is a hung-process guard, not a progress budget. Do not shorten it for real workflow reviews or audits. Use short caps only for deterministic smoke tests with fake or trivial agents.

## Spec Format

Create a JSON file with sequential `phases`. Jobs in one phase run in parallel up to `max_concurrency`; the next phase starts after the current phase finishes.

```json
{
  "name": "review-and-audit",
  "max_concurrency": 4,
  "defaults": {
    "agent": "claude",
    "role": "opinion",
    "read_only": true,
    "model": "haiku"
  },
  "phases": [
    {
      "name": "fanout",
      "jobs": [
        {
          "id": "readme",
          "agent": "codex",
          "model": "gpt-5",
          "prompt": "Audit README.md for correctness gaps.",
          "files": ["README.md"]
        },
        {
          "id": "cli",
          "prompt": "Audit bin/yoyo for correctness and safety issues.",
          "files": ["bin/yoyo"]
        }
      ]
    },
    {
      "name": "cross-check",
      "jobs": [
        {
          "id": "reviewer",
          "role": "review",
          "include_previous": true,
          "prompt": "Cross-check the previous findings. Reject weak claims and list only concrete issues."
        }
      ]
    }
  ]
}
```

Use `for_each` to fan out one job template:

```json
{
  "id": "audit-{index}",
  "for_each": ["README.md", "bin/yoyo", "tests/test_yoyo.py"],
  "prompt": "Audit {item}.",
  "files": ["{item}"]
}
```

## Safety Rules

- Workflow jobs default to `read_only: true`.
- `include_previous` and `include_phases` feed prior agent output into the next prompt. That is useful for auditing, but prior output is untrusted text.
- Yoyo blocks previous-output injection into a write-capable job or a job with raw `agent_args` unless the spec sets `allow_untrusted_context: true`.
- Review stages should report findings, not drive automatic control flow. The supervising agent must verify claims with code, tests, docs, or live state.
- Keep `max_concurrency` and `max_jobs` explicit for expensive workflows.
- Do not use short workflow timeouts for real agent review; short caps are for smoke tests only. If a workflow review times out, report that the review was unavailable.

## Agent Selection

Each job can set:

- `agent`: `codex`, `claude`, `pi`, or a configured custom agent.
- `model`: model name passed through to agents that support `--model`.
- `role`: `opinion`, `review`, or `worker`.
- `read_only`: use `false` only for scoped implementation jobs.
- `files`: string or array of context files.
- `agent_args`: raw extra arguments for the target agent.

## Operating Pattern

1. Draft the smallest workflow that covers the task.
2. Run `--dry-run --json` and inspect the prompts, agents, models, and file context.
3. Run the workflow on a narrow slice first when cost or blast radius is high.
4. Inspect the JSON result and verify high-impact claims yourself.
5. If implementation jobs changed files, run tests and a separate read-only review workflow before declaring done.
