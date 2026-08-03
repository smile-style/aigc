document.addEventListener("DOMContentLoaded", () => {
  const workspace = document.querySelector("[data-publishing-status-url]");
  if (!(workspace instanceof HTMLElement)) return;

  const activeStatuses = new Set(["queued", "running", "retry_wait", "submitted"]);
  let timer = null;

  const schedule = (delay = 5000) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(poll, delay);
  };

  async function poll() {
    if (document.hidden) {
      schedule();
      return;
    }

    const rows = [...workspace.querySelectorAll("[data-publishing-task]")];
    const active = rows.filter((row) => activeStatuses.has(row.dataset.status));
    if (!active.length) return;

    try {
      const ids = active.map((row) => row.dataset.publishingTask).join(",");
      const response = await fetch(
        `${workspace.dataset.publishingStatusUrl}?ids=${encodeURIComponent(ids)}`,
        { headers: { Accept: "application/json" }, cache: "no-store" }
      );
      if (!response.ok) throw new Error("status request failed");
      const payload = await response.json();

      for (const task of payload.tasks) {
        const row = workspace.querySelector(`[data-publishing-task='${task.id}']`);
        if (!row) continue;
        if (row.dataset.status !== task.status && !activeStatuses.has(task.status)) {
          window.location.reload();
          return;
        }
        row.dataset.status = task.status;
        const progress = row.querySelector("[data-task-progress]");
        if (progress) progress.value = task.progress;
        const percent = row.querySelector("[data-task-percent]");
        if (percent) percent.textContent = `${task.progress}%`;
        const stage = row.querySelector("[data-task-stage]");
        if (stage) stage.textContent = task.stage_label;
        const status = row.querySelector("[data-task-status]");
        if (status) status.textContent = task.status_label;
        const error = row.querySelector("[data-task-error]");
        if (error && task.error) error.textContent = task.error;
      }
    } catch (_error) {
      // Keep the current state visible and retry.
    }
    schedule();
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) schedule(0);
  });
  window.addEventListener("beforeunload", () => window.clearTimeout(timer), { once: true });
  schedule(1000);
});
