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

  const modalTriggers = new WeakMap();
  const focusableSelector = [
    'a[href]',
    'button:not([disabled]):not([tabindex="-1"])',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(",");
  const openModals = () => Array.from(document.querySelectorAll(".detail-modal:not([hidden])"));
  const topModal = () => openModals().at(-1) || null;
  const modalFocusables = (modal) => Array.from(modal.querySelectorAll(focusableSelector)).filter(
    (element) => !element.closest("[hidden]") && element.getAttribute("aria-hidden") !== "true",
  );
  const syncModalState = () => document.body.classList.toggle("modal-open", openModals().length > 0);
  const closeModal = (modal) => {
    if (!(modal instanceof HTMLElement) || modal.hidden) return;
    modal.hidden = true;
    const trigger = modalTriggers.get(modal);
    modalTriggers.delete(modal);
    syncModalState();
    if (trigger instanceof HTMLElement && trigger.isConnected) {
      trigger.focus();
      return;
    }
    const remainingModal = topModal();
    remainingModal?.querySelector(".modal-panel")?.focus();
  };
  const openModal = (modal, trigger = null) => {
    if (!(modal instanceof HTMLElement)) return;
    document.body.appendChild(modal);
    if (trigger instanceof HTMLElement) modalTriggers.set(modal, trigger);
    modal.hidden = false;
    const scrollArea = modal.querySelector(
      "[data-script-modal-scroll], [data-outline-detail-scroll], .modal-script, .llm-request-view",
    );
    if (scrollArea instanceof HTMLElement) scrollArea.scrollTop = 0;
    syncModalState();
    const initialFocus = modal.querySelector(
      "[data-modal-initial-focus], [data-modal-close], [data-outline-detail-close], [data-llm-request-close]",
    );
    window.requestAnimationFrame(() => (initialFocus || modal.querySelector(".modal-panel"))?.focus());
  };
  document.querySelectorAll("[data-outline-detail-target], [data-modal-target]").forEach((button) => {
    button.addEventListener("click", () => openModal(
      document.getElementById(button.dataset.modalTarget || button.dataset.outlineDetailTarget),
      button,
    ));
  });
  document.querySelectorAll("[data-outline-detail-close], [data-modal-close], [data-llm-request-close]").forEach((button) => {
    button.addEventListener("click", () => closeModal(button.closest(".detail-modal")));
  });
  document.addEventListener("keydown", (event) => {
    const modal = topModal();
    if (!modal) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeModal(modal);
      return;
    }
    if (event.key !== "Tab") return;
    const focusables = modalFocusables(modal);
    if (!focusables.length) {
      event.preventDefault();
      modal.querySelector(".modal-panel")?.focus();
      return;
    }
    const first = focusables[0];
    const last = focusables.at(-1);
    const active = document.activeElement;
    if (!modal.contains(active)) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  });
  const autoOpenModal = document.querySelector(".detail-modal[data-auto-open]");
  if (autoOpenModal instanceof HTMLElement) openModal(autoOpenModal);

  const requestDialog = document.querySelector("[data-llm-request-dialog]");
  if (requestDialog instanceof HTMLElement) {
    const requestTitle = requestDialog.querySelector("[data-llm-request-title]");
    const historySelect = requestDialog.querySelector("[data-llm-request-history]");
    const metaList = requestDialog.querySelector("[data-llm-request-meta]");
    const recordError = requestDialog.querySelector("[data-llm-request-record-error]");
    const messagesRoot = requestDialog.querySelector("[data-llm-request-messages]");
    const rawRoot = requestDialog.querySelector("[data-llm-request-raw]");
    const copyButton = requestDialog.querySelector("[data-llm-request-copy]");
    const liveRegion = requestDialog.querySelector("[data-llm-request-live]");
    const errorMessage = requestDialog.querySelector("[data-llm-request-error]");
    const retryButton = requestDialog.querySelector("[data-llm-request-retry]");
    const viewButtons = Array.from(requestDialog.querySelectorAll("[data-llm-request-view]"));
    const viewPanels = Array.from(requestDialog.querySelectorAll("[data-llm-request-panel]"));
    let records = [];
    let activeListUrl = "";
    let selectedIndex = 0;
    let requestToken = 0;
    let retryMode = "list";
    let currentRawText = "";

    const announce = (message) => {
      if (!(liveRegion instanceof HTMLElement)) return;
      liveRegion.textContent = "";
      window.requestAnimationFrame(() => { liveRegion.textContent = message; });
    };
    const setRequestState = (state) => {
      requestDialog.querySelectorAll("[data-llm-request-state]").forEach((element) => {
        element.hidden = element.dataset.llmRequestState !== state;
      });
    };
    const setRequestView = (view) => {
      viewButtons.forEach((button) => {
        const isActive = button.dataset.llmRequestView === view;
        button.setAttribute("aria-selected", String(isActive));
        button.tabIndex = isActive ? 0 : -1;
      });
      viewPanels.forEach((panel) => { panel.hidden = panel.dataset.llmRequestPanel !== view; });
    };
    const normalizeList = (payload) => {
      if (Array.isArray(payload)) return payload;
      if (!payload || typeof payload !== "object") return [];
      return payload.requests || payload.results || payload.items || payload.records || [];
    };
    const unwrapDetail = (payload) => {
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) return payload;
      return payload.request || payload.record || payload.item || payload;
    };
    const requestPayload = (record) => {
      if (!record || typeof record !== "object") return undefined;
      let payload = record.sanitized_payload ?? record.payload ?? record.request_payload ?? record.request_body;
      if (payload === undefined && record.request && typeof record.request === "object") payload = record.request;
      if (typeof payload !== "string") return payload;
      try {
        return JSON.parse(payload);
      } catch (error) {
        return payload;
      }
    };
    const hasRequestPayload = (record) => requestPayload(record) !== undefined && requestPayload(record) !== null;
    const formatDate = (value) => {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(date);
    };
    const statusLabel = (status) => ({
      pending: "等待中",
      running: "请求中",
      succeeded: "成功",
      failed: "失败",
      cancelled: "已取消",
    }[status] || status || "");
    const historyLabel = (record, index) => {
      const attempt = record.task_attempt ?? record.attempt;
      const sequence = record.call_sequence ?? record.request_sequence;
      const callLabel = attempt || sequence
        ? `第 ${attempt || 1} 次 · 调用 ${sequence || 1}`
        : `记录 ${records.length - index}`;
      return [
        callLabel,
        record.request_kind_label || record.request_kind,
        record.model,
        formatDate(record.created_at),
        statusLabel(record.status),
      ].filter(Boolean).join(" · ");
    };
    const detailUrl = (record) => {
      const explicitUrl = record.detail_url || record.url;
      if (explicitUrl) return new URL(explicitUrl, window.location.href).toString();
      const identifier = record.id ?? record.pk;
      if (identifier === undefined || identifier === null || !activeListUrl) return "";
      const url = new URL(activeListUrl, window.location.href);
      url.pathname = `${url.pathname.replace(/\/+$/, "")}/${encodeURIComponent(identifier)}/`;
      url.search = "";
      return url.toString();
    };
    const fetchJson = async (url) => {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
      if (!response.ok) {
        throw new Error(payload?.error || payload?.detail || `请求失败（${response.status}）`);
      }
      return payload;
    };
    const addMeta = (label, value) => {
      if (!(metaList instanceof HTMLElement) || value === undefined || value === null || value === "") return;
      const item = document.createElement("div");
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = String(value);
      item.append(term, detail);
      metaList.appendChild(item);
    };
    const stringifyContent = (content) => {
      if (typeof content === "string") return content;
      if (content === undefined || content === null) return "";
      return JSON.stringify(content, null, 2);
    };
    const writeClipboard = async (value) => {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
        return;
      }
      const fallbackTextarea = document.createElement("textarea");
      fallbackTextarea.value = value;
      fallbackTextarea.setAttribute("readonly", "");
      fallbackTextarea.style.position = "fixed";
      fallbackTextarea.style.opacity = "0";
      document.body.appendChild(fallbackTextarea);
      try {
        fallbackTextarea.select();
        if (!document.execCommand("copy")) throw new Error("copy failed");
      } finally {
        fallbackTextarea.remove();
      }
    };
    const renderMessages = (payload) => {
      if (!(messagesRoot instanceof HTMLElement)) return;
      messagesRoot.replaceChildren();
      let messages = payload && typeof payload === "object" ? payload.messages : null;
      if (!Array.isArray(messages) && payload && typeof payload === "object" && Array.isArray(payload.input)) {
        messages = payload.input;
      }
      if (!Array.isArray(messages) || !messages.length) {
        messages = [{ role: "request", content: payload }];
      }
      const roleNames = {
        system: "System",
        developer: "Developer",
        user: "User",
        assistant: "Assistant",
        tool: "Tool",
        request: "Request",
      };
      messages.forEach((message, index) => {
        const block = document.createElement("article");
        block.className = "llm-request-message";
        const header = document.createElement("header");
        const role = document.createElement("strong");
        const order = document.createElement("span");
        const copyMessage = document.createElement("button");
        const content = document.createElement("pre");
        const roleValue = message && typeof message === "object" ? message.role : "request";
        role.textContent = roleNames[roleValue] || String(roleValue || "Message");
        order.textContent = String(index + 1).padStart(2, "0");
        const messageText = stringifyContent(
          message && typeof message === "object" && Object.hasOwn(message, "content") ? message.content : message,
        );
        content.textContent = messageText;
        copyMessage.type = "button";
        copyMessage.className = "llm-request-message-copy";
        copyMessage.textContent = "复制";
        copyMessage.title = `复制第 ${index + 1} 条消息`;
        copyMessage.setAttribute("aria-label", copyMessage.title);
        copyMessage.addEventListener("click", async () => {
          try {
            await writeClipboard(messageText);
            announce(`第 ${index + 1} 条消息已复制`);
          } catch (error) {
            announce("复制失败，请稍后重试");
          } finally {
            copyMessage.focus();
          }
        });
        header.append(role, order, copyMessage);
        block.append(header, content);
        messagesRoot.appendChild(block);
      });
    };
    const renderRecord = (record) => {
      const payload = requestPayload(record);
      currentRawText = typeof payload === "string" ? payload : JSON.stringify(payload ?? {}, null, 2);
      if (metaList instanceof HTMLElement) metaList.replaceChildren();
      addMeta("类型", record.purpose_label || record.request_kind_label || record.purpose || record.request_kind);
      addMeta("模型", record.model);
      addMeta("温度", record.temperature);
      addMeta("Prompt 版本", record.prompt_version);
      addMeta("状态", statusLabel(record.status));
      addMeta("时间", formatDate(record.created_at));
      const attempt = record.task_attempt ?? record.attempt;
      const sequence = record.call_sequence ?? record.request_sequence;
      addMeta("调用", attempt || sequence ? `第 ${attempt || 1} 次 / ${sequence || 1}` : "");
      if (recordError instanceof HTMLElement) {
        recordError.textContent = record.error_message ? `失败原因：${record.error_message}` : "";
        recordError.hidden = !record.error_message;
      }
      renderMessages(payload);
      if (rawRoot instanceof HTMLElement) rawRoot.textContent = currentRawText;
      if (copyButton instanceof HTMLButtonElement) copyButton.disabled = false;
      setRequestState("content");
    };
    const showRequestError = (error, mode) => {
      retryMode = mode;
      if (errorMessage instanceof HTMLElement) errorMessage.textContent = error.message || "请稍后重试。";
      if (copyButton instanceof HTMLButtonElement) copyButton.disabled = true;
      setRequestState("error");
    };
    const loadRecord = async (index) => {
      const record = records[index];
      if (!record) return;
      selectedIndex = index;
      if (historySelect instanceof HTMLSelectElement) historySelect.value = String(index);
      const token = ++requestToken;
      setRequestState("loading");
      if (copyButton instanceof HTMLButtonElement) copyButton.disabled = true;
      try {
        let detail = record;
        if (!hasRequestPayload(record)) {
          const url = detailUrl(record);
          if (!url) throw new Error("请求详情地址不可用。");
          const response = unwrapDetail(await fetchJson(url));
          detail = response && typeof response === "object" ? { ...record, ...response } : record;
        }
        if (token !== requestToken) return;
        renderRecord(detail);
      } catch (error) {
        if (token !== requestToken) return;
        showRequestError(error, "detail");
      }
    };
    const populateHistory = () => {
      if (!(historySelect instanceof HTMLSelectElement)) return;
      historySelect.replaceChildren();
      records.forEach((record, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = historyLabel(record, index);
        historySelect.appendChild(option);
      });
      historySelect.disabled = records.length < 2;
    };
    const loadRequestList = async () => {
      const token = ++requestToken;
      retryMode = "list";
      records = [];
      if (historySelect instanceof HTMLSelectElement) {
        historySelect.disabled = true;
        historySelect.replaceChildren(new Option("正在加载...", ""));
      }
      if (copyButton instanceof HTMLButtonElement) copyButton.disabled = true;
      setRequestState("loading");
      try {
        const payload = await fetchJson(activeListUrl);
        if (token !== requestToken) return;
        records = normalizeList(payload);
        if (!Array.isArray(records) || !records.length) {
          records = [];
          if (historySelect instanceof HTMLSelectElement) {
            historySelect.replaceChildren(new Option("没有历史记录", ""));
          }
          setRequestState("empty");
          return;
        }
        populateHistory();
        await loadRecord(0);
      } catch (error) {
        if (token !== requestToken) return;
        showRequestError(error, "list");
      }
    };
    const copyRawRequest = async () => {
      if (!currentRawText) return;
      try {
        await writeClipboard(currentRawText);
        announce("请求 JSON 已复制");
      } catch (error) {
        announce("复制失败，请稍后重试");
      } finally {
        copyButton?.focus();
      }
    };

    document.querySelectorAll("[data-llm-request-open]").forEach((trigger) => {
      trigger.addEventListener("click", () => {
        activeListUrl = trigger.dataset.llmRequestUrl || "";
        if (!activeListUrl) return;
        if (requestTitle instanceof HTMLElement) {
          requestTitle.textContent = trigger.dataset.llmRequestTitle || "模型请求";
        }
        setRequestView("messages");
        openModal(requestDialog, trigger);
        loadRequestList();
      });
    });
    historySelect?.addEventListener("change", () => loadRecord(Number(historySelect.value)));
    retryButton?.addEventListener("click", () => {
      if (retryMode === "detail" && records.length) loadRecord(selectedIndex);
      else loadRequestList();
    });
    copyButton?.addEventListener("click", copyRawRequest);
    viewButtons.forEach((button, index) => {
      button.addEventListener("click", () => setRequestView(button.dataset.llmRequestView));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        let nextIndex = index;
        if (event.key === "ArrowLeft") nextIndex = (index - 1 + viewButtons.length) % viewButtons.length;
        if (event.key === "ArrowRight") nextIndex = (index + 1) % viewButtons.length;
        if (event.key === "Home") nextIndex = 0;
        if (event.key === "End") nextIndex = viewButtons.length - 1;
        const nextButton = viewButtons[nextIndex];
        setRequestView(nextButton.dataset.llmRequestView);
        nextButton.focus();
      });
    });
  }

  document.querySelectorAll("[data-pacing-track]").forEach((track) => {
    const duration = Number(track.dataset.pacingDuration);
    if (!Number.isFinite(duration) || duration <= 0) return;
    track.querySelectorAll("[data-pacing-start][data-pacing-end]").forEach((segment) => {
      const start = Number(segment.dataset.pacingStart);
      const end = Number(segment.dataset.pacingEnd);
      if (!Number.isFinite(start) || !Number.isFinite(end)) return;
      const span = Math.max(1, end - start);
      segment.style.setProperty("--pacing-span", String(span));
      segment.classList.toggle(
        "is-compact",
        segment.classList.contains("pacing-segment-crisis_open") && span / duration < 0.08,
      );
    });
    track.querySelectorAll("[data-pacing-at]").forEach((marker) => {
      const at = Number(marker.dataset.pacingAt);
      if (!Number.isFinite(at)) return;
      const position = Math.min(100, Math.max(0, (at / duration) * 100));
      marker.style.setProperty("--pacing-position", `${position}%`);
    });
  });

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

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-confirm-submit]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirmSubmit || "Confirm this action?")) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, { capture: true });
  });

  document.querySelectorAll('form[action*="/shot/"][action*="/characters/"]').forEach((form) => {
    const choices = Array.from(form.querySelectorAll('input[name="character_ids"]'));
    if (!choices.length) return;
    const counter = document.createElement("p");
    counter.className = "character-limit-counter";
    counter.setAttribute("role", "status");
    counter.setAttribute("aria-live", "polite");
    form.querySelector(".character-choice-grid")?.after(counter);

    const updateLimit = () => {
      const selected = choices.filter((choice) => choice.checked).length;
      counter.textContent = `\u5df2\u9009 ${selected}/5`;
      counter.classList.toggle("is-limit", selected >= 5);
      choices.forEach((choice) => {
        const unavailable = choice.closest(".character-choice")?.classList.contains("is-disabled");
        choice.disabled = Boolean(unavailable || (selected >= 5 && !choice.checked));
      });
    };
    choices.forEach((choice) => choice.addEventListener("change", updateLimit));
    updateLimit();
  });
});

document.addEventListener("DOMContentLoaded", () => {
  const discoveryForm = document.querySelector("#discover-storyboard-characters form");
  if (!(discoveryForm instanceof HTMLFormElement)) return;
  const choices = Array.from(discoveryForm.querySelectorAll('input[name="character_names"]'));
  const submitButton = discoveryForm.querySelector('button[type="submit"]');
  const counter = document.createElement("p");
  counter.className = "character-limit-counter discovery-selection-counter";
  counter.setAttribute("role", "status");
  counter.setAttribute("aria-live", "polite");
  discoveryForm.querySelector(".character-discovery-list")?.after(counter);

  const updateSelection = () => {
    const selected = choices.filter((choice) => choice.checked).length;
    counter.textContent = `\u5df2\u9009 ${selected} \u4e2a\u89d2\u8272`;
    if (submitButton instanceof HTMLButtonElement) submitButton.disabled = selected === 0;
  };
  choices.forEach((choice) => choice.addEventListener("change", updateSelection));
  updateSelection();
});
