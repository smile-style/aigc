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


class SequenceProvider:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def generate_json(self, messages, temperature=None):
        self.calls.append({"messages": messages, "temperature": temperature})
        return next(self.payloads)


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


def test_generate_outlines_requires_non_empty_string_fields_in_prompt():
    provider = FakeProvider({"outlines": [make_outline(i) for i in range(1, 7)]})

    generate_outlines(provider, "urban revenge")

    prompt = provider.messages[-1]["content"]
    assert "All field values must be non-empty strings" in prompt
    assert "arc_summary" in prompt
    assert "long-running story arc" in prompt


def test_generate_outlines_corrects_invalid_model_payload_once():
    invalid = [make_outline(i) for i in range(1, 7)]
    for outline in invalid:
        outline["arc_summary"] = None
    corrected = [make_outline(i) for i in range(1, 7)]
    provider = SequenceProvider([
        {"outlines": invalid},
        {"outlines": corrected},
    ])

    outlines = generate_outlines(provider, "urban revenge")

    assert outlines == corrected
    assert len(provider.calls) == 2
    assert provider.calls[0]["temperature"] == 0.9
    assert provider.calls[1]["temperature"] == 0.3
    correction_messages = provider.calls[1]["messages"]
    assert correction_messages[-2]["role"] == "assistant"
    assert '"arc_summary": null' in correction_messages[-2]["content"]
    assert "field arc_summary must be a non-empty string" in correction_messages[-1]["content"]
    assert "All field values must be non-empty strings" in correction_messages[-1]["content"]


def test_validate_outlines_rejects_wrong_count():
    with pytest.raises(ValueError, match="Expected 6 outlines, got 1"):
        validate_outlines({"outlines": [make_outline(1)]})


@pytest.mark.parametrize("payload", [None, {"outlines": "not-a-list"}])
def test_validate_outlines_rejects_missing_or_non_list_payload(payload):
    with pytest.raises(ValueError, match="outlines list"):
        validate_outlines(payload)


def test_validate_outlines_rejects_non_object_outline():
    outlines = [123] + [make_outline(i) for i in range(2, 7)]

    with pytest.raises(ValueError, match="object"):
        validate_outlines({"outlines": outlines})


def test_validate_outlines_rejects_missing_field():
    bad_outline = make_outline(1)
    del bad_outline["hook"]

    with pytest.raises(ValueError, match="hook"):
        validate_outlines({"outlines": [bad_outline for _ in range(6)]})


@pytest.mark.parametrize("value", ["", "   "])
def test_validate_outlines_rejects_blank_required_field(value):
    outlines = [make_outline(i) for i in range(1, 7)]
    outlines[0]["title"] = value

    with pytest.raises(ValueError, match="title"):
        validate_outlines({"outlines": outlines})


@pytest.mark.parametrize("value", [123, None])
def test_validate_outlines_rejects_non_string_required_field(value):
    outlines = [make_outline(i) for i in range(1, 7)]
    outlines[0]["hook"] = value

    with pytest.raises(ValueError, match="hook"):
        validate_outlines({"outlines": outlines})


def test_validate_outlines_rejects_duplicate_id():
    outlines = [make_outline(i) for i in range(1, 7)]
    outlines[1]["id"] = outlines[0]["id"]

    with pytest.raises(ValueError, match="duplicate|id"):
        validate_outlines({"outlines": outlines})
