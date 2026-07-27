document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-episode-workflow]").forEach((panel) => {
    if (!(panel instanceof HTMLElement)) return;
    const endpoint = panel.dataset.workflowUrl;
    const initialStatus = panel.dataset.workflowStatus;
    if (!endpoint || !["queued", "running"].includes(initialStatus || "")) return;
    let timer = null;

    const poll = async () => {
      try {
        const response = await fetch(endpoint, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`status ${response.status}`);
        const payload = await response.json();
        const progress = panel.querySelector("[data-workflow-progress]");
        const percent = panel.querySelector("[data-workflow-percent]");
        const label = panel.querySelector("[data-workflow-label]");
        const detail = panel.querySelector("[data-workflow-detail]");
        if (progress instanceof HTMLProgressElement) {
          progress.value = payload.progress_percent;
        }
        if (percent) percent.textContent = `${payload.progress_percent}%`;
        if (label) label.textContent = payload.stage_label;
        if (detail) {
          const ready = payload.details?.video_ready;
          const total = payload.details?.video_total;
          detail.textContent =
            payload.auto_retrying
              ? `\u6b63\u5728\u81ea\u52a8\u91cd\u8bd5 ${payload.auto_retry_count}/${payload.auto_retry_max}\uff1a${payload.auto_retry_message}`
              : payload.stage === "videos" && total
              ? `\u5df2\u5b8c\u6210 ${ready || 0} / ${total} \u4e2a\u955c\u5934`
              : payload.error_message ||
                "\u53ef\u4ee5\u79bb\u5f00\u9875\u9762\uff0c\u540e\u53f0\u4f1a\u7ee7\u7eed\u6267\u884c\u3002";
        }
        payload.steps.forEach((step) => {
          const item = panel.querySelector(`[data-workflow-step="${step.key}"]`);
          if (!item) return;
          item.className = `is-${step.status}`;
        });
        panel.dataset.workflowStatus = payload.status;
        panel.classList.remove("is-queued", "is-running", "is-failed", "is-succeeded");
        panel.classList.add(`is-${payload.status}`);
        if (payload.terminal) {
          window.setTimeout(() => window.location.reload(), 500);
          return;
        }
      } catch (_) {
        const detail = panel.querySelector("[data-workflow-detail]");
        if (detail) {
          detail.textContent =
            "\u6682\u65f6\u65e0\u6cd5\u83b7\u53d6\u8fdb\u5ea6\uff0c\u7a0d\u540e\u81ea\u52a8\u91cd\u8bd5\u3002";
        }
      }
      timer = window.setTimeout(poll, document.hidden ? 10000 : 2000);
    };

    timer = window.setTimeout(poll, 500);
    window.addEventListener("beforeunload", () => window.clearTimeout(timer), {
      once: true,
    });
  });
});
