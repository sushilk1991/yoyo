# yoyo audit and Codex brief (2026-11-17)

To run it, tell Codex:

> Read `docs/plans/2026-11-17-long-runs-and-quota.md` and carry out the "Brief for Codex" section.

## Verdict on the five ideas

Evidence: 6,645 run records in `~/.local/state/yoyo/runs` (2026-09-07 to 2026-11-17), the code at v0.34.0, and live probes of codex-cli 0.169.0 and Claude Code 2.1.326.

| Idea | Verdict | The number behind it |
|---|---|---|
| Probe and extend the 4-hour limit | **Build first** | Five workers were killed at exactly 14,400 s: four in the iOS redesign on Nov 14 (three Claude, one Codex) and one Codex on Nov 4. None passed `--timeout` and `YOYO_TIMEOUT` is not set on this machine; they hit yoyo's default. Four were writing output within 35 s of the kill. Both Codex runs returned an empty answer, and none of the five can be resumed. |
| Is yoyo token-efficient? | **Measure, don't redesign** | yoyo adds at most about 4 KB (roughly 1k tokens) to a call. A Codex run uses 45k tokens at the median and 179k at p90 (460 runs since Nov 10). The spend is the agent exploring the repo, and yoyo does not record tokens, so nothing can say which calls waste them. |
| Quota-aware routing | **Build the small version** | Both CLIs report quota without an inference call (probed today, details in item 3). 15 calls failed on a usage limit (Codex 7, agy 7, pi 1) and were filed as ordinary failures. |
| Small local decision model (Ollama or packaged) | **Don't** | The repo already tried one (`extras/`): 23/30 and 44/60 on its own fixtures, 27 s first launch, 767 MB of memory, Apple Silicon only. The agent calling yoyo is a frontier model that already holds the task, so it routes better than a 0.8B classifier. Give it data instead. |
| Rewrite in Go, Rust or C | **Don't** | Startup is 0.10 s and the median Codex call is 76 s. `bin/yoyo` is 365 KB. A port means 8,455 lines and 526 tests to save a tenth of a second on calls that wait minutes. What has grown is the run ledger: 1.4 GB, 751 MB of it from Nov 1 to 17. |

Two things to know before you hand this over:

- **The routing work reverses decisions recorded on 2026-07-02** (no auto model routing, no cost accounting). The brief keeps the calling agent in charge: yoyo reports quota and tokens and follows a table you write. It never picks a cheaper model on its own, and it records tokens, never dollars.
- **Extending a deadline on output growth proves activity, not progress.** An agent looping on a failing command would also extend, so the brief adds an absolute ceiling. The fifth killed run (Claude, `ios-home`) had been silent for 13 minutes, which is why the activity window is 30 minutes and not 5.

Defaults I chose that are yours to change: the runtime ceiling (12 h), automatic deletion of raw streams older than 30 days, and the 30-minute activity window.

## Brief for Codex

You are improving yoyo, the CLI in this repo that delegates work to other coding-agent CLIs. Read `AGENTS.md` first; it holds the commands, guardrails and the definition of done. `docs/REFERENCE.md` and `skills/yoyo/SKILL.md` describe the current behaviour.

The goal is that a long delegated run is never lost to a clock while it is working, a killed run can be picked up again, and a caller can see quota and token use before choosing an agent.

### How to work

- Create a branch. Do the items in order, one commit per item, each passing the gates in `AGENTS.md`. Do not push, tag or release.
- For each item, write the test first and confirm it fails before the change. Fake agents with `YOYO_AGENT_<NAME>` commands as the existing tests do. Timing tests must finish in seconds, so make every new clock overridable.
- Use an isolated `YOYO_STATE_DIR` for every test and trial. Never run prune or retention against the real `~/.local/state/yoyo`.
- Tick the checkbox for an item in this file in the same commit. If your own session is cut off, the next one resumes from the first unticked box.
- Where an item says "verify first", run the check and paste its output in your report. If the check fails, ship the smaller behaviour named in that item and say so.
- Update `docs/REFERENCE.md` and `skills/yoyo/SKILL.md` with each new flag or behaviour. Bump `VERSION` once, in the last commit.

### Items

