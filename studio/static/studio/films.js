(() => {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm || "Confirm this action?")) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    });
  });

  const modal = document.getElementById("film-player");
  if (!(modal instanceof HTMLElement)) return;

  const player = modal.querySelector("[data-film-player-video]");
  const title = modal.querySelector("#film-player-title");
  const meta = modal.querySelector("[data-film-player-meta]");
  const download = modal.querySelector("[data-film-player-download]");
  const triggers = document.querySelectorAll("[data-film-preview-url]");

  const clearPlayer = () => {
    if (!(player instanceof HTMLVideoElement)) return;
    player.pause();
    player.removeAttribute("src");
    player.load();
  };

  triggers.forEach((trigger) => {
    const preview = trigger.querySelector("video");
    if (preview instanceof HTMLVideoElement) {
      preview.removeAttribute("controls");
      preview.muted = true;
      preview.tabIndex = -1;
    }
    trigger.addEventListener("click", () => {
      if (player instanceof HTMLVideoElement) player.src = trigger.dataset.filmPreviewUrl || "";
      if (title) title.textContent = trigger.dataset.filmPreviewTitle || "";
      if (meta) meta.textContent = trigger.dataset.filmPreviewMeta || "";
      if (download instanceof HTMLAnchorElement) download.href = trigger.dataset.filmPreviewDownload || "#";
      if (player instanceof HTMLVideoElement) player.load();
    });
  });

  modal.querySelectorAll("[data-modal-close]").forEach((button) => {
    button.addEventListener("click", clearPlayer);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      clearPlayer();
    }
  });

  const fallbackCopy = (value) => {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) throw new Error("Copy failed");
  };

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const targetId = button.dataset.copyTarget || "";
      const target = document.getElementById(targetId);
      if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return;

      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(target.value);
        } else {
          fallbackCopy(target.value);
        }
        const dialog = button.closest("[role='dialog']");
        const status = dialog?.querySelector("[data-copy-status]");
        if (status) status.textContent = "已复制";
        button.textContent = "已复制";
        window.setTimeout(() => {
          button.textContent = "复制";
          if (status) status.textContent = "";
        }, 1600);
      } catch (_error) {
        const dialog = button.closest("[role='dialog']");
        const status = dialog?.querySelector("[data-copy-status]");
        if (status) status.textContent = "复制失败，请手动选择文本";
      }
    });
  });
})();
