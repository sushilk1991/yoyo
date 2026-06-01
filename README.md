# yoyo

`yoyo` is a tiny, dependency-free CLI for calling one coding agent from another. It lets Codex, Claude, Pi, or configured custom agents ask each other for second opinions, reviews, scoped worker tasks, and interactive sessions.

The design is deliberately boring: one Python script, subprocess calls, explicit prompts, no daemon, no package manager requirement.

Requires Python 3.9+.

## Install

```bash
git clone https://github.com/sushilk1991/yoyo.git
cd yoyo
./install.sh
```

This installs:

- `~/.local/bin/yoyo`
- the bundled `yoyo` skill for Codex, Claude, and Pi when their standard skill directories are present or creatable

Make sure `~/.local/bin` is on `PATH`.

## Usage

Check available agents:

```bash
yoyo doctor
yoyo agents
```

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

## Durability

- Each `ask` call carries a trace ID in the prompt metadata and JSON result.
- Plain output also emits `trace_id=...` on stderr.
- Subprocess stdout/stderr is captured through temporary files instead of unbounded in-memory pipes.
- Output is capped by `--max-output-bytes` or `YOYO_MAX_OUTPUT_BYTES` and reports truncation in JSON mode.
- Stdin and `--file` context share an aggregate cap from `--max-input-bytes` or `YOYO_MAX_INPUT_BYTES`.
- Temporary Codex output files live inside a per-call temporary directory and are cleaned up automatically.
- `--cwd` is validated before launching the target agent.
- Timeouts kill the target process group on POSIX systems.

## Test

```bash
python3 -m unittest discover -s tests
```
