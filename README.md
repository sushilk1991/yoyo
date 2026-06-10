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
- bundled workflow templates at `~/.config/yoyo/workflows/`
- the `yoyo-imagegen` skill (only when codex is on PATH)
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
yoyo ask codex --role review --cwd "$PWD" --file bin/yoyo "Find correctness bugs and missing tests. Inspect the repo as needed."
```

Delegate scoped work:

```bash
yoyo ask pi --role worker --cwd "$PWD" "Fix the failing test. Do not touch unrelated files."
```

Pipe context:

```bash
git diff | yoyo ask claude --role review --cwd "$PWD" "Review this diff. Use the current worktree as the source of truth."
```

For repo review, the worktree is the primary context. A diff is useful focus, but diff-only review can miss callers, tests, config, generated behavior, and adjacent invariants. Prefer `--cwd "$PWD"` plus a diff or `--file` context.

Steer the target agent with a skill:

```bash
yoyo ask codex --role worker --cwd "$PWD" --skill frontend-design "Build the settings page. Keep the change minimal."
```

`--skill <name>` (repeatable) injects a named `SKILL.md` into the delegated prompt as guidance. This is the main lever for making an otherwise unpredictable agent produce consistent results: the skill constrains *how* the work is done while the prompt states *what* to do. Skills are resolved by directory name from `YOYO_SKILL_PATH` (colon-separated), then `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills`, `~/.config/opencode/skills`, and the Pi skills directory. First match wins; a missing skill fails loudly before the agent is launched. Per-skill content is capped by `YOYO_SKILL_MAX_BYTES` (default 100000).

List discoverable skills:

```bash
yoyo skills
yoyo skills --json
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

Long calls print a periodic progress heartbeat to stderr so a working agent is not mistaken for a hung one (`--quiet` or `YOYO_HEARTBEAT_SECS=0` disables it; `YOYO_HEARTBEAT_SECS` sets the interval). For a stricter hang guard on agents that stream output, add `--idle-timeout <seconds>` (or `YOYO_IDLE_TIMEOUT`) to terminate an agent that goes silent. yoyo reads stdin as context only when input is actually available, so an open-but-idle stdin can no longer block the call before the agent starts; use `--stdin-wait <seconds>` to wait for a slow producer or `--no-stdin` to ignore stdin entirely.

Limit captured output:

```bash
yoyo ask codex --max-output-bytes 200000 "Summarize this repo."
```

Limit captured input:

```bash
git diff | yoyo ask claude --cwd "$PWD" --max-input-bytes 200000 "Review this diff. Inspect the worktree as needed."
```

Open an interactive session:

```bash
yoyo chat claude
yoyo chat codex --cwd "$PWD" "Help me debug this repo."
yoyo chat pi --agent-arg=--provider --agent-arg=anthropic --model haiku
```

## Background Runs

A real review or worker task can outlive the calling agent's own tool budget. `--background` detaches the run and records it in a durable run ledger, so the caller returns immediately and collects the result later:

```bash
run_id=$(yoyo ask codex --role review --cwd "$PWD" --background "Audit the auth module.")
yoyo runs list
yoyo wait "$run_id"            # blocks until done, exits with the run's exit code
yoyo runs show "$run_id" --json
yoyo runs prune --days 7       # delete old finished runs
```

Runs live under `$YOYO_STATE_DIR/runs/<run_id>/` (default `~/.local/state/yoyo`): `meta.json` (agent, pid, trace id, argv), `result.json` (the standard JSON envelope), `log.txt` (stderr/heartbeats), and `stdin.txt` when context was piped. Status is derived, never stored: `done` (result.json parses), `running` (pid alive), `dead` (pid gone, no result). Argument validation still happens before detaching, so a bad agent name or cwd fails loudly in the foreground. One caveat inherent to pid-based liveness: if the OS recycles a dead child's pid, a crashed run can read as `running` until the impostor pid exits; `wait` then times out rather than reporting `dead`.

## Sessions

`yoyo ask` is one-shot by default. `--session <name>` gives a named durable conversation with the target agent — the first call creates it, later calls continue it with full context:

```bash
yoyo ask codex --session auth-review --role review --cwd "$PWD" "Review the auth changes."
yoyo ask codex --session auth-review "Is the issue you flagged in middleware.py fixed by the latest commit?"
yoyo sessions list
yoyo sessions rm codex:auth-review
```

