import pytest

from studio.services.subtitle_qc import (
    check_subtitle_rules,
    run_subtitle_qc,
    subtitle_qc_content_hash,
)


def cue(text, start, end, *, shot_id=1, duration_seconds=20, **extra):
    value = {
        "id": extra.pop("id", start + 1),
        "shot_id": shot_id,
        "position": extra.pop("position", start + 1),
        "text": text,
        "source_text": text,
        "recognized_text": "",
        "local_start_ms": start,
        "local_end_ms": end,
        "confidence": 0.95,
        "shot": {"duration_seconds": duration_seconds},
    }
    value.update(extra)
    return value


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload

    def generate_json(self, messages, temperature=0.0):
        assert messages
        assert temperature == 0.0
        return self.payload


def test_healthy_short_drama_subtitles_pass_rules():
    cues = [
        cue(text, index * 1400, index * 1400 + 1200)
        for index, text in enumerate(
            ["走", "别回头", "他来了", "我知道", "快躲开", "等一下"]
        )
    ]

    result = check_subtitle_rules(cues)

    assert result.decision == "rule_pass"
    assert result.score >= 0.85


@pytest.mark.parametrize(
    ("cues", "reason"),
    [
        ([], "empty"),
        ([cue("bad", 1000, 900)], "invalid_timing"),
        ([cue("one", 0, 1200), cue("two", 1100, 2200)], "overlap"),
        ([cue("", 0, 100, source_text="missing", needs_review=True)], "unmatched_speech"),
        ([cue("late", 0, 2500, duration_seconds=2)], "out_of_bounds"),
        ([cue("Transcribed by somebody", 0, 1500)], "hallucination_meta"),
        ([cue("点击", 0, 1500)], "hallucination_meta"),
    ],
)
def test_hard_rule_failures(cues, reason):
    result = check_subtitle_rules(cues)

    assert result.decision == "rule_fail"
    assert result.reason == f"rule_fail:{reason}"


def test_extreme_repetition_fails():
    cues = [cue("重复句", index * 1200, index * 1200 + 1000) for index in range(8)]

    result = check_subtitle_rules(cues)

    assert result.decision == "rule_fail"
    assert result.reason == "rule_fail:extreme_repetition"


def test_boundary_result_uses_optional_ai_review():
    cues = [
        cue(chr(0x4E00 + index), index * 1000, index * 1000 + (300 if index < 2 else 900))
        for index in range(4)
    ]

    passed = run_subtitle_qc(
        cues,
        provider=FakeProvider({"passed": True, "score": 0.9, "reason": "acceptable"}),
    )
    failed = run_subtitle_qc(
        cues,
        provider=FakeProvider({"passed": False, "score": 0.2, "reason": "bad timing"}),
    )

    assert passed.passed is True
    assert passed.ai_score == 0.9
    assert failed.passed is False
    assert failed.reason == "ai_fail:bad timing"


def test_boundary_result_falls_back_to_rules_without_ai():
    cues = [
        cue(chr(0x4E00 + index), index * 1000, index * 1000 + (300 if index < 2 else 900))
        for index in range(4)
    ]

    result = run_subtitle_qc(cues)

    assert result.passed is True
    assert result.metrics["ai_status"] == "not_configured"


def test_content_hash_tracks_text_and_timing_but_not_external_style():
    cues = [cue("original", 0, 1200)]
    original = subtitle_qc_content_hash(cues)

    cues[0]["text"] = "edited"
    edited = subtitle_qc_content_hash(cues)
    cues[0]["text"] = "original"
    cues[0]["local_end_ms"] = 1300
    retimed = subtitle_qc_content_hash(cues)

    assert original != edited
    assert original != retimed