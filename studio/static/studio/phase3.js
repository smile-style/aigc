document.addEventListener("DOMContentLoaded", () => {
  const workspace = document.querySelector("[data-video-status-url]");
  if (workspace instanceof HTMLElement) {
    const endpoint = workspace.dataset.videoStatusUrl;
    let lastSnapshot = null;
    let timer = null;
    const poll = async () => {
      try {
        const response = await fetch(endpoint, { headers: { Accept: "application/json" }, cache: "no-store" });
        if (!response.ok) throw new Error(`status ${response.status}`);
        const payload = await response.json();
        for (const [key, value] of Object.entries(payload.counts || {})) {
          const node = workspace.querySelector(`[data-video-count="${key}"]`);
          if (node) node.textContent = String(value);
        }
        const snapshot = JSON.stringify({ shots: payload.shots, composition: payload.composition_status, subtitle: payload.subtitle });
        if (lastSnapshot && snapshot !== lastSnapshot) window.location.reload();
        lastSnapshot = snapshot;
      } catch (error) {
        // Keep the page usable; the next poll retries automatically.
      }
      timer = window.setTimeout(poll, 5000);
    };
    timer = window.setTimeout(poll, 1000);
    window.addEventListener("beforeunload", () => window.clearTimeout(timer), { once: true });
  }

  const list = document.querySelector("[data-sortable-list]");
  const orderInput = document.querySelector("[data-shot-order]");
  if (list instanceof HTMLOListElement && orderInput instanceof HTMLInputElement) {
    let dragging = null;
    const updateOrder = () => {
      orderInput.value = Array.from(list.querySelectorAll("[data-shot-id]"))
        .map((item) => item.dataset.shotId)
        .join(",");
    };
    list.querySelectorAll("[data-shot-id]").forEach((item) => {
      item.addEventListener("dragstart", () => {
        dragging = item;
        item.classList.add("is-dragging");
      });
      item.addEventListener("dragend", () => {
        item.classList.remove("is-dragging");
        dragging = null;
        updateOrder();
      });
      item.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (!dragging || dragging === item) return;
        const bounds = item.getBoundingClientRect();
        list.insertBefore(dragging, event.clientY < bounds.top + bounds.height / 2 ? item : item.nextSibling);
      });
      const handle = item.querySelector(".drag-handle");
      if (handle instanceof HTMLButtonElement) {
        handle.addEventListener("keydown", (event) => {
          if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
          event.preventDefault();
          const sibling = event.key === "ArrowUp" ? item.previousElementSibling : item.nextElementSibling;
          if (!sibling) return;
          if (event.key === "ArrowUp") list.insertBefore(item, sibling);
          else list.insertBefore(sibling, item);
          updateOrder();
          handle.focus();
        });
      }
    });
  }

  document.querySelectorAll("[data-subtitle-form]").forEach((subtitleForm) => {
    if (!(subtitleForm instanceof HTMLFormElement)) return;
    const previewStage = subtitleForm.querySelector("[data-subtitle-preview-stage]");
    const previewVideo = subtitleForm.querySelector("[data-subtitle-preview]");
    const previewText = subtitleForm.querySelector("[data-subtitle-preview-text]");
    const offsetInput = subtitleForm.elements.namedItem("global_offset_ms")
      || subtitleForm.elements.namedItem("offset_ms");

    const updatePreviewText = (cue) => {
      if (!(previewText instanceof HTMLElement) || !(cue instanceof HTMLElement)) return;
      const text = cue.querySelector("[data-subtitle-text]");
      previewText.textContent = text instanceof HTMLTextAreaElement ? text.value : "";
    };

    const updateCueVisibility = (cue) => {
      if (!(cue instanceof HTMLElement)) return;
      const text = cue.querySelector("[data-subtitle-text]");
      if (!(text instanceof HTMLTextAreaElement)) return;
      const hidden = text.value.trim() === "";
      cue.classList.toggle("is-hidden", hidden);
      const state = cue.querySelector("[data-subtitle-hidden-state]");
      if (state instanceof HTMLElement) state.hidden = !hidden;
      const reviewed = cue.querySelector('input[name^="cue_reviewed_"]');
      if (hidden && reviewed instanceof HTMLInputElement) reviewed.checked = true;
    };

    subtitleForm.querySelectorAll("[data-subtitle-preview-url]").forEach((button) => {
      button.addEventListener("click", () => {
        const cue = button.closest("[data-subtitle-cue]");
        updatePreviewText(cue);
        if (!(previewVideo instanceof HTMLVideoElement) || !(previewStage instanceof HTMLElement)) return;
        const source = button.dataset.subtitlePreviewUrl;
        if (!source) return;
        const seek = Number(button.dataset.subtitlePreviewStart || 0);
        const playFromCue = () => {
          previewVideo.currentTime = Math.min(
            Math.max(0, seek),
            Number.isFinite(previewVideo.duration) ? previewVideo.duration : seek
          );
          previewVideo.play().catch(() => {});
        };
        previewStage.classList.add("has-video");
        if (previewVideo.getAttribute("src") !== source) {
          previewVideo.src = source;
          previewVideo.addEventListener("loadedmetadata", playFromCue, { once: true });
          previewVideo.load();
        } else {
          playFromCue();
        }
      });
    });

    subtitleForm.querySelectorAll("[data-subtitle-text]").forEach((textarea) => {
      const cue = textarea.closest("[data-subtitle-cue]");
      textarea.addEventListener("focus", () => updatePreviewText(cue));
      textarea.addEventListener("input", () => {
        updatePreviewText(cue);
        updateCueVisibility(cue);
      });
      updateCueVisibility(cue);
    });

    subtitleForm.querySelectorAll("[data-subtitle-shift]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!(offsetInput instanceof HTMLInputElement)) return;
        const next = Number(offsetInput.value || 0) + Number(button.dataset.subtitleShift || 0);
        offsetInput.value = String(Math.max(-10000, Math.min(10000, next)));
      });
    });

    subtitleForm.querySelectorAll("[data-subtitle-style]").forEach((input) => {
      input.addEventListener("input", () => {
        if (!(previewText instanceof HTMLElement) || !(input instanceof HTMLInputElement)) return;
        const property = input.dataset.subtitleStyle;
        if (property === "fontSize") previewText.style.fontSize = `${input.value}px`;
        if (property === "color") previewText.style.color = input.value;
        if (property === "outlineColor") previewText.style.webkitTextStrokeColor = input.value;
        if (property === "outlineWidth") previewText.style.webkitTextStrokeWidth = `${input.value}px`;
        if (property === "bottom") previewText.style.bottom = `${input.value}px`;
      });
    });

  });

  const subtitleEditor = document.getElementById("subtitle-editor");
  const exportButton = document.querySelector('.export-actions form[action*="/export/"] .primary-button');
  const subtitleToggle = document.querySelector('#subtitle-editor input[name="enabled"]');
  if (subtitleEditor instanceof HTMLElement && exportButton instanceof HTMLButtonElement) {
    const subtitlesEnabled = subtitleToggle instanceof HTMLInputElement && subtitleToggle.checked;
    const hasComposition = document.querySelector('.export-actions > a[href*="/download/"]') !== null;
    if (subtitlesEnabled) {
      exportButton.textContent = hasComposition
        ? "\u91cd\u65b0\u5bfc\u51fa\u5b57\u5e55\u6210\u7247"
        : "\u5bfc\u51fa\u5b57\u5e55\u6210\u7247";
    }
    const subtitlesBlocked = subtitleEditor.querySelector(".subtitle-aligning-state, .subtitle-editor-alert");
    if (subtitlesBlocked) exportButton.disabled = true;
  }

});