Per agent: claude uses `--session-id` on create and `--resume` on follow-up (session persistence re-enabled for these calls); codex creates a persistent `codex exec` session and yoyo records the session id from its banner, then resumes with `codex exec resume <id>` (sandbox passed via `-c sandbox_mode=...` because the resume subcommand has no `--sandbox` flag); pi uses `--session-id`, which creates or resumes with the same flag. The mapping name -> backend session id lives in `$YOYO_STATE_DIR/sessions.json`; `yoyo sessions rm` removes only the mapping, not the backend's stored conversation. Custom agents without a built-in flavor reject `--session` loudly. If a codex create call fails before the banner is captured, yoyo warns that the session was not recorded — a retry then starts a fresh conversation rather than resuming.

`--session` also works with `chat` for interactive follow-ups (codex chat can only resume an existing recorded session).

## Image Generation

`yoyo imagegen` generates a real raster image by delegating to an agent with a native image-generation tool. The default agent is codex, whose bundled `imagegen` skill uses the built-in `image_gen` tool (GPT-image models, no API key needed):

```bash
yoyo imagegen "Hand-drawn flowchart in black marker on white, four boxes labeled 'SPEC', 'BUILD', 'GATE', 'REVIEW', bold arrows. No other text." --out flow.png --size 1536x1024 --quality high
yoyo imagegen "make the background white" --edit flow.png --out flow-v2.png
yoyo imagegen "..." --out hero.png --json
```

The delegated prompt forbids code-drawn output (no PIL, no SVG, no matplotlib) and instructs the agent to fail rather than fake it. Yoyo then verifies the artifact deterministically: the file must exist at `--out`, have changed since before the call, start with the correct magic bytes for its extension (`.png`, `.jpg`, `.jpeg`, `.webp`), and have a plausible size. A renamed SVG or a stale leftover file fails the run loudly.

`--size WIDTHxHEIGHT` and `--quality low|medium|high|auto` pass through as hints; `--edit existing.png` switches to edit mode with that image as the reference. Use `--quality low` to iterate on composition, then regenerate the final at `high`.

The bundled `yoyo-imagegen` skill teaches calling agents when to reach for images (flow diagrams in plans, whimsical explainer notes, architecture sketches) and how to write prompts that produce document-quality results. It installs only when codex is on PATH, since codex provides the default image tool.

## Live Doctor

`yoyo doctor` checks that agent binaries exist. `yoyo doctor --live` goes further: it fires a real one-line probe through each found agent in both read-only and full-access mode, exercising the exact hardcoded flag paths yoyo depends on. Run it after upgrading claude/codex/pi to catch flag drift before it surfaces as a confusing mid-task failure:

```bash
yoyo doctor --live
yoyo doctor --live --agent codex --timeout 60 --json
yoyo doctor --live --strict   # exit 1 on any failed probe or missing agent
```

Probes run from a temporary directory, cost a few tokens each, and run in parallel across agents. Custom agents without `read_only_args` get their read-only probe skipped and reported as such.

## Workflows

`yoyo workflow` runs a saved multi-agent workflow from a JSON spec. Use it when one agent call is not enough: fan out research across files, ask different agents/models for independent audits, run an implementation phase followed by a review phase, or cross-check several findings before acting.

Workflows are deliberately local and auditable. Yoyo does not add a daemon or hidden planner. It reads the spec, expands jobs, runs each phase in order, runs jobs inside a phase in parallel up to `max_concurrency`, and returns a JSON result with commands, trace IDs, exit codes, duration, stdout/stderr, and truncation flags.

Run a reusable multi-agent workflow:

```bash
yoyo workflow ./workflow.json --input "audit auth routes" --json
```

Run a bundled template by name:

```bash
yoyo workflow --list
yoyo workflow cross-review --input "review the current branch diff" --json
```

A bare name (no path, no `.json`) is resolved against `YOYO_WORKFLOW_PATH` (colon-separated dirs), then `~/.config/yoyo/workflows/` (populated by `install.sh`), then the source checkout's `workflows/` directory. Bundled templates:

- `cross-review`: Codex and Claude review independently in parallel, then a synthesis judge verifies each finding against the code and rejects weak claims.
- `adversarial-audit`: three parallel single-lens audits (correctness, security, maintainability), then an adversarial verifier that tries to refute every finding and keeps only survivors.
- `frontend-impl-review`: a Codex worker implements a frontend task with the `frontend-design` skill injected, then an independent read-only review checks the result against the same skill. Requires a `frontend-design` skill to be discoverable.

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
- `skill`: skill name(s) injected into the job prompt as guidance (job, phase, or defaults level).
- `retries`: re-run a job up to N extra times when it fails its exit code or `expect` contract.
- `expect`: optional output marker check (`contains` and/or `regex`) against job stdout.
- `gates` (phase level): optional shell commands run after the phase's jobs.

## Verification Hooks (opt-in)

