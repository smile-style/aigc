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
});