- [x] **1. The default deadline renews while the agent is active.**
  Today `run_to_files` kills at `--timeout` no matter what the agent is doing, and the default is 14,400 s.
  - When neither `--timeout` nor `YOYO_TIMEOUT` is set, 14,400 s becomes a soft deadline. At the deadline, if captured stdout plus stderr grew in the last 1,800 s, extend by 3,600 s and check again at the new deadline. If it did not grow, kill as today.
  - An absolute ceiling, `YOYO_MAX_RUNTIME` (default 43,200 s), ends the run however active it is.
  - An explicit `--timeout N` or `YOYO_TIMEOUT` stays a hard kill at N. Callers chose it.
  - Agents marked `buffers_output` cannot show activity, so they keep the hard default.
  - Each extension prints one stderr line and is counted in `result.json`. `yoyo runs autopsy` and `yoyo runs audit` name which clock ended a run: explicit timeout, quiet at the deadline, or the ceiling.
  - This applies wherever the default timeout applies: `ask`, `review`, `research`, loop iterations and background workers.
  - Proof: tests for a chatty agent that outlives the soft deadline and exits 0 with at least one extension; an agent that goes quiet and is killed at the soft deadline; a chatty agent stopped at the ceiling; an explicit `--timeout` that kills a chatty agent at N with no extension. Repeat the first case through `--background` and `yoyo wait`, because the killed runs were launched that way.

- [x] **2. A killed run leaves an answer and a way to continue.**
  The killed Codex runs ran with `--ephemeral` and left a 0-byte `answer.txt`; the Claude runs ran with `--no-session-persistence`.
  - On any timeout or signal kill, `answer.txt` holds the last complete agent message recovered from the captured stream, for Codex as well as Claude.
  - Full-access `ask` calls to Codex and Claude keep a backend session, and its id is stored in that run's own record. The timeout envelope and `yoyo runs autopsy` print one command that continues the run. Read-only calls stay ephemeral. How this shares code with `--session` is your call; do not add an entry per run to the named-session registry.
  - Verify first: Claude's headless docs say SIGINT ends the turn and returns a result while SIGTERM exits 143 with none. Check whether stopping with SIGINT first, then SIGTERM after a grace period, yields a final answer on each CLI, and use it if it does.
  - Verify first: start a real Codex run and a real Claude run that are each given a random word, stop each mid-task the way yoyo's deadline does, resume it, and ask for the word. Also check what keeping sessions adds to `codex resume` and `claude --resume` listings for the user. If a killed session cannot be resumed on either CLI, ship the recovered answer alone for that CLI.
  - Proof: a test that a fake agent killed at the deadline leaves a non-empty `answer.txt` and a continue command in the envelope; the live output above.

- [x] **3. `yoyo quota` reports what is left, and quota failures are named.**
  - Codex: the app-server method `account/rateLimits/read`, on the same pipe `_codex_models` opens. On codex-cli 0.169.0 it answered in 2.1 s with `rateLimits.primary.{usedPercent, windowDurationMins, resetsAt}`, `secondary`, `credits` and `rateLimitReachedType`.
  - Claude: the initialize handshake has no quota fields. 66 of this month's 88 stored `claude -p` streams contain a `rate_limit_event`. On 2.1.326 it carries `rate_limit_info.{status, rateLimitType, resetsAt, unifiedWindows.<name>.{utilization, resetsAt, status}}`; Anthropic documents only `status`, `resetsAt` and `utilization`, so read the others when present and do not require them. Report the newest event from the ledger with its age, labelled as observed, not live.
  - Other agents report `unknown`. Do not scrape private endpoints.
  - `yoyo quota [AGENT...] [--json]` prints per agent: source (`live`, `observed`, `unknown`), observed time, each window's used percentage and reset time, and whether the agent is blocked. Print only those fields; the raw replies contain account identifiers. `yoyo models` shows the same line in place of "quota not checked".
  - A run that ends on a usage limit gets `failure_kind: "quota"` and the reset time when the message carries one. `yoyo runs audit` counts these in their own column. Match the final error lines only: a search of the whole stream matches Claude's routine `rate_limit_event` lines. Real examples from the ledger: "ERROR: You've hit your usage limit. … try again at Sep 19th, 2026 2:04 PM" (Codex), "Individual quota reached. … Resets in 75h19m18s" (agy), "429 … reached your session usage limit" (pi via Ollama).
  - Proof: tests with a fake app-server reply, a ledger fixture holding a `rate_limit_event`, and the three error strings; one real `yoyo quota --json` run.

