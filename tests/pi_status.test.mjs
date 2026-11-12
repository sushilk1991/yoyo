import assert from "node:assert/strict";
import test from "node:test";
import extension from "../extras/pi/yoyo-status.ts";

// Stub host APIs and timers; no model, terminal renderer, or provider is started.
function host(t, exec = async () => ({ code: 0, stdout: "YOYO · idle\n", killed: false })) {
  const handlers = new Map();
  const updates = [];
  const calls = [];
  const timers = new Set();
  const originalSet = global.setInterval;
  const originalClear = global.clearInterval;
  global.setInterval = (callback, delay) => {
    const timer = { callback, delay, unref() {} };
    timers.add(timer);
    return timer;
  };
  global.clearInterval = (timer) => timers.delete(timer);
  t.after(() => {
    global.setInterval = originalSet;
    global.clearInterval = originalClear;
  });
  const pi = {
    on: (name, handler) => handlers.set(name, handler),
    exec: (...args) => { calls.push(args); return exec(...args); },
  };
  const context = { mode: "tui", cwd: "/tmp/project with spaces", ui: {
    setStatus: (...args) => updates.push(args),
  } };
  extension(pi);
  return { handlers, updates, calls, timers, context };
}

test("does nothing before a TUI session starts", async (t) => {
  const h = host(t);
  assert.equal(h.timers.size, 0);
  assert.equal(h.calls.length, 0);
  await h.handlers.get("session_start")({}, { ...h.context, mode: "json" });
  assert.equal(h.timers.size, 0);
  assert.equal(h.calls.length, 0);
  assert.equal(h.updates.length, 0);
});

test("adds a keyed footer status without replacing the existing footer", async (t) => {
  const h = host(t);
  await h.handlers.get("session_start")({}, h.context);
  assert.deepEqual(h.updates.at(-1), ["yoyo", "YOYO · idle"]);
  assert.deepEqual(h.calls[0][1], ["status", "--cwd", h.context.cwd, "--width", "120"]);
  assert.equal(h.calls[0][2].timeout, 2000);
  assert.equal(h.timers.size, 1);
  await h.handlers.get("session_shutdown")({}, h.context);
  assert.equal(h.timers.size, 0);
  assert.deepEqual(h.updates.at(-1), ["yoyo", undefined]);
});

test("polls without overlapping and aborts on shutdown", async (t) => {
  let resolve;
  const h = host(t, () => new Promise((done) => { resolve = done; }));
  const started = h.handlers.get("session_start")({}, h.context);
  assert.equal(h.calls.length, 1);
  const timer = [...h.timers][0];
  await timer.callback();
  assert.equal(h.calls.length, 1);
  const signal = h.calls[0][2].signal;
  await h.handlers.get("session_shutdown")({}, h.context);
  assert.equal(signal.aborted, true);
  resolve({ code: 0, stdout: "OLD RESULT", killed: false });
  await started;
  assert.equal(h.updates.some((row) => row[1] === "OLD RESULT"), false);
});

test("replaces timers and ignores results from an old session", async (t) => {
  const pending = [];
  const h = host(t, () => new Promise((done) => pending.push(done)));
  const first = h.handlers.get("session_start")({}, h.context);
  const next = { ...h.context, cwd: "/tmp/new-project" };
  const second = h.handlers.get("session_start")({}, next);
  assert.equal(h.timers.size, 1);
  pending[1]({ code: 0, stdout: "NEW RESULT", killed: false });
  await second;
  pending[0]({ code: 0, stdout: "OLD RESULT", killed: false });
  await first;
  assert.deepEqual(h.updates.at(-1), ["yoyo", "NEW RESULT"]);
  await h.handlers.get("session_shutdown")({}, next);
});

test("reports command failure without claiming idle", async (t) => {
  const h = host(t, async () => { throw new Error("missing binary"); });
  await h.handlers.get("session_start")({}, h.context);
  assert.deepEqual(h.updates.at(-1), ["yoyo", "YOYO · status unavailable"]);
  await h.handlers.get("session_shutdown")({}, h.context);
});
