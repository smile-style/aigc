document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-submit-guard]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      form.dataset.submitting = "true";

      const clickedButton = event.submitter instanceof HTMLButtonElement ? event.submitter : null;
      const submitButtons = form.querySelectorAll('button[type="submit"]');

      submitButtons.forEach((button) => {
        if (!button.dataset.originalText) {
          button.dataset.originalText = button.textContent.trim();
        }
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

  document.querySelectorAll("[data-outline-detail-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const modal = document.getElementById(button.dataset.outlineDetailTarget);
      if (!modal) return;
      if (modal.parentElement !== document.body) {
        document.body.appendChild(modal);
      }
      modal.hidden = false;
      const scrollArea = modal.querySelector("[data-outline-detail-scroll]");
      if (scrollArea instanceof HTMLElement) {
        scrollArea.scrollTop = 0;
      }
      document.body.classList.add("modal-open");
      const closeButton = modal.querySelector("[data-outline-detail-close]");
      if (closeButton instanceof HTMLElement) {
        closeButton.focus();
      }
    });
  });

  document.querySelectorAll("[data-outline-detail-close]").forEach((button) => {
    button.addEventListener("click", () => closeModal(button.closest(".detail-modal")));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const openModal = document.querySelector(".detail-modal:not([hidden])");
    closeModal(openModal);
  });
});