Yoyo does not constrain or grade agent output by default. The calling agent states the output it wants in its prompt and judges the result itself — that judgment is what the model is for. Three opt-in hooks exist for the narrow cases where code, not a model, is the right verifier:

**Gates** are shell commands attached to a phase. They never inspect agent output; they run real checks — tests, linters, builds — after all the phase's jobs succeed, in order, from the workflow `cwd` (or a gate-level `cwd`). The first failing gate stops the entire workflow with that gate's exit code, regardless of `--fail-fast`. If any job in the phase failed, gates are recorded as skipped. Use a gate when the question has a closed-form answer ("do the tests pass?"); do not delegate that question to another agent:

```json
{
  "name": "implement",
  "jobs": [{"id": "fix", "role": "worker", "read_only": false, "prompt": "Fix the failing test."}],
  "gates": [{"name": "tests", "run": "python3 -m pytest -q"}]
}
```

**`retries`** re-runs a job (same prompt) when it fails, up to N extra attempts. The JSON result records `attempts` per job. Use it for transient failures (crashes, rate limits, truncated runs).

**`expect`** is a per-job stdout marker check. If the agent exits 0 but stdout is missing a `contains` string or does not match `regex`, the job fails with exit code 3 and `output contract not met` in stderr. Use it sparingly, and only for a marker the prompt explicitly told the agent to emit (for example "end with a line `VERDICT: PASS` or `VERDICT: FAIL`"). Never use it to grade free-form prose — a regex over an answer the agent phrased its own way produces false failures and pushes quality down, which is worse than no check at all. When in doubt, leave `expect` off and let the supervising agent read the output.

Misconfigured specs — unknown skills, invalid `expect`, invalid gates — fail loudly when the spec is validated, before any agent call spends tokens.

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

Built-in agents depend on specific flags of the underlying CLIs: codex's `exec`, `--sandbox`, `--ask-for-approval`, `--output-last-message`, `-C`, `--skip-git-repo-check`, `--ephemeral`; claude's `-p`, `--permission-mode`, `--tools`, `--model`; pi's `-p`, `--no-session`, `--mode`, `--tools`, `--model`. yoyo does not detect CLI versions, so if one of these tools renames or changes a flag, the call will fail as a confusing agent error rather than a yoyo error. If a built-in agent suddenly breaks after a CLI upgrade, check its flags here first. To pin an exact path or adjust flags without code changes, override the command via `~/.config/yoyo/agents.json` (use `kind` to keep built-in flag behavior, or define `read_only_args`/`full_access_args` for a fully custom command).

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

`--agent-arg` is intentionally raw and powerful. It exists for provider-specific flags and advanced routing. If you use it with constrained modes such as `--read-only`, verify the final command with `--dry-run`; a raw argument may change the target agent's effective behavior.

## Access Model

`yoyo ask` defaults to full access so agent-to-agent calls do not stop for approval prompts:

- Codex receives `--sandbox danger-full-access --ask-for-approval never`
- Claude receives `--permission-mode bypassPermissions`
- Pi receives read, grep, find, ls, bash, edit, and write tools

Use `--read-only` when you want a bounded reviewer:

- Codex receives `--sandbox read-only`
- Claude receives read-oriented tools only
- Pi receives read-oriented tools only

`--read-only` is enforced by the target agent's own mechanism, not by yoyo, and the strength varies: Codex applies an actual filesystem sandbox, while Claude and Pi apply tool allowlists. yoyo guarantees the constraining flags are passed (and fails loudly for custom agents that have no `read_only_args`), not that the downstream CLI honors them perfectly. Treat read-only as a strong default, not an airtight sandbox.

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
- stdin is read only when input is actually available, so an open-but-idle stdin cannot hang the call before the agent starts (`--stdin-wait` waits for slow producers; `--no-stdin` ignores stdin).
- Long calls emit a periodic progress heartbeat on stderr; `--idle-timeout`/`YOYO_IDLE_TIMEOUT` adds an optional no-output hang guard.
- Timeouts kill the target process group on POSIX systems.
- SIGINT/SIGTERM/SIGHUP and normal exit terminate any in-flight agent process groups, so an interrupted or killed yoyo does not orphan nested agents.
- `yoyo workflow` records a run trace ID, per-job trace IDs, agent commands, exit codes, durations, and truncation flags in its JSON result.
- Agent and workflow jobs default to a one-hour timeout and fail loudly on timeout.
- Workflows enforce `max_concurrency`, `max_jobs`, per-job timeouts, per-job output caps, and a bounded previous-output context size.

If a real agent review times out, treat the review as unavailable and say so. Do not count a timed-out review as completed, and do not hide the timeout by summarizing local checks as an external review.

## Test

```bash
python3 -m unittest discover -s tests
```
