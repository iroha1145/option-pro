/* Optix Pro Night Desk — 持久 AI 任务协调器
   任务只由明确点击创建。轮询使用 deck-api 的独立低优先级通道。 */
(function () {
  "use strict";

  const POLL_SECONDS = [2, 3, 5, 8, 10];
  const TERMINAL = new Set([
    "completed", "failed", "cancelled", "insufficient_context", "budget_blocked",
    "incomplete_output", "submission_outcome_unknown", "worker_interrupted",
  ]);
  const ACTIVE = new Set(["pending", "queued", "in_progress", "processing", "running", "cancel_requested"]);
  const tasks = new Map();

  const unwrap = payload => payload && typeof payload === "object"
    ? (payload.job && typeof payload.job === "object" ? payload.job
      : payload.data && typeof payload.data === "object" && (payload.data.job_id || payload.data.status) ? payload.data
        : payload)
    : {};

  function normalizeStatus(value) {
    const status = String(value || "pending").toLowerCase();
    if (status === "processing" || status === "running") return "in_progress";
    if (status === "canceled") return "cancelled";
    if (status === "complete" || status === "succeeded" || status === "success") return "completed";
    return status;
  }

  function normalize(payload) {
    const raw = unwrap(payload);
    const rawStatus = normalizeStatus(raw.status || raw.state || (payload && payload.status));
    const status = raw.cancel_requested && ["pending", "queued", "in_progress"].includes(rawStatus) ? "cancel_requested" : rawStatus;
    return Object.assign({}, raw, {
      job_id: raw.job_id || raw.id || (payload && payload.job_id) || null,
      status,
      result: raw.result !== undefined ? raw.result : (payload && payload.result),
    });
  }

  const abortError = error => !!error && (error.name === "AbortError" || error.code === 20);

  function delay(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { reject(new DOMException("任务轮询已停止", "AbortError")); return; }
      const timer = setTimeout(resolve, ms);
      signal.addEventListener("abort", () => {
        clearTimeout(timer);
        reject(new DOMException("任务轮询已停止", "AbortError"));
      }, { once: true });
    });
  }

  function emit(task, kind, value) {
    if (task.stopped) return;
    const fn = task.config[kind];
    if (typeof fn === "function") fn(value, task.publicHandle);
  }

  function finish(task, job) {
    if (task.stopped) return;
    task.last = job;
    emit(task, "onComplete", job);
    task.stopped = true;
    task.controller.abort();
    if (tasks.get(task.scope) === task) tasks.delete(task.scope);
  }

  async function pollLoop(task) {
    let attempt = 0;
    while (!task.stopped) {
      const base = POLL_SECONDS[Math.min(attempt, POLL_SECONDS.length - 1)];
      const waitSeconds = document.hidden ? Math.max(20, base * 3) : base;
      await delay(waitSeconds * 1000, task.controller.signal);
      if (task.stopped) return;
      const payload = await task.config.poll(task.jobId, task.controller.signal);
      const job = normalize(payload);
      task.last = job;
      emit(task, "onUpdate", job);
      if (TERMINAL.has(job.status)) { finish(task, job); return; }
      attempt += 1;
    }
  }

  async function run(task) {
    try {
      let job;
      if (task.initial) {
        job = normalize(task.initial);
      } else {
        emit(task, "onUpdate", { status: "pending", job_id: null });
        job = normalize(await task.config.create(task.controller.signal));
      }
      if (task.stopped) return;
      task.last = job;
      task.jobId = job.job_id;
      emit(task, "onUpdate", job);
      if (TERMINAL.has(job.status)) { finish(task, job); return; }
      if (!task.jobId) throw new Error("服务端未返回任务编号");
      await pollLoop(task);
    } catch (error) {
      if (abortError(error) || task.stopped) return;
      task.lastError = error;
      emit(task, "onError", error);
      task.stopped = true;
      task.controller.abort();
      if (tasks.get(task.scope) === task) tasks.delete(task.scope);
    }
  }

  function makeTask(config, initial) {
    if (!config || typeof config.poll !== "function") throw new TypeError("任务缺少轮询函数");
    if (!initial && typeof config.create !== "function") throw new TypeError("任务缺少创建函数");
    const scope = String(config.scope || "default");
    stop(scope);
    const task = {
      scope,
      config,
      initial,
      controller: new AbortController(),
      stopped: false,
      jobId: null,
      last: null,
      lastError: null,
      publicHandle: null,
    };
    task.publicHandle = {
      scope,
      stop: () => stop(scope),
      cancel: () => cancel(scope),
      get jobId() { return task.jobId; },
      get state() { return task.last; },
    };
    tasks.set(scope, task);
    run(task);
    return task.publicHandle;
  }

  function start(config) { return makeTask(config, null); }

  function watch(initial, config) { return makeTask(config, initial); }

  function stop(scope) {
    const task = tasks.get(String(scope || "default"));
    if (!task) return false;
    task.stopped = true;
    task.controller.abort();
    tasks.delete(task.scope);
    return true;
  }

  function stopPrefix(prefix) {
    for (const scope of Array.from(tasks.keys())) if (scope.startsWith(prefix)) stop(scope);
  }

  function stopAll() {
    for (const scope of Array.from(tasks.keys())) stop(scope);
  }

  async function cancel(scope) {
    const task = tasks.get(String(scope || "default"));
    if (!task || !task.jobId || typeof task.config.cancel !== "function") return null;
    task.stopped = true;
    task.controller.abort();
    try {
      const payload = await task.config.cancel(task.jobId);
      const job = normalize(payload);
      tasks.delete(task.scope);
      if (!TERMINAL.has(job.status)) {
        makeTask(task.config, job);
        return job;
      }
      task.stopped = false;
      task.last = job;
      emit(task, "onUpdate", job);
      finish(task, job);
      return job;
    } finally {
      task.stopped = true;
      if (tasks.get(task.scope) === task) tasks.delete(task.scope);
    }
  }

  function elapsed(job) {
    const started = job && (job.started_at || job.created_at || job.submitted_at);
    if (!started) return null;
    const seconds = Math.floor((Date.now() - new Date(started).getTime()) / 1000);
    return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
  }

  window.OPTIX_AI_JOBS = {
    start, watch, stop, stopPrefix, stopAll, cancel, normalize, normalizeStatus, elapsed,
    isTerminal: status => TERMINAL.has(normalizeStatus(status)),
    isActive: status => ACTIVE.has(normalizeStatus(status)),
    pollSchedule: POLL_SECONDS.slice(),
  };
})();
