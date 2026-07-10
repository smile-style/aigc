document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-submit-guard]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      form.dataset.submitting = "true";
      const clickedButton = event.submitter instanceof HTMLButtonElement ? event.submitter : null;
      const submitButtons = form.querySelectorAll('button[type="submit"]');

      submitButtons.forEach((button) => {
        if (!button.dataset.originalText) {
          button.dataset.originalText = button.textContent.trim();
        }
        button.disabled = true;
      });

      const loadingButton = clickedButton || submitButtons[0];
      if (loadingButton) {
        loadingButton.textContent =
          loadingButton.dataset.loadingText || loadingButton.dataset.originalText || "提交中...";
      }
    });
  });

  const closeModal = (modal) => {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  };

  document.querySelectorAll("[data-outline-detail-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const modal = document.getElementById(button.dataset.outlineDetailTarget);
      if (!modal) return;
      if (modal.parentElement !== document.body) {
        document.body.appendChild(modal);
      }
      modal.hidden = false;
      const scrollArea = modal.querySelector("[data-outline-detail-scroll]");
      if (scrollArea instanceof HTMLElement) {
        scrollArea.scrollTop = 0;
      }
      document.body.classList.add("modal-open");
      const closeButton = modal.querySelector("[data-outline-detail-close]");
      if (closeButton instanceof HTMLElement) {
        closeButton.focus();
      }
    });
  });

  document.querySelectorAll("[data-outline-detail-close]").forEach((button) => {
    button.addEventListener("click", () => closeModal(button.closest(".detail-modal")));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const openModal = document.querySelector(".detail-modal:not([hidden])");
    closeModal(openModal);
  });

  const taskPoller = document.querySelector("[data-task-poller]");
  if (taskPoller instanceof HTMLElement) {
    const endpoint = taskPoller.dataset.taskUrl;
    const label = taskPoller.querySelector("[data-task-label]");
    const detail = taskPoller.querySelector("[data-task-detail]");
    const taskKind = taskPoller.dataset.taskKind || "storyboard";
    const taskName = taskKind === "episode-script" ? "本集剧本" : "本集分镜";
    const runningText = taskKind === "episode-script" ? "正在生成本集剧本" : "正在生成本集分镜";
    let pollTimer = null;

    const schedulePoll = (delay = 2000) => {
      pollTimer = window.setTimeout(pollTask, delay);
    };


    const showTerminalState = (title, message, className) => {
      taskPoller.classList.remove("is-running");
      taskPoller.classList.add(className);
      if (label) label.textContent = title;
      if (detail) detail.textContent = message;
    };

    async function pollTask() {
      if (!endpoint) return;
      try {
        const response = await fetch(endpoint, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`Task status request failed: ${response.status}`);

        const task = await response.json();
        if (task.status === "succeeded") {
          showTerminalState(`${taskName}生成完成`, "正在载入最新结果...", "is-success");
          window.location.assign(task.result_url || window.location.href);
          return;
        }
        if (task.status === "failed" || task.status === "cancelled") {
          const title = task.status === "cancelled" ? `${taskName}任务已取消` : `${taskName}生成失败`;
          showTerminalState(title, task.error_message || "请重试。", "is-error");
          window.location.reload();
          return;
        }

        if (label) label.textContent = task.status === "pending" ? "任务已提交" : runningText;
        if (detail) detail.textContent = "页面可以保持打开，完成后会自动显示结果。";
        schedulePoll();
      } catch (error) {
        if (label) label.textContent = "正在重新连接";
        if (detail) detail.textContent = "暂时无法获取任务状态，稍后会自动重试。";
        schedulePoll(4000);
      }
    }

    schedulePoll(500);
    window.addEventListener("beforeunload", () => window.clearTimeout(pollTimer), { once: true });
  }
});
