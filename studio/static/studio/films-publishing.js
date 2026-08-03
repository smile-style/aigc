document.addEventListener("DOMContentLoaded", () => {
  const csrfToken = () =>
    document.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";

  const initializePublishForm = (form) => {
    if (!(form instanceof HTMLFormElement) || form.dataset.initialized === "true") return;
    form.dataset.initialized = "true";

    const title = form.querySelector("input[name='title']");
    const counter = form.querySelector("[data-character-count]");
    const titleLimit = form.querySelector("[data-title-limit]");
    const account = form.querySelector("[data-publish-account]");
    const partition = form.querySelector("[data-publish-partition]");
    const error = form.querySelector("[data-publish-error]");
    const submit = form.querySelector("button[type='submit']");
    const grid = form.querySelector(".publish-form-grid");
    const cards = [];

    const updateTitleCount = () => {
      if (counter && title instanceof HTMLInputElement) {
        counter.textContent = String(title.value.length);
      }
    };

    if (
      account instanceof HTMLSelectElement &&
      partition instanceof HTMLSelectElement &&
      grid instanceof HTMLElement
    ) {
      const platformGrid = document.createElement("div");
      platformGrid.className = "publish-platform-grid field-wide";
      [
        { key: "bilibili", label: "Bilibili" },
        { key: "acfun", label: "AcFun" },
      ].forEach((definition) => {
        const accountOptions = [...account.options].filter(
          (option) => option.dataset.platform === definition.key
        );
        if (!accountOptions.length) return;

        const card = document.createElement("fieldset");
        card.className = "publish-platform-card";
        const toggleLabel = document.createElement("label");
        toggleLabel.className = "publish-platform-toggle";
        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        const label = document.createElement("span");
        label.textContent = definition.label;
        toggleLabel.append(toggle, label);

        const accountField = document.createElement("label");
        accountField.className = "publish-platform-field";
        const accountCaption = document.createElement("span");
        accountCaption.textContent = "\u53d1\u5e03\u8d26\u53f7";
        const accountSelect = document.createElement("select");
        accountSelect.name = "account_ids";
        accountSelect.disabled = true;
        accountSelect.append(new Option("\u8bf7\u9009\u62e9\u8d26\u53f7", ""));
        accountOptions.forEach((option) => accountSelect.append(option.cloneNode(true)));
        accountField.append(accountCaption, accountSelect);

        const partitionField = document.createElement("label");
        partitionField.className = "publish-platform-field";
        const partitionCaption = document.createElement("span");
        partitionCaption.textContent = `${definition.label} \u5206\u533a`;
        const partitionSelect = document.createElement("select");
        partitionSelect.name = `tid_${definition.key}`;
        partitionSelect.disabled = true;
        [...partition.options]
          .filter((option) => option.dataset.platform === definition.key)
          .forEach((option) => partitionSelect.append(option.cloneNode(true)));
        partitionField.append(partitionCaption, partitionSelect);

        toggle.addEventListener("change", () => {
          accountSelect.disabled = !toggle.checked;
          accountSelect.required = toggle.checked;
          partitionSelect.disabled = !toggle.checked;
          partitionSelect.required = toggle.checked;
          card.classList.toggle("is-selected", toggle.checked);
          const limits = cards
            .filter((item) => item.toggle.checked)
            .map((item) => Number(item.accountSelect.selectedOptions[0]?.dataset.titleLimit || 80));
          const limit = limits.length ? Math.min(...limits) : 80;
          if (title instanceof HTMLInputElement) title.maxLength = limit;
          if (titleLimit) titleLimit.textContent = String(limit);
        });

        card.append(toggleLabel, accountField, partitionField);
        platformGrid.append(card);
        cards.push({ toggle, accountSelect });
      });

      if (cards.length) {
        grid.insertBefore(platformGrid, grid.firstChild);
        account.closest("label")?.setAttribute("hidden", "");
        partition.closest("label")?.setAttribute("hidden", "");
        account.disabled = true;
        account.required = false;
        partition.disabled = true;
        partition.required = false;
      }
    }

    title?.addEventListener("input", updateTitleCount);
    updateTitleCount();

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (cards.length && !cards.some((card) => card.toggle.checked)) {
        if (error) {
          error.textContent = "\u8bf7\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u53d1\u5e03\u5e73\u53f0\u3002";
          error.hidden = false;
        }
        return;
      }
      if (!form.reportValidity()) return;
      if (error) error.hidden = true;
      if (submit instanceof HTMLButtonElement) {
        submit.disabled = true;
        submit.textContent = "\u6b63\u5728\u68c0\u67e5...";
      }
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: { Accept: "application/json", "X-CSRFToken": csrfToken() },
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "\u65e0\u6cd5\u521b\u5efa\u53d1\u5e03\u4efb\u52a1\u3002");
        window.location.assign("/publishing/tasks/");
      } catch (reason) {
        if (error) {
          error.textContent = reason.message;
          error.hidden = false;
        }
        if (submit instanceof HTMLButtonElement) {
          submit.disabled = false;
          submit.textContent = "\u68c0\u67e5\u5e76\u53d1\u5e03";
        }
      }
    });
  };

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-modal-target^='publish-composition-']");
    if (!(trigger instanceof HTMLElement)) return;
    const modal = document.getElementById(trigger.dataset.modalTarget || "");
    initializePublishForm(modal?.querySelector("[data-publish-form]"));
  });

  const workspace = document.querySelector("[data-publishing-status-url]");
  if (!(workspace instanceof HTMLElement)) return;
  let timer = null;
  const poll = async () => {
    if (document.hidden) {
      timer = window.setTimeout(poll, 5000);
      return;
    }
    const rows = [...workspace.querySelectorAll("[data-publishing-task]")];
    const active = rows.filter((row) =>
      ["queued", "running", "retry_wait", "submitted"].includes(row.dataset.status)
    );
    if (!active.length) return;
    try {
      const ids = active.map((row) => row.dataset.publishingTask).join(",");
      const response = await fetch(
        `${workspace.dataset.publishingStatusUrl}?ids=${encodeURIComponent(ids)}`
      );
      if (!response.ok) throw new Error("status request failed");
      const payload = await response.json();
      payload.tasks.forEach((task) => {
        const row = workspace.querySelector(`[data-publishing-task='${task.id}']`);
        if (!row) return;
        row.dataset.status = task.status;
        const progress = row.querySelector("[data-task-progress]");
        if (progress) progress.value = task.progress;
        const percent = row.querySelector("[data-task-percent]");
        if (percent) percent.textContent = `${task.progress}%`;
        const stage = row.querySelector("[data-task-stage]");
        if (stage) stage.textContent = task.stage_label;
        const status = row.querySelector("[data-task-status]");
        if (status) status.textContent = task.status_label;
      });
    } catch (_error) {
      // Retry on the next interval.
    }
    timer = window.setTimeout(poll, 5000);
  };
  timer = window.setTimeout(poll, 1000);
  window.addEventListener("beforeunload", () => window.clearTimeout(timer), { once: true });
});
