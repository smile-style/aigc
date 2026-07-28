import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from django.utils import timezone


logger = logging.getLogger(__name__)
SHORT_DURATION_MS = 500
MAX_REPEATED_SAMPLE_PER_TEXT = 3
DEFAULT_THRESHOLD = 0.60

_NON_CONTENT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", re.IGNORECASE)
_CREDIT_RE = re.compile(
    r"\b(?:transcription|transcribed|subtitled|subtitle|captioned|captions?)\s+by\b|"
    r"(?:字幕|听写|转录)[：:]?(?:由|by)",
    re.IGNORECASE,
)
_NOISE_RE = re.compile(
    r"^\s*(?:ignore noise|click|tap|beep|mouse click|keyboard click|background noise|noise only|"
    r"忽略噪音|点击|背景噪音)[.!。！]?\s*$",
    re.IGNORECASE,
)


@dataclass
class SubtitleQCResult:
    passed: bool
    score: float
    reason: str
    decision: str
    rule_score: float
    ai_score: float | None = None
    metrics: dict | None = None
    raw_ai: dict | None = None

    def details(self):
        value = asdict(self)
        value.pop("passed", None)
        value.pop("score", None)
        value.pop("reason", None)
        return value


@dataclass
class RuleResult:
    decision: str
    score: float
    reason: str
    metrics: dict


