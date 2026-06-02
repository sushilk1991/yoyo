# yoyo

`yoyo` is a tiny, dependency-free CLI for calling one coding agent from another. It lets Codex, Claude, Pi, or configured custom agents ask each other for second opinions, reviews, scoped worker tasks, and interactive sessions.

The design is deliberately boring: one Python script, subprocess calls, explicit prompts, no daemon, no package manager requirement.

It also includes `yoyo workflow`, a local multi-agent runner for reusable fan-out and audit workflows. A workflow is a JSON spec that runs phases in order, runs jobs within a phase in parallel, passes per-job agent/model/role settings to `yoyo ask`, and can feed previous phase results into later read-only review jobs.

Requires Python 3.9+.

## Install

```bash
git clone https://github.com/sushilk1991/yoyo.git
cd yoyo
./install.sh
```

This installs:

- `~/.local/bin/yoyo`
- the bundled `yoyo` and `yoyo-workflow` skills for Codex, Claude, Pi, OpenCode, and agent-compatible skill directories when their standard directories are present or creatable
- a source checkout pointer at `~/.config/yoyo/source`, used by `yoyo update`

Make sure `~/.local/bin` is on `PATH`.

Update the local install from the recorded checkout:

```bash
yoyo update
```

This runs `git fetch`, `git pull --ff-only`, then `install.sh`. Reinstall from the current checkout without pulling:

```bash
yoyo update --no-pull
```

## Usage

Check available agents:

```bash
yoyo doctor
yoyo agents
```

`yoyo doctor` also reports whether a source checkout has been recorded for `yoyo update`.

Ask for a second opinion:

```bash
yoyo ask claude --role opinion "Challenge this design and list failure modes."
```

`--role` defaults to `opinion`; specify `review` or `worker` when you want those behaviors.

By default, `yoyo ask` is one-shot, full-access, and configured not to ask follow-up permission prompts where the target agent supports that. Use `--read-only` for constrained review.
When full-access mode includes piped stdin or `--file` context, `yoyo` prints a warning because that context may be untrusted.

Review a file:

```bash
yoyo ask codex --role review --file bin/yoyo "Find correctness bugs and missing tests."
```

Delegate scoped work:

```bash
yoyo ask pi --role worker --cwd "$PWD" "Fix the failing test. Do not touch unrelated files."
```

Pipe context:

```bash
git diff | yoyo ask claude --role review "Review this diff."
```

JSON output:

```bash
yoyo ask claude --json --role opinion "Return one risk."
```

Trace a delegated call:

```bash
yoyo ask claude --trace-id "auth-review-001" --json "Review this plan."
```

Agent calls default to a one-hour timeout. The timeout is a hung-process guard, not a progress budget. Do not shorten it for real reviews, audits, or worker delegations; short caps are only for deterministic smoke tests with fake or trivial agents. Override with `--timeout` or `YOYO_TIMEOUT` only when you have an explicit operational reason.

Limit captured output:

```bash
yoyo ask codex --max-output-bytes 200000 "Summarize this repo."
```

Limit captured input:

```bash
git diff | yoyo ask claude --max-input-bytes 200000 "Review this diff."
```

Open an interactive session:

```bash
yoyo chat claude
yoyo chat codex --cwd "$PWD" "Help me debug this repo."
yoyo chat pi --agent-arg=--provider --agent-arg=anthropic --model haiku
```

## Workflows

`yoyo workflow` runs a saved multi-agent workflow from a JSON spec. Use it when one agent call is not enough: fan out research across files, ask different agents/models for independent audits, run an implementation phase followed by a review phase, or cross-check several findings before acting.

Workflows are deliberately local and auditable. Yoyo does not add a daemon or hidden planner. It reads the spec, expands jobs, runs each phase in order, runs jobs inside a phase in parallel up to `max_concurrency`, and returns a JSON result with commands, trace IDs, exit codes, duration, stdout/stderr, and truncation flags.

Run a reusable multi-agent workflow:

```bash
yoyo workflow ./workflow.json --input "audit auth routes" --json
```

Dry-run a workflow before spending model calls:

```bash
yoyo workflow ./workflow.json --input "audit auth routes" --dry-run --json
```

Workflow jobs default to read-only mode. Set `read_only: false` only for tightly scoped worker jobs that should edit files.

Minimal workflow spec:

