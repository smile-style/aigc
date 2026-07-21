document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = () => document.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";

  document.querySelectorAll("[data-publish-form]").forEach((form) => {
    const title = form.querySelector("input[name='title']");
    const counter = form.querySelector("[data-character-count]");
    const sourceField = form.querySelector("[data-source-field]");
    const sourceInput = sourceField?.querySelector("input");
    const error = form.querySelector("[data-publish-error]");
    const submit = form.querySelector("button[type='submit']");

    const updateTitleCount = () => { if (counter && title) counter.textContent = String(title.value.length); };
    const updateCopyright = () => {
      const repost = form.querySelector("input[name='copyright']:checked")?.value === "2";
      if (sourceField) sourceField.hidden = !repost;
      if (sourceInput) sourceInput.required = repost;
    };
    title?.addEventListener("input", updateTitleCount);
    form.querySelectorAll("input[name='copyright']").forEach((input) => input.addEventListener("change", updateCopyright));
    updateTitleCount();
    updateCopyright();

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      if (error) error.hidden = true;
      if (submit) { submit.disabled = true; submit.textContent = "正在检查..."; }
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json", "X-CSRFToken": csrfToken() },
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "无法创建发布任务。");
        window.location.assign("/publishing/tasks/");
      } catch (reason) {
        if (error) { error.textContent = reason.message; error.hidden = false; }
        if (submit) { submit.disabled = false; submit.textContent = "检查并发布"; }
      }
    });
  });

  const qrButton = document.querySelector("[data-qr-login]");
  if (qrButton instanceof HTMLElement) {
    let timer = null;
    qrButton.addEventListener("click", async () => {
      const modal = document.getElementById(qrButton.dataset.modalTarget);
      const image = modal?.querySelector("[data-login-qr]");
      const status = modal?.querySelector("[data-login-status]");
      if (status) status.textContent = "正在创建二维码...";
      try {
        const response = await fetch(qrButton.dataset.qrLogin, {
          method: "POST",
          headers: { Accept: "application/json", "X-CSRFToken": csrfToken() },
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "无法创建登录二维码。");
        if (image) { image.src = payload.qr_url; image.hidden = false; }
        if (status) status.textContent = "请使用哔哩哔哩客户端扫码。";
        clearInterval(timer);
        timer = setInterval(async () => {
          const pollResponse = await fetch(payload.status_url, { headers: { Accept: "application/json" } });
          if (!pollResponse.ok) return;
          const poll = await pollResponse.json();
          if (status) status.textContent = poll.message || poll.label;
          if (["succeeded", "expired", "failed"].includes(poll.status)) clearInterval(timer);
          if (poll.status === "succeeded") window.location.assign("/system/?publishing_success=1");
        }, 2000);
      } catch (reason) {
        if (status) status.textContent = reason.message;
      }
    });
  }

  const workspace = document.querySelector("[data-publishing-status-url]");
  if (workspace instanceof HTMLElement) {
    let stopped = false;
    const poll = async () => {
      if (stopped) return;
      const rows = [...workspace.querySelectorAll("[data-publishing-task]")];
      const active = rows.filter((row) => ["queued", "running", "retry_wait"].includes(row.dataset.status));
      if (!active.length) return;
      const ids = active.map((row) => row.dataset.publishingTask).join(",");
      try {
        const response = await fetch(`${workspace.dataset.publishingStatusUrl}?ids=${encodeURIComponent(ids)}`);
        if (!response.ok) throw new Error("status request failed");
        const payload = await response.json();
        payload.tasks.forEach((task) => {
          const row = workspace.querySelector(`[data-publishing-task='${task.id}']`);
          if (!row) return;
          if (row.dataset.status !== task.status && task.terminal) {
            window.location.reload();
            stopped = true;
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
        });
      } catch (_) {
        // Keep the last known state; the next poll can recover.
      } finally {
        if (!stopped) window.setTimeout(poll, document.hidden ? 15000 : 2000);
      }
    };
    poll();
  }
});
