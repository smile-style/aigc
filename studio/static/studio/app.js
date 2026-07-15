document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-submit-guard]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const scriptContext = form.closest("[data-script-id]");
      if (scriptContext instanceof HTMLElement && !form.elements.namedItem("script_id")) {
        const scriptInput = document.createElement("input");
        scriptInput.type = "hidden";
        scriptInput.name = "script_id";
        scriptInput.value = scriptContext.dataset.scriptId || "";
        form.appendChild(scriptInput);
      }

      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "true";
      const clickedButton = event.submitter instanceof HTMLButtonElement ? event.submitter : null;
      if (clickedButton?.name) {
        const submitValue = document.createElement("input");
        submitValue.type = "hidden";
        submitValue.name = clickedButton.name;
        submitValue.value = clickedButton.value;
        form.appendChild(submitValue);
      }
      const submitButtons = form.querySelectorAll('button[type="submit"]');
      submitButtons.forEach((button) => {
        if (!button.dataset.originalText) button.dataset.originalText = button.textContent.trim();
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

  const openModal = (modal) => {
    if (!modal) return;
    if (modal.parentElement !== document.body) document.body.appendChild(modal);
    modal.hidden = false;
    const scrollArea = modal.querySelector("[data-outline-detail-scroll], .modal-script");
    if (scrollArea instanceof HTMLElement) scrollArea.scrollTop = 0;
    document.body.classList.add("modal-open");
    const closeButton = modal.querySelector("[data-modal-close], [data-outline-detail-close]");
    if (closeButton instanceof HTMLElement) closeButton.focus();
  };

  document.querySelectorAll("[data-outline-detail-target], [data-modal-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.modalTarget || button.dataset.outlineDetailTarget;
      openModal(document.getElementById(target));
    });
  });

  document.querySelectorAll("[data-outline-detail-close], [data-modal-close]").forEach((button) => {
    button.addEventListener("click", () => closeModal(button.closest(".detail-modal")));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal(document.querySelector(".detail-modal:not([hidden])"));
  });

  const autoOpenModal = document.querySelector(".detail-modal[data-auto-open]");
  if (autoOpenModal instanceof HTMLElement) openModal(autoOpenModal);

  document.querySelectorAll("[data-task-poller]").forEach((taskPoller) => {
    if (!(taskPoller instanceof HTMLElement)) return;
    const endpoint = taskPoller.dataset.taskUrl;
    const label = taskPoller.querySelector("[data-task-label]");
    const detail = taskPoller.querySelector("[data-task-detail]");
    const names = {
      "episode-script": "本集剧本",
      storyboard: "本集分镜",
      "character-profile": "角色设定",
      "character-image": "角色原图",
    };
    const taskName = names[taskPoller.dataset.taskKind] || "生成任务";
    let pollTimer = null;

    const schedulePoll = (delay = 2000) => {
      pollTimer = window.setTimeout(pollTask, delay);
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
          if (label) label.textContent = `${taskName}生成完成`;
          if (detail) detail.textContent = "正在载入最新结果...";
          window.location.assign(task.result_url || window.location.href);
          return;
        }
        if (task.status === "failed" || task.status === "cancelled") {
          window.location.reload();
          return;
        }
        if (label) label.textContent = task.status === "pending" ? "任务已提交" : `正在生成${taskName}`;
        schedulePoll();
      } catch (error) {
        if (label) label.textContent = "正在重新连接";
        if (detail) detail.textContent = "暂时无法获取任务状态，稍后会自动重试。";
        schedulePoll(4000);
      }
    }

    schedulePoll(500);
    window.addEventListener("beforeunload", () => window.clearTimeout(pollTimer), { once: true });
  });
});
