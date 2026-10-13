# Muse → Codex sandbox diagnosis — 2026-10-13

Muse supports `--yolo`. yoyo's current Muse worker flags disable approvals but retain the sandbox. A real Muse shell probe reproduced Codex's initialization failure and found two blocked operations: writing to Codex's home and applying a nested macOS Seatbelt policy. A local socket-pair probe succeeded.

No adapter default, authentication configuration, or saved permission policy was changed. Successful nested delegation with YOLO or one-command escalation has not been tested.

## Tested boundary

Versions: Muse Code 1.4.7 (`1.4.7-R6101.1`), Codex CLI 0.162.1, macOS arm64. Muse probes used `MUSE_NO_AUTO_UPDATE=1`. Primary documentation was checked October 13, 2026; Meta's published changelog currently starts at 1.4.6, so native help and probes provide the evidence specific to this installed 1.4.7 build.

The same probe ran outside Muse and inside a yoyo Muse worker using `--trust-workspace --disable-approval`. It opened the existing `~/.codex/installation_id` read/write without reading or changing its contents, created a local socket pair, attempted a no-op nested Seatbelt launch, and forked a nonexistent Codex thread. The fork exercises initialization without submitting a model turn.

| Operation | Outside Muse | Inside Muse's worker sandbox |
| --- | --- | --- |
| Open existing Codex installation ID read/write | Succeeds | `EPERM`, errno 1 |
| Create local `socket.socketpair()` | Succeeds | Succeeds |
| Run `sandbox-exec -p '(version 1)(allow default)' /usr/bin/true` | Exit 0 | Exit 71; `sandbox_apply: Operation not permitted` |
| Initialize Codex and fork nonexistent thread | Initializes; expected `no rollout found` error | Fails before fork; app-server initialization denied |

The sandboxed child reported:

```text
WARNING: proceeding, even though we could not create PATH aliases: Operation not permitted (os error 1)
Error: failed to initialize in-process app-server client: Operation not permitted (os error 1)
```