```json
{
  "name": "review-and-cross-check",
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

Common workflow fields:

- `phases`: ordered groups of work. Later phases can include earlier results.
- `jobs`: agent calls inside a phase. Jobs in the same phase may run in parallel.
- `agent`: `codex`, `claude`, `pi`, or a configured custom agent.
- `model`: model name passed through to agents that support `--model`.
- `role`: `opinion`, `review`, or `worker`.
- `files`: context files attached to the job prompt.
- `for_each`: expands one job template into many jobs.
- `include_previous`: includes all prior phase outputs in the job prompt.
- `include_phases`: includes only named prior phases.
- `max_concurrency`: caps parallel jobs.
- `max_jobs`: caps expanded jobs before any agent call starts.
- `context_bytes`: caps prior-output context injected into later jobs.

Use `for_each` to fan out one job template:

```json
{
  "id": "audit-{index}",
  "for_each": ["README.md", "bin/yoyo", "tests/test_yoyo.py"],
  "prompt": "Audit {item}.",
  "files": ["{item}"]
}
```

Use `include_previous` for a review or synthesis phase:

```json
{
  "name": "review",
  "jobs": [
    {
      "id": "cross-check",
      "role": "review",
      "include_previous": true,
      "prompt": "Cross-check the previous findings. Reject weak claims and list only concrete issues."
    }
  ]
}
```

## Agents

Built-in agents:

- `codex`: runs `codex exec`
- `claude`: runs `claude -p --no-session-persistence`
- `pi`: runs `pi -p --no-session --mode text`

Custom agents can be added with `~/.config/yoyo/agents.json`:

```json
{
  "agents": {
    "local": {
      "command": ["python3", "/path/to/my-agent.py"],
      "read_only_args": ["--read-only"],
      "full_access_args": ["--write"]
    },
    "echoer": "cat"
  }
}
```

For configured agents, `read_only_args` and `full_access_args` are appended automatically based on `--read-only`. If a custom agent has no `read_only_args`, `yoyo ask custom --read-only ...` fails loudly instead of pretending the custom agent is constrained.

To override a built-in command while keeping built-in flag behavior, set `kind`:

```json
{
  "agents": {
    "codex": {
      "kind": "codex",
      "command": ["/custom/path/codex", "exec"]
    }
  }
}
```

Or with environment variables:

```bash
export YOYO_AGENT_ECHOER=cat
yoyo ask echoer "hello"
```

Custom agents receive the rendered prompt on stdin.

## Access Model

`yoyo ask` defaults to full access so agent-to-agent calls do not stop for approval prompts:

- Codex receives `--sandbox danger-full-access --ask-for-approval never`
- Claude receives `--permission-mode bypassPermissions`
- Pi receives read, grep, find, ls, bash, edit, and write tools

Use `--read-only` when you want a bounded reviewer:

- Codex receives `--sandbox read-only`
- Claude receives read-oriented tools only
- Pi receives read-oriented tools only

This is intentionally powerful. Agent output is not truth; verify it with code, tests, docs, or live state before acting.

## Workflow Safety

Workflow jobs default to `read_only: true`. Feeding previous agent output into later prompts with `include_previous` or `include_phases` is useful for cross-checking, but that output is untrusted text. Yoyo blocks previous-output injection into a write-capable job or a job with raw `agent_args` unless the workflow explicitly sets `allow_untrusted_context: true`.

Reviewer jobs never gate control flow by themselves. The supervising agent or human must inspect the result and verify claims with tests, code, docs, or live state.

## Durability

- Each `ask` call carries a trace ID in the prompt metadata and JSON result.
- Plain output also emits `trace_id=...` on stderr.
- Subprocess stdout/stderr is captured through temporary files instead of unbounded in-memory pipes.
- Output is capped by `--max-output-bytes` or `YOYO_MAX_OUTPUT_BYTES` and reports truncation in JSON mode.
- Stdin and `--file` context share an aggregate cap from `--max-input-bytes` or `YOYO_MAX_INPUT_BYTES`.
- Temporary Codex output files live inside a per-call temporary directory and are cleaned up automatically.
- `--cwd` is validated before launching the target agent.
- Timeouts kill the target process group on POSIX systems.
- `yoyo workflow` records a run trace ID, per-job trace IDs, agent commands, exit codes, durations, and truncation flags in its JSON result.
- Agent and workflow jobs default to a one-hour timeout and fail loudly on timeout.
- Workflows enforce `max_concurrency`, `max_jobs`, per-job timeouts, per-job output caps, and a bounded previous-output context size.

If a real agent review times out, treat the review as unavailable and say so. Do not count a timed-out review as completed, and do not hide the timeout by summarizing local checks as an external review.

## Test

```bash
python3 -m unittest discover -s tests
```
