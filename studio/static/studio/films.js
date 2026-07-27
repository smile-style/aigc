(() => {
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
})();