def _value(item, name, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _normalized(value):
    return _NON_CONTENT_RE.sub("", str(value or "").strip().lower())


def _cue_times(cue):
    start = _value(cue, "local_start_ms")
    end = _value(cue, "local_end_ms")
    if start is None:
        start = _value(cue, "start_ms", 0)
    if end is None:
        end = _value(cue, "end_ms", 0)
    return int(start or 0), int(end or 0)


def subtitle_qc_content_hash(cues):
    payload = []
    for cue in cues:
        start, end = _cue_times(cue)
        payload.append(
            {
                "id": _value(cue, "id"),
                "shot_id": _value(cue, "shot_id"),
                "position": int(_value(cue, "position", 0) or 0),
                "text": str(_value(cue, "text", "") or ""),
                "source_text": str(_value(cue, "source_text", "") or ""),
                "recognized_text": str(_value(cue, "recognized_text", "") or ""),
                "start_ms": start,
                "end_ms": end,
                "confidence": round(float(_value(cue, "confidence", 0.0) or 0.0), 6),
            }
        )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _max_repeat_run(values):
    longest = 0
    current = 0
    previous = None
    for value in values:
        if value and value == previous:
            current += 1
        else:
            current = 1 if value else 0
            previous = value
        longest = max(longest, current)
    return longest


def _analyze(cues):
    visible = []
    normalized = []
    invalid_time_count = 0
    out_of_bounds_count = 0
    short_duration_count = 0
    fast_reading_count = 0
    suspicious_count = 0
    low_confidence_count = 0
    recognized_count = 0
    groups = defaultdict(list)

    for cue in cues:
        text = str(_value(cue, "text", "") or "").strip()
        if not text:
            continue
        norm = _normalized(text)
        start, end = _cue_times(cue)
        duration = end - start
        visible.append(cue)
        normalized.append(norm)
        if start < 0 or duration <= 0:
            invalid_time_count += 1
        if 0 < duration < SHORT_DURATION_MS:
            short_duration_count += 1
        if duration > 0 and len(norm) / (duration / 1000.0) > 12.0:
            fast_reading_count += 1
        if _CREDIT_RE.search(text) or _NOISE_RE.search(text):
            suspicious_count += 1

        recognized = str(_value(cue, "recognized_text", "") or "").strip()
        if recognized:
            recognized_count += 1
            if float(_value(cue, "confidence", 0.0) or 0.0) < 0.65:
                low_confidence_count += 1

        shot_id = _value(cue, "shot_id")
        groups[shot_id].append((start, end))
        shot = _value(cue, "shot")
        duration_seconds = _value(shot, "duration_seconds") if shot is not None else None
        if duration_seconds is not None and (start < 0 or end > int(float(duration_seconds) * 1000) + 100):
            out_of_bounds_count += 1

    overlap_count = 0
    for ranges in groups.values():
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            if current[0] < previous[1]:
                overlap_count += 1

    freq = Counter(value for value in normalized if value)
    usable_count = sum(1 for value in normalized if value)
    top_frequency = max(freq.values()) if freq else 0
    total = len(visible)
    repeat_mass = sum(count for count in freq.values() if count >= 2)
    total_chars = sum(len(value) for value in normalized)
    timeline_ms = sum(max(0, _cue_times(cue)[1] - _cue_times(cue)[0]) for cue in visible)
    metrics = {
        "total_items": len(cues),
        "visible_count": total,
        "usable_count": usable_count,
        "hidden_count": max(0, len(cues) - total),
        "unique_ratio": len(freq) / max(1, usable_count),
        "top_frequency": top_frequency,
        "top_ratio": top_frequency / max(1, usable_count),
        "repeat_mass_ratio": repeat_mass / max(1, usable_count),
        "max_repeat_run": _max_repeat_run(normalized),
        "avg_text_length": total_chars / max(1, usable_count),
        "short_duration_count": short_duration_count,
        "short_duration_ratio": short_duration_count / max(1, total),
        "fast_reading_count": fast_reading_count,
        "fast_reading_ratio": fast_reading_count / max(1, total),
        "invalid_time_count": invalid_time_count,
        "overlap_count": overlap_count,
        "out_of_bounds_count": out_of_bounds_count,
        "suspicious_phrase_count": suspicious_count,
        "recognized_count": recognized_count,
        "low_confidence_count": low_confidence_count,
        "low_confidence_ratio": low_confidence_count / max(1, recognized_count),
        "total_text_chars": total_chars,
        "display_seconds": round(timeline_ms / 1000.0, 3),
    }
    return metrics


def _score(metrics):
    score = 1.0
    score -= min(0.30, metrics["short_duration_ratio"] * 0.45)
    score -= min(0.30, metrics["fast_reading_ratio"] * 0.45)
    score -= min(0.30, metrics["low_confidence_ratio"] * 0.40)
    score -= min(0.35, max(0.0, metrics["top_ratio"] - 0.35) * 0.70)
    score -= min(0.25, max(0.0, metrics["repeat_mass_ratio"] - 0.45) * 0.55)
    score -= min(0.20, max(0, metrics["max_repeat_run"] - 2) * 0.08)
    score -= min(0.60, metrics["suspicious_phrase_count"] * 0.30)
    score -= min(0.70, metrics["invalid_time_count"] * 0.35)
    score -= min(0.70, metrics["overlap_count"] * 0.25)
    score -= min(0.70, metrics["out_of_bounds_count"] * 0.25)
    return round(max(0.0, min(1.0, score)), 4)


def check_subtitle_rules(cues):
    cues = list(cues)
    metrics = _analyze(cues)
    score = _score(metrics)
    hard_failures = (
        (metrics["visible_count"] == 0, "empty"),
        (metrics["invalid_time_count"] > 0, "invalid_timing"),
        (metrics["overlap_count"] > 0, "overlap"),
        (metrics["out_of_bounds_count"] > 0, "out_of_bounds"),
        (metrics["suspicious_phrase_count"] > 0, "hallucination_meta"),
        (
            metrics["usable_count"] >= 8
            and (metrics["top_ratio"] >= 0.75 or metrics["max_repeat_run"] >= 4),
            "extreme_repetition",
        ),
    )
    for failed, reason in hard_failures:
        if failed:
            return RuleResult("rule_fail", score, f"rule_fail:{reason}", metrics)
    if score >= 0.85:
        return RuleResult("rule_pass", score, "rule_pass:healthy", metrics)
    return RuleResult("needs_ai", score, "needs_ai:boundary", metrics)


def _sample(cues, max_items=80, max_chars=9000):
    rendered = []
    size = 0
    seen = Counter()
    for index, cue in enumerate(cues, start=1):
        text = str(_value(cue, "text", "") or "").strip()
        if not text:
            continue
        norm = _normalized(text)
        if seen[norm] >= MAX_REPEATED_SAMPLE_PER_TEXT:
            continue
        seen[norm] += 1
        start, end = _cue_times(cue)
        line = f"{index}. {start}ms --> {end}ms\n{text}"
        if len(rendered) >= max_items or size + len(line) > max_chars:
            break
        rendered.append(line)
        size += len(line)
    return "\n\n".join(rendered), {"sample_items": len(rendered), "sample_chars": size}


def _call_ai(provider, cues, metrics):
    sample, sample_meta = _sample(cues)
    payload = provider.generate_json(
        [
            {
                "role": "system",
                "content": (
                    "You are a strict subtitle QC reviewer. Detect hallucinated credits or commands, "
                    "mechanical repetition, unreadably fast captions, timing anomalies, and ASR text "
                    "that clearly conflicts with the intended subtitle. Short dramatic Chinese lines are valid. "
                    "Return JSON only: {\"passed\": true, \"score\": 0.9, \"reason\": \"ok\"}."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"task": "subtitle_qc", "metrics": metrics, "subtitle_sample": sample},
                    ensure_ascii=False,
                ),
            },
        ],
        temperature=0.0,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("passed"), bool):
        raise ValueError("Subtitle QC model returned an invalid result")
    ai_score = float(payload.get("score", 1.0 if payload["passed"] else 0.0))
    ai_score = max(0.0, min(1.0, ai_score))
    return payload["passed"], ai_score, str(payload.get("reason") or "unknown")[:120], payload, sample_meta


