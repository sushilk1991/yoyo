# yoyo

Stdlib Python CLI in `bin/yoyo`. Skills in `skills/<name>/SKILL.md`. Tests in `tests/test_yoyo.py`. Flags in `docs/REFERENCE.md`.

## Commands

From the repo root. There is no package build.

```
python3 -m py_compile bin/yoyo
python3 -m unittest discover -s tests
python3 -m unittest tests.test_yoyo.YoyoTests.test_version_outputs_current_release
python3 bin/yoyo --help
```

`py_compile` is silent on success. Tests print `OK` and exit 0. Host `ruff`/`black`/`pytest` are not this project's tools; do not run them here.

## Guardrails

- Need new behavior: add a function in `bin/yoyo`. Leave it one file, stdlib only, Python 3.9+ (`from __future__ import annotations` is already there).
- Need a test: subclass `CliTestCase` in `tests/test_yoyo.py` and call `run_cli`. Fake agents with `YOYO_AGENT_ECHO` / `YOYO_AGENT_SLOW`; keep `YOYO_STATE_DIR` isolated (already in `CliTestCase`).
- Change `VERSION` in `bin/yoyo`: update the expected string in `test_version_outputs_current_release` in the same commit.
- Edit `skills/yoyo/SKILL.md`: keep every string in `SkillGuardTests.LOAD_BEARING_ANCHORS`. Quote a YAML `description:` that contains `: `.
- `yoyo-fable-mode` is inbuilt: edit `skills/yoyo-fable-mode/SKILL.md`; do not copy it into agent homes.
- Flag or caller-recipe questions: read `docs/REFERENCE.md` and `skills/yoyo/SKILL.md`.

## Done

Both must exit 0:

```
python3 -m py_compile bin/yoyo
python3 -m unittest discover -s tests
```

Skill edits also: `python3 -m unittest tests.test_yoyo.SkillGuardTests`

Version edits also: `python3 -m unittest tests.test_yoyo.YoyoTests.test_version_outputs_current_release`

Passing looks like unittest's `OK` line and empty `py_compile` output.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
