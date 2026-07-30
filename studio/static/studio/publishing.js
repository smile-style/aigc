document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = () => document.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";

  document.querySelectorAll("[data-publish-form]").forEach((form) => {
    const title = form.querySelector("input[name='title']");
    const counter = form.querySelector("[data-character-count]");
    const titleLimit = form.querySelector("[data-title-limit]");
    const account = form.querySelector("[data-publish-account]");
    const partition = form.querySelector("[data-publish-partition]");
    const partitionField = form.querySelector("[data-partition-field]");
    const platformLabel = form.closest(".publish-dialog")?.querySelector("[data-publish-platform-label]");
    const copyrightField = form.querySelector("[data-copyright-field]");
    const sourceField = form.querySelector("[data-source-field]");
    const sourceInput = sourceField?.querySelector("input");
    const error = form.querySelector("[data-publish-error]");
    const submit = form.querySelector("button[type='submit']");
    const platformCards = [];

    if (account instanceof HTMLSelectElement && partition instanceof HTMLSelectElement) {
      const accountLabel = account.closest("label");
      const partitionLabel = partition.closest("label");
      const grid = form.querySelector(".publish-form-grid");
      const platformGrid = document.createElement("div");
      platformGrid.className = "publish-platform-grid field-wide";

      [
        { key: "bilibili", label: "Bilibili", partitionLabel: "Bilibili \u5206\u533a" },
        { key: "acfun", label: "AcFun", partitionLabel: "AcFun \u5206\u533a" },
      ].forEach((definition) => {
        const sourceOptions = [...account.options].filter(
          (option) => option.dataset.platform === definition.key
        );
        if (!sourceOptions.length) return;

        const card = document.createElement("fieldset");
        card.className = "publish-platform-card";
        const toggleLabel = document.createElement("label");
        toggleLabel.className = "publish-platform-toggle";
        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        const toggleText = document.createElement("span");
        toggleText.textContent = definition.label;
        toggleLabel.append(toggle, toggleText);

        const accountField = document.createElement("label");
        accountField.className = "publish-platform-field";
        const accountCaption = document.createElement("span");
        accountCaption.textContent = "\u53d1\u5e03\u8d26\u53f7";
        const accountSelect = document.createElement("select");
        accountSelect.name = "account_ids";
        accountSelect.disabled = true;
        accountSelect.append(new Option("\u8bf7\u9009\u62e9\u8d26\u53f7", ""));
        sourceOptions.forEach((option) => accountSelect.append(option.cloneNode(true)));
        accountField.append(accountCaption, accountSelect);

        const partitionFieldNode = document.createElement("label");
        partitionFieldNode.className = "publish-platform-field";
        const partitionCaption = document.createElement("span");
        partitionCaption.textContent = definition.partitionLabel;
        const partitionSelect = document.createElement("select");
        partitionSelect.name = `tid_${definition.key}`;
        partitionSelect.disabled = true;
        [...partition.options]
          .filter((option) => option.dataset.platform === definition.key)
          .forEach((option) => partitionSelect.append(option.cloneNode(true)));
        partitionFieldNode.append(partitionCaption, partitionSelect);

        toggle.addEventListener("change", () => {
          accountSelect.disabled = !toggle.checked;
          accountSelect.required = toggle.checked;
          partitionSelect.disabled = !toggle.checked;
          partitionSelect.required = toggle.checked;
          card.classList.toggle("is-selected", toggle.checked);
          updatePlatform();
        });
        card.append(toggleLabel, accountField, partitionFieldNode);
        platformGrid.append(card);
        platformCards.push({ toggle, accountSelect, partitionSelect });
      });
      if (platformCards.length && grid) {
        grid.insertBefore(platformGrid, grid.firstChild);
        if (accountLabel) accountLabel.hidden = true;
        if (partitionLabel) partitionLabel.hidden = true;
        account.disabled = true;
        account.required = false;
        partition.disabled = true;
        partition.required = false;
        if (platformLabel) platformLabel.textContent = "\u591a\u5e73\u53f0\u53d1\u5e03";
      }
    }

    const updateTitleCount = () => { if (counter && title) counter.textContent = String(title.value.length); };
    const updatePlatform = () => {
      if (platformCards.length) {
        const limits = platformCards
          .filter((card) => card.toggle.checked)
          .map((card) => Number(card.accountSelect.options[1]?.dataset.titleLimit || 80));
        const limit = limits.length ? Math.min(...limits) : 80;
        if (title) title.maxLength = limit;
        if (titleLimit) titleLimit.textContent = String(limit);
        updateTitleCount();
        return;
      }
      const selected = account?.selectedOptions[0];
      const platform = selected?.dataset.platform || "";
      const limit = Number(selected?.dataset.titleLimit || 80);
      const requiresPartition = selected?.dataset.requiresPartition !== "false";
      if (title) title.maxLength = limit;
      if (titleLimit) titleLimit.textContent = String(limit);
      if (platformLabel) platformLabel.textContent = selected?.dataset.platformLabel || "发布平台";
      if (partitionField) partitionField.hidden = !requiresPartition;
      if (partition) {
        partition.required = requiresPartition;
        let firstAvailable = null;
        [...partition.options].forEach((option) => {
          const available = !platform || option.dataset.platform === platform;
          option.disabled = !available;
          option.hidden = !available;
          if (available && !firstAvailable) firstAvailable = option;
        });
        if (partition.selectedOptions[0]?.disabled && firstAvailable) firstAvailable.selected = true;
      }
      if (copyrightField) copyrightField.hidden = platform === "douyin";
      updateTitleCount();
    };
    const updateCopyright = () => {
      const platform = account?.selectedOptions[0]?.dataset.platform || "";
      const repost = platform !== "douyin" && form.querySelector("input[name='copyright']:checked")?.value === "2";
      if (sourceField) sourceField.hidden = !repost;
      if (sourceInput) sourceInput.required = repost;
    };
    title?.addEventListener("input", updateTitleCount);
    account?.addEventListener("change", () => {
      updatePlatform();
      updateCopyright();
    });
    form.querySelectorAll("input[name='copyright']").forEach((input) => input.addEventListener("change", updateCopyright));
    updatePlatform();
    updateCopyright();

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (platformCards.length && !platformCards.some((card) => card.toggle.checked)) {
        if (error) {
          error.textContent = "\u8bf7\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u53d1\u5e03\u5e73\u53f0\u3002";
          error.hidden = false;
        }
        return;
      }
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