def run_subtitle_qc(cues, provider=None, threshold=DEFAULT_THRESHOLD):
    cues = list(cues)
    rule = check_subtitle_rules(cues)
    if rule.decision == "rule_pass":
        return SubtitleQCResult(True, rule.score, rule.reason, rule.decision, rule.score, metrics=rule.metrics)
    if rule.decision == "rule_fail":
        return SubtitleQCResult(False, rule.score, rule.reason, rule.decision, rule.score, metrics=rule.metrics)

    if provider is None:
        passed = rule.score >= float(threshold)
        prefix = "rule_pass" if passed else "rule_fail"
        return SubtitleQCResult(
            passed,
            rule.score,
            f"{prefix}:threshold",
            "needs_ai",
            rule.score,
            metrics={**rule.metrics, "ai_status": "not_configured"},
        )

    try:
        ai_passed, ai_score, ai_reason, raw_ai, sample_meta = _call_ai(provider, cues, rule.metrics)
    except Exception as exc:
        logger.warning("Subtitle AI QC unavailable: %s", exc)
        passed = rule.score >= float(threshold)
        return SubtitleQCResult(
            passed,
            rule.score,
            "qc_skipped:ai_unavailable" if passed else "rule_fail:ai_unavailable",
            "needs_ai",
            rule.score,
            metrics={**rule.metrics, "ai_status": "unavailable"},
        )

    final_score = min(rule.score, ai_score)
    passed = bool(ai_passed) and final_score >= float(threshold)
    return SubtitleQCResult(
        passed,
        final_score,
        f"ai_{'pass' if passed else 'fail'}:{ai_reason}",
        "needs_ai",
        rule.score,
        ai_score=ai_score,
        metrics={**rule.metrics, **sample_meta, "ai_status": "ok"},
        raw_ai=raw_ai,
    )


