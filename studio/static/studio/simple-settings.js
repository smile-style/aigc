document.querySelectorAll("[data-video-provider]").forEach((select) => {
  select.addEventListener("change", () => {
    const option = select.options[select.selectedIndex];
    const baseUrl = option.dataset.defaultBaseUrl;
    const modelId = option.dataset.defaultModelId;
    if (!baseUrl || !modelId) return;

    const form = select.closest("form");
    const baseInput = form?.querySelector("[data-video-base-url]");
    const modelInput = form?.querySelector("[data-video-model-id]");
    const tokenInput = form?.querySelector("[data-video-token]");
    if (baseInput) baseInput.value = baseUrl;
    if (modelInput) modelInput.value = modelId;
    if (tokenInput) {
      tokenInput.value = "";
      tokenInput.placeholder = "输入 MiniMax Token";
    }
  });
});