Meta [documents](https://dev.meta.ai/docs/muse-code/permissions#run-inside-the-sandbox) macOS Seatbelt enforcement, workspace/temp writes, and read-only access elsewhere. Codex's [version-pinned installation-ID source](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/core/src/installation_id.rs) opens that file with `read(true).write(true).create(true)` and locks it even when an ID already exists. A read-only task therefore still needs writable CLI state during startup.

These probes establish incompatible startup requirements under this Muse sandbox. They do not trace the exact syscall behind Codex's app-server error. The nested Seatbelt probe demonstrates a separate restriction; it does not prove Codex reached that operation before failing initialization. The successful socket-pair probe does not cover every socket type or destination.

The earlier real Codex child and successful outside-Muse model control remain recorded in [the Muse compatibility report](2026-10-13-muse.md).

## Native permission controls

Meta's [permissions documentation](https://dev.meta.ai/docs/muse-code/permissions#run-without-guardrails) and installed `muse exec --help` distinguish these controls:

| Muse option | Approvals | Sandbox | Workspace trust |
| --- | --- | --- | --- |
| `--disable-approval` | Disabled | Retained | Separate `--trust-workspace` flag |
| `--disable-sandbox` | Retained | Disabled; file tools can access outside the workspace and networking becomes unrestricted | Separate trust control |
| `--yolo` | Disabled | Disabled | Trusted for this run |

The Unrestricted profile has the same posture as `--yolo`. Meta recommends YOLO only in an already-isolated environment with trusted code, not on a workstation with real credentials or infrastructure. A temporary working directory alone does not provide that isolation.

Changing `--sandbox-network` does not grant Codex home writes or permission to apply a nested Seatbelt policy. Moving `CODEX_HOME` into the workspace alone would not address the demonstrated nested-policy restriction; it also changes where Codex looks for configuration and credentials. Neither is an established fix here.

Meta also [documents one-time approval](https://dev.meta.ai/docs/muse-code/changelog) to run an exact blocked command outside the sandbox (0.2.1), and human-approved `require_escalated` commands under restricted profiles (1.4.0). Installed binary guidance names the shell-tool argument `sandbox_permissions: "require_escalated"` and requires one-time human approval. This is a tool request, not a Muse CLI flag. No approval was granted during this diagnosis, and behavior with `--disable-approval` remains untested; it must not be treated as automatic authority to escape the sandbox.

## Existing yoyo opt-in

No new adapter is needed to forward Muse's YOLO flag. This dry-run exited 0 and included `--yolo` after the existing worker flags:

```bash
python3 bin/yoyo ask muse --role worker --agent-arg=--yolo \
  --dry-run --json --no-stdin "Inspect only a disposable test workspace."
```

A dry-run proves argument construction only. It does not launch Muse or verify nested delegation. For an explicitly approved run inside an already-isolated environment, the existing caller recipe is:

```bash
run_id=$(yoyo ask muse --role worker --agent-arg=--yolo \
  --cwd "$PWD" --background "Delegate the bounded read-only check to Codex; verify its answer and ledger.")
yoyo wait "$run_id" --timeout 25
```

If Muse itself is the interactive caller, start that outer caller with `muse --yolo`. Passing YOLO to a child cannot remove restrictions inherited from an already-sandboxed parent.

Do not combine this opt-in with a read-only review or persist Unrestricted as the native default to fix one call. yoyo's read-only Muse path keeps shell and write tools disabled and exposes only `read_file` and `search`.

## Comparison and next decision

OpenAI [documents](https://learn.chatgpt.com/docs/agent-approvals-security#common-sandbox-and-approval-combinations) Codex's `--yolo` as an alias for `--dangerously-bypass-approvals-and-sandbox`. Anthropic [documents](https://code.claude.com/docs/en/permissions#permission-modes) `bypassPermissions` as skipping prompts, while [sandboxing remains a separate control](https://code.claude.com/docs/en/permissions#how-permissions-interact-with-sandboxing). The three vendors' flags are not interchangeable.

1. Keep sandboxed Muse workers by default; use explicit per-call YOLO only with approved external isolation. This needs no behavioral change, but nested success still needs a live test.
2. In an interactive Muse caller, request one-time approval for the exact yoyo command. This retains the default boundary for other commands; the local flow remains unverified.
3. Change yoyo's Muse worker default to YOLO while preserving read-only reviews. This matches yoyo's unsandboxed Codex worker posture but removes Muse's filesystem/network boundary from every ordinary worker call. It requires a separate approval, regression tests, and live artifact/ledger verification.

## Evidence

- Probe script: `/tmp/yoyo-muse-sandbox-probe.py`; outside report: `/tmp/yoyo-muse-sandbox-control.json`.
- Sandboxed run: `20261013T101300-1aac801b`; direct report: `/var/folders/r2/hxzthddn0vnd14jsz35qwcc40000gn/T/yoyo-muse-sandbox-diagnosis-rgfajptg/diagnosis.json`.
- Native help: `/tmp/yoyo-muse-sandbox-help.txt`, `/tmp/yoyo-muse-sandbox-main-help.txt`; extracted escalation guidance: `/tmp/yoyo-muse-sandbox-escalation-hints.txt`.
- YOLO command construction only: `/tmp/yoyo-muse-yolo-dry-run.json`.
- Independent read-only primary-source research: `20261013T102006-0ac63fd0`; `/tmp/yoyo-muse-sandbox-research-wait-2.json`. The coordinator checked the cited Meta pages separately. An earlier attempt failed before inference because `codex exec` rejected an appended `--search` flag.

After these documentation edits, compilation, all 378 tests, all 8 skill guards, and `git diff --check` passed. Outputs: `/tmp/yoyo-muse-sandbox-tests.txt`, `/tmp/yoyo-muse-sandbox-guards.txt`. These regression checks do not verify nested YOLO execution.