def _track_cues_for_qc(track):
    from studio.models import ShotSubtitleSetting

    cues = list(track.cues.select_related("shot").order_by("position", "id"))
    settings = {
        setting.shot_id: setting
        for setting in ShotSubtitleSetting.objects.filter(
            shot_id__in={cue.shot_id for cue in cues if cue.shot_id}
        )
    }
    effective = []
    for cue in cues:
        setting = settings.get(cue.shot_id)
        if setting is not None and not setting.enabled:
            continue
        start, end = _cue_times(cue)
        offset = int(track.global_offset_ms or 0) + int(setting.offset_ms if setting else 0)
        effective_start = max(0, start + offset)
        effective_end = max(effective_start + 100, end + offset)
        effective.append(
            {
                "id": cue.id,
                "shot_id": cue.shot_id,
                "shot": cue.shot,
                "position": cue.position,
                "text": cue.text,
                "source_text": cue.source_text,
                "recognized_text": cue.recognized_text,
                "local_start_ms": effective_start,
                "local_end_ms": effective_end,
                "confidence": cue.confidence,
            }
        )
    return effective

def subtitle_qc_is_current(track, cues=None):
    cues = list(cues) if cues is not None else _track_cues_for_qc(track)
    return bool(
        track.qc_status in {track.QC_PASSED, track.QC_FAILED}
        and track.qc_content_hash
        and track.qc_content_hash == subtitle_qc_content_hash(cues)
    )


def _persist_subtitle_qc_result(track, cues, result):
    track.qc_status = track.QC_PASSED if result.passed else track.QC_FAILED
    track.qc_score = result.score
    track.qc_rule_score = result.rule_score
    track.qc_ai_score = result.ai_score
    track.qc_reason = result.reason
    track.qc_details = result.details()
    track.qc_checked_at = timezone.now()
    track.qc_content_hash = subtitle_qc_content_hash(cues)
    track.qc_revision = track.revision
    track.save(
        update_fields=[
            "qc_status",
            "qc_score",
            "qc_rule_score",
            "qc_ai_score",
            "qc_reason",
            "qc_details",
            "qc_checked_at",
            "qc_content_hash",
            "qc_revision",
            "updated_at",
        ]
    )
    return result


def run_and_persist_subtitle_qc(
    track,
    provider=None,
    use_configured_ai=True,
    allow_empty=False,
):
    cues = _track_cues_for_qc(track)
    if allow_empty and not cues:
        result = SubtitleQCResult(
            True,
            1.0,
            "rule_pass:no_spoken_dialogue",
            "rule_pass",
            1.0,
            metrics={"visible_count": 0, "no_spoken_dialogue": True},
        )
        return _persist_subtitle_qc_result(track, cues, result)

    owned_provider = False
    if provider is None and use_configured_ai:
        try:
            from studio.models import ModelAssignment, ModelConfig
            from studio.services.model_config import assigned_model, llm_provider_for

            model = assigned_model(ModelAssignment.PURPOSE_SUBTITLE_QC, ModelConfig.CAPABILITY_TEXT)
            if model is not None:
                provider = llm_provider_for(ModelAssignment.PURPOSE_SUBTITLE_QC)
                owned_provider = True
        except Exception as exc:
            logger.warning("Subtitle QC model is unavailable; using rule result: %s", exc)
            provider = None
    try:
        result = run_subtitle_qc(cues, provider=provider)
    finally:
        if owned_provider and provider is not None:
            try:
                provider.close()
            except Exception as exc:
                logger.warning("Could not close subtitle QC model client: %s", exc)

    return _persist_subtitle_qc_result(track, cues, result)


def ensure_subtitle_qc(track):
    if not subtitle_qc_is_current(track):
        return run_and_persist_subtitle_qc(track)
    return SubtitleQCResult(
        track.qc_status == track.QC_PASSED,
        float(track.qc_score or 0.0),
        track.qc_reason,
        str((track.qc_details or {}).get("decision") or "stored"),
        float(track.qc_rule_score or 0.0),
        ai_score=track.qc_ai_score,
        metrics=(track.qc_details or {}).get("metrics") or {},
        raw_ai=(track.qc_details or {}).get("raw_ai"),
    )
