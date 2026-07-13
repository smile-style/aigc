from studio.llm.image_provider import ImageConfig


def test_image_config_defaults_to_flash_image_model():
    config = ImageConfig.from_env(
        {
            "LLM_BASE_URL": "https://example.test/v1",
            "LLM_API_KEY": "key",
        }
    )

    assert config.model == "gemini-3.1-flash-image-preview"
    assert config.endpoint == "/chat/completions"


def test_image_config_allows_model_override():
    config = ImageConfig.from_env(
        {"LLM_BASE_URL": "https://example.test/v1", "LLM_API_KEY": "key", "IMAGE_MODEL": "custom-image-model"}
    )

    assert config.model == "custom-image-model"
