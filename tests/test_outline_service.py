import pytest

from studio.services.outline import generate_outlines, validate_outlines


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None

    def generate_json(self, messages, temperature=None):
        self.messages = messages
        self.temperature = temperature
        return self.payload


def make_outline(index):
    return {
        "id": f"outline-{index}",
        "title": f"标题 {index}",
        "core_premise": f"核心设定 {index}",
        "protagonist": f"主角 {index}",
        "hook": f"爽点 {index}",
        "arc_summary": f"60集走向 {index}",
    }


def test_generate_outlines_returns_six_candidates():
    provider = FakeProvider({"outlines": [make_outline(i) for i in range(1, 7)]})

    outlines = generate_outlines(provider, "逆袭爽文")

    assert len(outlines) == 6
    assert outlines[0]["id"] == "outline-1"
    assert "逆袭爽文" in provider.messages[-1]["content"]
    assert "60集" in provider.messages[-1]["content"]
    assert provider.temperature == 0.9


def test_validate_outlines_rejects_wrong_count():
    with pytest.raises(ValueError, match="6"):
        validate_outlines({"outlines": [make_outline(1)]})


def test_validate_outlines_rejects_missing_field():
    bad_outline = make_outline(1)
    del bad_outline["hook"]

    with pytest.raises(ValueError, match="hook"):
        validate_outlines({"outlines": [bad_outline for _ in range(6)]})
