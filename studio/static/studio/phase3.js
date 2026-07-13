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
        const snapshot = JSON.stringify({ shots: payload.shots, composition: payload.composition_status });
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
});
