document.addEventListener("DOMContentLoaded", () => {
  const createTaskSteps = () => {
    const steps = document.createElement("ol");
    steps.className = "task-steps";
    steps.dataset.taskSteps = "";
    [
      ["created", "生成任务"],
      ["model", "等待大模型返回"],
      ["result", "保存结果"],
    ].forEach(([key, text], index) => {
      const item = document.createElement("li");
      item.dataset.taskStep = key;
      const number = document.createElement("span");
      number.textContent = String(index + 1);
      const label = document.createElement("b");
      label.textContent = text;
      item.append(number, label);
      steps.appendChild(item);
    });
    return steps;
  };

  const updateTaskSteps = (root, stage, status) => {
    let steps = root.querySelector("[data-task-steps]");
    if (!(steps instanceof HTMLElement)) {
      steps = createTaskSteps();
      root.querySelector("div")?.appendChild(steps);
    }
    const order = ["created", "model", "result"];
    const activeIndex = stage === "result" ? 2 : stage === "model" || stage === "failed" ? 1 : 0;
    steps.querySelectorAll("[data-task-step]").forEach((item, index) => {
      item.classList.toggle("is-complete", index < activeIndex || status === "succeeded");
      item.classList.toggle("is-current", index === activeIndex && status !== "succeeded");
      item.classList.toggle("is-error", index === activeIndex && (status === "failed" || status === "cancelled"));
    });
  };

  const showSubmitProgress = (form) => {
    if (!form.matches("[data-generation-form]") && !form.action.includes("/generate/")) return;
    document.querySelector(".generation-toast")?.remove();
    const toast = document.createElement("section");
    toast.className = "generation-toast task-status is-running";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    const indicator = document.createElement("span");
    indicator.className = "task-indicator";
    indicator.setAttribute("aria-hidden", "true");
    const content = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = "正在创建生成任务";
    const detail = document.createElement("p");
    detail.textContent = "正在整理输入并提交任务。";
    content.append(title, detail, createTaskSteps());
    toast.append(indicator, content);
    document.body.appendChild(toast);
    updateTaskSteps(toast, "created", "pending");
    window.setTimeout(() => {
      if (!toast.isConnected) return;
      title.textContent = "等待大模型返回";
      detail.textContent = "请求已发送，正在等待大模型生成结果。";
      updateTaskSteps(toast, "model", "running");
    }, 180);
  };

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
      if (loadingButton) loadingButton.textContent = loadingButton.dataset.loadingText || "正在创建任务...";
      showSubmitProgress(form);
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
    modal.querySelector("[data-modal-close], [data-outline-detail-close]")?.focus();
  };
  document.querySelectorAll("[data-outline-detail-target], [data-modal-target]").forEach((button) => {
    button.addEventListener("click", () => openModal(document.getElementById(button.dataset.modalTarget || button.dataset.outlineDetailTarget)));
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
      "series-script": "剧集规划",
      "episode-script": "本集剧本",
      storyboard: "本集分镜",
      "character-profile": "角色设定",
      "character-image": "角色原图",
      "cover-image": "封面母版",
    };
    const taskName = names[taskPoller.dataset.taskKind] || "生成任务";
    let pollTimer = null;
    const schedulePoll = (delay = 2000) => { pollTimer = window.setTimeout(pollTask, delay); };

    async function pollTask() {
      if (!endpoint) return;
      try {
        const response = await fetch(endpoint, { headers: { Accept: "application/json" }, cache: "no-store" });
        if (!response.ok) throw new Error(`Task status request failed: ${response.status}`);
        const task = await response.json();
        updateTaskSteps(taskPoller, task.stage, task.status);
        if (label) label.textContent = task.stage_label || `${taskName}处理中`;
        if (detail) detail.textContent = task.stage_detail || "正在获取最新状态。";
        if (task.status === "succeeded") {
          taskPoller.classList.remove("is-running");
          taskPoller.classList.add("is-success");
          window.setTimeout(() => window.location.assign(task.result_url || window.location.href), 350);
          return;
        }
        if (task.status === "failed" || task.status === "cancelled") {
          taskPoller.classList.remove("is-running");
          taskPoller.classList.add("is-error");
          if (label) label.textContent = `${taskName}生成失败`;
          if (detail) detail.textContent = task.error_message || task.stage_detail || "生成过程中发生未知错误，请重试。";
          if (!taskPoller.querySelector(".task-refresh-link")) {
            const refresh = document.createElement("a");
            refresh.className = "secondary-link task-refresh-link";
            refresh.href = window.location.href;
            refresh.textContent = "刷新页面后重试";
            taskPoller.querySelector("div")?.appendChild(refresh);
          }
          return;
        }
        schedulePoll(task.status === "retry_wait" ? 3500 : 2000);
      } catch (error) {
        if (label) label.textContent = "正在重新连接";
        if (detail) detail.textContent = "暂时无法获取任务状态，稍后会自动重试。";
        schedulePoll(4000);
      }
    }
    updateTaskSteps(taskPoller, "created", "pending");
    schedulePoll(300);
    window.addEventListener("beforeunload", () => window.clearTimeout(pollTimer), { once: true });
  });

  const sidebar = document.querySelector("[data-sidebar]");
  sidebar?.querySelectorAll("a[href]").forEach((link) => {
    link.addEventListener("click", () => {
      sidebar.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("is-active"));
      if (link.classList.contains("nav-item")) link.classList.add("is-active");
      sidebar.setAttribute("aria-busy", "true");
    });
  });
});
