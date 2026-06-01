# yoyo

`yoyo` is a tiny, dependency-free CLI for calling one coding agent from another. It lets Codex, Claude, Pi, or configured custom agents ask each other for second opinions, reviews, and scoped worker tasks.

The design is deliberately boring: one Python script, subprocess calls, explicit prompts, no daemon, no package manager requirement.

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

Review a file:

```bash
yoyo ask codex --role review --file bin/yoyo "Find correctness bugs and missing tests."
```

Delegate scoped work:

```bash
yoyo ask pi --role worker --write --cwd "$PWD" "Fix the failing test. Do not touch unrelated files."
```

Pipe context:

```bash
git diff | yoyo ask claude --role review "Review this diff."
```

JSON output:

```bash
yoyo ask claude --json --role opinion "Return one risk."
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
      "command": ["python3", "/path/to/my-agent.py"]
    },
    "echoer": "cat"
  }
}
```

Or with environment variables:

```bash
export YOYO_AGENT_ECHOER=cat
yoyo ask echoer "hello"
```

Custom agents receive the rendered prompt on stdin.

## Safety Model

`yoyo` defaults to read-only delegation where supported:

- Codex receives `--sandbox read-only`
- Claude receives read-oriented tools only
- Pi receives read-oriented tools only

Use `--write` only for scoped worker tasks. Agent output is not truth; verify it with code, tests, docs, or live state before acting.

## Test

```bash
python3 -m unittest discover -s tests
```
