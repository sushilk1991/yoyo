import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

// The host owns rendering: journals -> yoyo status -> keyed Pi footer text.
const REFRESH_MS = 2000;
const COMMAND_TIMEOUT_MS = 2000;
const STATUS_WIDTH = 120;
const STATUS_KEY = "yoyo";
const UNAVAILABLE = "YOYO · status unavailable";

export default function (pi: ExtensionAPI) {
  let timer: ReturnType<typeof setInterval> | undefined;
  let context: ExtensionContext | undefined;
  let controller: AbortController | undefined;
  let busy = false;
  let generation = 0;

  // Reload and session switches invalidate in-flight work before clearing the UI.
  function stop() {
    generation++;
    if (timer) {
      clearInterval(timer);
      timer = undefined;
    }
    controller?.abort();
    controller = undefined;
    context?.ui.setStatus(STATUS_KEY, undefined);
    context = undefined;
    busy = false;
  }

  // Poll out of band without overlapping commands or letting old results repaint.
  async function refresh() {
    if (busy || !context) {
      return;
    }
    busy = true;
    const ticket = generation;
    const current = context;
    controller = new AbortController();
    try {
      const result = await pi.exec(
        process.env.YOYO_STATUS_BIN || "yoyo",
        ["status", "--cwd", current.cwd, "--width", String(STATUS_WIDTH)],
        { timeout: COMMAND_TIMEOUT_MS, signal: controller.signal },
      );
      if (ticket !== generation) {
        return;
      }
      const text = result.stdout.trim();
      current.ui.setStatus(STATUS_KEY, result.code === 0 && !result.killed && text ? text : UNAVAILABLE);
    } catch {
      if (ticket === generation) {
        current.ui.setStatus(STATUS_KEY, UNAVAILABLE);
      }
    } finally {
      if (ticket === generation) {
        busy = false;
        controller = undefined;
      }
    }
  }

  // Timers start only in a real TUI session; print/RPC/discovery remain untouched.
  pi.on("session_start", async (_event, ctx) => {
    stop();
    if (ctx.mode !== "tui") {
      return;
    }
    context = ctx;
    timer = setInterval(refresh, REFRESH_MS);
    timer.unref();
    await refresh();
  });

  pi.on("session_shutdown", () => {
    stop();
  });
}