- [ ] **4. A caller can route around an exhausted vendor.**
  - `yoyo ask AGENT --fallback OTHER[,OTHER]` reruns the same prompt on the next agent only when the first is blocked at launch or ends with `failure_kind: "quota"`. No other failure triggers it. `--read-only` carries over. When the first agent had full access, the fallback's prompt starts with one line saying a previous agent was stopped by its usage limit and the working tree may hold partial changes. The envelope records which agent answered and why. A block that was observed, not read live, stops counting once its reset time has passed.
  - Route aliases: an optional user-owned file in yoyo's config directory maps a name to an ordered list of `{agent, model}` entries. `yoyo ask @name …` uses the first entry that is not blocked. Ship no table of model names; the ledger shows three Codex model generations in ten weeks. `yoyo doctor` warns when an alias names a model missing from the live catalog that `yoyo models` reads.
  - yoyo never substitutes a model the caller or the alias did not name.
  - Proof: tests that a fake agent failing with a quota message falls back, that one failing any other way does not, and that an alias skips a blocked entry.

- [ ] **5. Token use is recorded per run.**
  - Store input, output, cached and total tokens in `result.json`: from the `usage` object of Claude's final `result` event, and for Codex from the "tokens used" trailer it prints or the `turn.completed.usage` event of `codex exec --json`. Leave the field out when an agent reports nothing. No dollar figures.
  - `yoyo runs audit` shows token p50 and p90 per agent and per model.
  - Proof: tests with a captured sample of each format; one real run per vendor whose recorded total matches what the CLI printed.

- [ ] **6. The ledger stops growing without bound.**
  One Claude run stores 4.8 MB of stream, 95% of it echoed tool results, and pruning is manual.
  - At most once a day, a finishing run removes `stdout.txt` and `stderr.txt` from run directories older than `YOYO_RETENTION_DAYS` (default 30, 0 disables). `meta.json`, `result.json`, `answer.txt` and `prompt.txt` stay, so `yoyo runs audit` keeps its history. A run that is still going is never touched.
  - Proof: tests that old streams go and the small files stay, that a running run is skipped, that 0 disables it, and that a second pass the same day does nothing.

### Out of scope

A Go or Rust port, a local classifier that picks models, any judgement of whether an agent is making progress, dollar accounting, and model choices made by yoyo without the caller or an alias naming them.

### Report back

For each item: the commit, the tests added, the pasted output of any live check, and anything you shipped smaller than specified with the reason. Then the full output of the two gate commands from `AGENTS.md`.

## Also noticed, not in the brief

- 24 of the 33 hard timeouts were a caller passing `--timeout 400` on thumbnail and icon jobs. The skill already warns against short budgets.
- `caller` is `unknown` on every record, so the ledger cannot say which agent launched what.

## Outside research (leads from a web pass; I did not re-check them)

- Temporal uses the same two-clock design as item 1: a generous overall limit plus a short heartbeat timeout. CircleCI kills on 10 minutes of no output. [Temporal](https://docs.temporal.io/encyclopedia/detecting-activity-failures), [CircleCI](https://circleci.com/docs/reference/configuration-reference/)
- OpenHands flags a stuck agent by repeated actions (the same action and result four times, the same error three times) and tells the caller. That is a possible later addition; it is not in this brief. [OpenHands](https://docs.openhands.dev/sdk/guides/agent-stuck-detector)
- `account/rateLimits/read` is in OpenAI's app-server documentation. Claude's undocumented OAuth usage endpoint is reported to return 429s and sits outside the stated terms, so the brief does not use it. [Codex app-server](https://learn.chatgpt.com/docs/app-server)
- An independent benchmark found that learned and commercial model routers "fail to reliably outperform a simple baseline". Vendor savings claims of 30–66% are self-reported. [LLMRouterBench](https://arxiv.org/abs/2601.07206)
- OpenAI moved Codex from TypeScript to Rust for install size, sandbox bindings and a wire protocol, and published no speed numbers. [discussion](https://github.com/openai/codex/discussions/1174)
- X and Reddit returned only anecdotes; nothing from them changed a verdict.
