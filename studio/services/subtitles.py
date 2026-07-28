import hashlib
import json
import os
import re
import shutil
import subprocess
from difflib import SequenceMatcher

from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from studio.models import (
    GenerationTask,
    ShotSubtitleSetting,
    StoryboardShot,
    SubtitleCue,
    SubtitleTrack,
    VideoAsset,
)


DEFAULT_STYLE = {
    "font_name": "Noto Sans CJK SC",
    "font_size": 38,
    "text_color": "#FFFFFF",
    "outline_color": "#000000",
    "outline_size": 3,
    "margin_bottom": 92,
}
MAX_CUE_TEXT_LENGTH = 18
ROLE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"[【\[][^】\]\n]{1,16}[】\]]\s*[：:]?"
    r"|[A-Za-z\u4e00-\u9fff·• ]{1,16}(?:（[^）\n]{1,10}）|\([^\)\n]{1,10}\))?\s*[：:]"
    r")\s*"
)
SUBTITLE_QUOTE_RE = re.compile(r"[\"“”‘’「」『』]")
SUBTITLE_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+(?:[。！？!?；;]+[\"”’』」]?)?")
SPEAKER_TAG_RE = re.compile(
    r"(?P<label>[A-Za-z0-9\u4e00-\u9fff·•（）() ]{1,30})[：:]\s*"
)
NO_DIALOGUE_RE = re.compile(
    r"^\s*(?:无|无对白|无台词|无人说话|空镜|纯画面|环境音|音效|无声|"
    r"none|n/?a|[-—]+)\s*[。.!！]?\s*$",
    re.IGNORECASE,
)
NON_SPOKEN_SENTENCE_RE = re.compile(
    r"^(?:画面|镜头|屏幕|楼梯间|走廊|门外|远处|背景|施工声|脚步声|敲击声|"
    r"警报|音乐|环境音).*(?:传来|响起|出现|显示|切换|闪过|靠近|远去|连续)"
)
NON_SPEECH_LABEL_KEYWORDS = (
    "旁白",
    "音效",
    "环境音",
    "脚步声",
    "敲击声",
    "施工声",
    "警报",
    "计时",
    "字幕",
    "屏幕标识",
    "画面",
    "镜头",
    "动作",
    "系统提示",
    "系统播报",
    "转场",
)
SPEECH_LABEL_HINTS = (
    "说",
    "问",
    "答",
    "喊",
    "自语",
    "低声",
    "高声",
    "急声",
    "语音",
    "电话",
    "读出",
    "念出",
)
QUOTE_PAIRS = {"“": "”", '"': '"', "「": "」", "『": "』", "‘": "’"}
_WHISPER_MODEL = None


def selected_episode_assets(episode):
    shots = list(
        StoryboardShot.objects.filter(storyboard__episode=episode)
        .order_by("position", "shot_number", "id")
    )
    asset_map = {
        asset.shot_id: asset
        for asset in VideoAsset.objects.filter(
            shot_id__in=[shot.id for shot in shots],
            is_selected=True,
            status=VideoAsset.STATUS_READY,
        ).select_related("shot")
    }
    return [asset_map.get(shot.id) for shot in shots]


def shot_subtitle_source_hash(shot, asset=None):
    payload = {
        "shot_id": str(shot.shot_id),
        "dialogue": shot.dialogue_or_narration,
        "duration_seconds": shot.duration_seconds,
        "video_asset_id": asset.id if asset else None,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def subtitle_source_hash(episode, assets=None):
    assets = assets if assets is not None else selected_episode_assets(episode)
    payload = [
        {
            "shot_id": str(shot.shot_id),
            "position": shot.position,
            "dialogue": shot.dialogue_or_narration,
            "duration_seconds": shot.duration_seconds,
            "video_asset_id": asset.id if asset else None,
        }
        for shot, asset in zip(
            StoryboardShot.objects.filter(storyboard__episode=episode).order_by("position", "shot_number", "id"),
            assets,
        )
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def queue_subtitle_alignment(episode, shot=None):
    active = episode.script.project.generation_tasks.filter(
        task_type=GenerationTask.TYPE_SUBTITLE_ALIGN,
        status__in=[GenerationTask.STATUS_PENDING, GenerationTask.STATUS_RUNNING],
    ).filter(
        Q(input_snapshot__episode_id=episode.id) | Q(target_id=str(episode.id))
    ).first()
    if active:
        return active, False

    shots = (
        [shot]
        if shot is not None
        else list(
            StoryboardShot.objects.filter(storyboard__episode=episode)
            .order_by("position", "shot_number", "id")
        )
    )
    asset_map = {
        asset.shot_id: asset
        for asset in VideoAsset.objects.filter(
            shot_id__in=[item.id for item in shots],
            is_selected=True,
            status=VideoAsset.STATUS_READY,
        ).select_related("shot")
    }
    assets = [asset_map.get(item.id) for item in shots]
    if not assets or any(asset is None for asset in assets):
        raise ValueError("请先为要生成字幕的分镜选定可用视频。")

    track, _ = SubtitleTrack.objects.get_or_create(episode=episode)
    track.enabled = True
    track.status = SubtitleTrack.STATUS_ALIGNING
    track.error_message = ""
    track.save(update_fields=["enabled", "status", "error_message", "updated_at"])
    for item in shots:
        setting, _ = ShotSubtitleSetting.objects.get_or_create(shot=item)
        setting.status = ShotSubtitleSetting.STATUS_ALIGNING
        setting.error_message = ""
        setting.save(update_fields=["status", "error_message", "updated_at"])

    task = GenerationTask.objects.create(
        project=episode.script.project,
        task_type=GenerationTask.TYPE_SUBTITLE_ALIGN,
        target_id=str(track.id),
        input_snapshot={
            "episode_id": episode.id,
            "subtitle_track_id": track.id,
            "shot_ids": [item.id for item in shots],
            "video_asset_ids": [asset.id for asset in assets],
            "source_hash": subtitle_source_hash(episode),
            "scope": "shot" if shot is not None else "episode",
        },
    )
    return task, True


def process_subtitle_task(task_id):
    task = GenerationTask.objects.get(pk=task_id)
    track = SubtitleTrack.objects.select_related("episode").get(pk=task.target_id)
    try:
        GenerationTask.objects.filter(pk=task.pk).update(
            status=GenerationTask.STATUS_RUNNING,
            error_message="",
            started_at=task.started_at or timezone.now(),
            finished_at=None,
        )
        assets = list(
            VideoAsset.objects.filter(pk__in=task.input_snapshot["video_asset_ids"])
            .select_related("shot")
        )
        asset_map = {asset.id: asset for asset in assets}
        ordered = [asset_map[asset_id] for asset_id in task.input_snapshot["video_asset_ids"]]
        generate_subtitle_cues(
            track,
            ordered,
            source_hash=task.input_snapshot.get("source_hash"),
            task_id=task.id,
        )
        GenerationTask.objects.filter(pk=task.pk).update(
            status=GenerationTask.STATUS_SUCCEEDED,
            progress_current=len(ordered),
            progress_total=len(ordered),
            progress_percent=100,
            result_snapshot={
                "subtitle_track_id": track.id,
                "cue_count": track.cues.count(),
                "shot_ids": task.input_snapshot.get("shot_ids", []),
                "subtitle_qc": {
                    "status": track.qc_status,
                    "score": track.qc_score,
                    "reason": track.qc_reason,
                },
            },
            error_message="",
            finished_at=timezone.now(),
        )
    except Exception as exc:
        shot_ids = task.input_snapshot.get("shot_ids", [])
        ShotSubtitleSetting.objects.filter(shot_id__in=shot_ids).update(
            status=ShotSubtitleSetting.STATUS_FAILED,
            error_message=str(exc),
            updated_at=timezone.now(),
        )
        track.status = SubtitleTrack.STATUS_FAILED
        track.error_message = str(exc)
        track.save(update_fields=["status", "error_message", "updated_at"])
        GenerationTask.objects.filter(pk=task.pk).update(
            status=GenerationTask.STATUS_FAILED,
            error_message=str(exc),
            finished_at=timezone.now(),
        )
    return track


def generate_subtitle_cues(track, assets, source_hash=None, task_id=None):
    target_shot_ids = [asset.shot_id for asset in assets]
    manual_by_shot = {}
    for cue in track.cues.filter(
        shot_id__in=target_shot_ids,
        is_manually_edited=True,
    ).order_by("position"):
        manual_by_shot.setdefault(cue.shot_id, []).append(cue)

    rows = []
    timeline_offset = 0
    total_assets = len(assets)
    if task_id is not None:
        GenerationTask.objects.filter(pk=task_id).update(
            progress_current=0,
            progress_total=total_assets,
            progress_percent=0,
            heartbeat_at=timezone.now(),
        )
    for asset_index, asset in enumerate(assets, start=1):
        shot = asset.shot
        duration_ms = probe_duration_ms(asset)
        segments = subtitle_dialogue_segments(
            shot.dialogue_or_narration,
            speaker_names=shot.character_names,
        )
        parts = [segment[1] for segment in segments]
        alignment = recognize_speech_alignment(
            asset,
            shot.dialogue_or_narration,
            parts,
        )
        if alignment:
            ranges, recognized_text, confidence = alignment
            ranges = [
                None
                if item is None
                else (max(0, item[0]), min(duration_ms, item[1]))
                for item in ranges
            ]
        else:
            speech_start = min(250, max(0, duration_ms // 10))
            speech_end = max(speech_start + 500, duration_ms - speech_start)
            recognized_text = ""
            confidence = 0.5
            ranges = distribute_cues(
                parts,
                speech_start,
                min(duration_ms, speech_end),
            )
        previous_manual = manual_by_shot.get(shot.id, [])
        for local_index, ((cue_text_source, cleaned_text), cue_range) in enumerate(
            zip(segments, ranges)
        ):
            old = previous_manual[local_index] if local_index < len(previous_manual) else None
            reviewed = bool(old and not old.needs_review)
            if old is not None:
                local_start, local_end = _cue_local_times(old)
                cue_text = old.text
                cue_confidence = old.confidence
            elif cue_range is None:
                local_start, local_end = 0, 100
                cue_text = ""
                cue_confidence = 0.0
            else:
                local_start, local_end = cue_range
                cue_text = cleaned_text
                cue_confidence = confidence
            rows.append(
                SubtitleCue(
                    track=track,
                    shot=shot,
                    position=0,
                    source_text=cue_text_source,
                    recognized_text=recognized_text if local_index == 0 else "",
                    text=cue_text,
                    start_ms=max(0, timeline_offset + local_start),
                    end_ms=max(timeline_offset + local_start + 1, timeline_offset + local_end),
                    local_start_ms=max(0, local_start),
                    local_end_ms=max(local_start + 1, local_end),
                    confidence=cue_confidence,
                    needs_review=False if reviewed else cue_confidence < 0.85,
                    is_manually_edited=bool(old),
                )
            )
        timeline_offset += duration_ms
        if task_id is not None:
            GenerationTask.objects.filter(pk=task_id).update(
                progress_current=asset_index,
                progress_total=total_assets,
                progress_percent=int(asset_index * 100 / max(1, total_assets)),
                heartbeat_at=timezone.now(),
            )

    now = timezone.now()
    with transaction.atomic():
        track.cues.filter(shot_id__in=target_shot_ids).delete()
        next_position = (
            track.cues.aggregate(value=Max("position"))["value"] or 0
        ) + 1
        for index, row in enumerate(rows):
            row.position = next_position + index
        SubtitleCue.objects.bulk_create(rows)
        _reindex_track_cues(track)

        for asset in assets:
            shot_rows = [row for row in rows if row.shot_id == asset.shot_id]
            needs_review = any(row.needs_review for row in shot_rows)
            setting, _ = ShotSubtitleSetting.objects.get_or_create(shot=asset.shot)
            setting.status = (
                ShotSubtitleSetting.STATUS_NEEDS_REVIEW
                if needs_review
                else ShotSubtitleSetting.STATUS_CONFIRMED
            )
            setting.source_hash = shot_subtitle_source_hash(asset.shot, asset)
            setting.error_message = ""
            setting.aligned_at = now
            setting.confirmed_at = None if needs_review else now
            setting.revision += 1
            setting.save()

        track.source_hash = source_hash or subtitle_source_hash(track.episode)
        track.style_options = subtitle_style(track.style_options)
        track.error_message = ""
        track.aligned_at = now
        track.revision += 1
        _refresh_track_status(track)
        track.save()
    from studio.services.subtitle_qc import run_and_persist_subtitle_qc

    run_and_persist_subtitle_qc(track, allow_empty=True)
    return track


def _cue_local_times(cue, fallback_offset=0):
    if cue.local_start_ms is not None and cue.local_end_ms is not None:
        return cue.local_start_ms, cue.local_end_ms
    return (
        max(0, cue.start_ms - fallback_offset),
        max(100, cue.end_ms - fallback_offset),
    )


def _reindex_track_cues(track):
    cues = list(track.cues.select_related("shot"))
    cues.sort(
        key=lambda cue: (
            cue.shot.position if cue.shot_id else 1_000_000,
            _cue_local_times(cue)[0],
            cue.position,
            cue.id,
        )
    )
    if not cues:
        return
    track.cues.update(position=F("position") + 1_000_000)
    for position, cue in enumerate(cues, start=1):
        cue.position = position
    SubtitleCue.objects.bulk_update(cues, ["position"])


def _refresh_track_status(track):
    settings = list(
        ShotSubtitleSetting.objects.filter(shot__storyboard__episode=track.episode)
    )
    if any(item.status == ShotSubtitleSetting.STATUS_ALIGNING for item in settings):
        track.status = SubtitleTrack.STATUS_ALIGNING
        track.confirmed_at = None
    elif any(item.status == ShotSubtitleSetting.STATUS_FAILED for item in settings):
        track.status = SubtitleTrack.STATUS_FAILED
        track.confirmed_at = None
    elif track.cues.filter(needs_review=True).exists():
        track.status = SubtitleTrack.STATUS_NEEDS_REVIEW
        track.confirmed_at = None
    elif track.cues.exists():
        track.status = SubtitleTrack.STATUS_CONFIRMED
        track.confirmed_at = timezone.now()
    else:
        track.status = SubtitleTrack.STATUS_DRAFT
        track.confirmed_at = None

def clean_subtitle_text(value):
    text = ROLE_PREFIX_RE.sub("", str(value or "").strip(), count=1)
    text = SUBTITLE_QUOTE_RE.sub("", text)
    return text.strip(" ，,")


def _split_cue_text(value):
    sentence = value
    parts = []
    while len(sentence) > MAX_CUE_TEXT_LENGTH:
        candidates = [
            index + 1
            for index, character in enumerate(sentence[: MAX_CUE_TEXT_LENGTH + 1])
            if character in "，,、"
        ]
        split_at = candidates[-1] if candidates else MAX_CUE_TEXT_LENGTH
        parts.append(sentence[:split_at].strip())
        sentence = sentence[split_at:].strip()
    if sentence:
        parts.append(sentence)
    return parts


def _normalized_label(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def _is_non_speech_label(label):
    normalized = _normalized_label(label)
    return any(keyword in normalized for keyword in NON_SPEECH_LABEL_KEYWORDS)


def _is_speech_label(label, speaker_names):
    normalized = _normalized_label(label)
    names = [
        _normalized_label(name)
        for name in (speaker_names or [])
        if str(name).strip()
    ]
    if names and any(name in normalized for name in names):
        return True
    if any(hint in normalized for hint in SPEECH_LABEL_HINTS):
        return True
    return not speaker_names


def _accepted_speaker_tags(text, speaker_names):
    tags = []
    for match in SPEAKER_TAG_RE.finditer(text):
        label = match.group("label").strip()
        if _is_non_speech_label(label) or _is_speech_label(label, speaker_names):
            tags.append((match, label))
    return tags


def _quoted_turn_content(value):
    content = str(value or "").strip()
    if not content:
        return ""
    closer = QUOTE_PAIRS.get(content[0])
    if closer:
        closing_index = content.find(closer, 1)
        if closing_index >= 0:
            return content[1:closing_index]
    return content


def _append_dialogue_parts(segments, source, content):
    cleaned = SUBTITLE_QUOTE_RE.sub("", content).strip(" ，,")
    for match in SUBTITLE_SENTENCE_RE.finditer(cleaned):
        sentence = match.group(0).strip(" ，,")
        if (
            NO_DIALOGUE_RE.fullmatch(sentence)
            or NON_SPOKEN_SENTENCE_RE.search(sentence)
        ):
            continue
        for part in _split_cue_text(sentence):
            if normalize_text(part):
                segments.append((source, part))


def subtitle_dialogue_segments(value, speaker_names=None):
    text = str(value or "").strip()
    if not text or NO_DIALOGUE_RE.fullmatch(text):
        return []

    tags = _accepted_speaker_tags(text, speaker_names)
    if not tags:
        if speaker_names is not None:
            if not speaker_names or text[0] not in QUOTE_PAIRS:
                return []
        cleaned = clean_subtitle_text(text)
        segments = []
        _append_dialogue_parts(segments, text, cleaned)
        return segments

    segments = []
    for index, (tag, label) in enumerate(tags):
        end = tags[index + 1][0].start() if index + 1 < len(tags) else len(text)
        raw_content = text[tag.end():end]
        if _is_non_speech_label(label):
            continue
        content = _quoted_turn_content(raw_content)
        source = text[tag.start():tag.end()] + raw_content
        _append_dialogue_parts(segments, source.strip(), content)
    return segments


def split_dialogue(value, speaker_names=None):
    return [
        cleaned
        for _, cleaned in subtitle_dialogue_segments(value, speaker_names=speaker_names)
    ]

def distribute_cues(parts, start_ms, end_ms):
    if not parts:
        return []
    usable = max(len(parts) * 100, end_ms - start_ms)
    weights = [max(1, len(normalize_text(part))) for part in parts]
    total_weight = sum(weights)
    ranges = []
    cursor = start_ms
    consumed = 0
    for index, weight in enumerate(weights):
        consumed += weight
        boundary = end_ms if index == len(weights) - 1 else start_ms + round(usable * consumed / total_weight)
        boundary = max(cursor + 100, boundary)
        ranges.append((cursor, boundary))
        cursor = boundary
    return ranges


def recognize_speech_alignment(asset, source_text, parts):
    if os.environ.get("SUBTITLE_ASR_BACKEND", "").lower() != "faster_whisper":
        return None
    if not parts:
        return [], "", 1.0
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "已启用 faster-whisper 字幕对齐，但当前环境没有安装 faster-whisper。"
            ) from exc
        _WHISPER_MODEL = WhisperModel(
            os.environ.get("SUBTITLE_WHISPER_MODEL", "small"),
            device=os.environ.get("SUBTITLE_WHISPER_DEVICE", "cpu"),
            compute_type=os.environ.get("SUBTITLE_WHISPER_COMPUTE_TYPE", "int8"),
        )
    expected_text = "".join(parts)
    vad_filter = os.environ.get(
        "SUBTITLE_WHISPER_VAD_FILTER",
        "true",
    ).lower() in {"1", "true", "yes", "on"}
    segments, _ = _WHISPER_MODEL.transcribe(
        asset.video.path,
        language=os.environ.get("SUBTITLE_LANGUAGE", "zh"),
        word_timestamps=True,
        vad_filter=vad_filter,
        initial_prompt=expected_text,
        condition_on_previous_text=False,
    )
    segments = list(segments)
    if not segments:
        return [None] * len(parts), "", 0.0
    recognized_text = "".join(segment.text.strip() for segment in segments)
    confidence = SequenceMatcher(
        None,
        normalize_text(expected_text or source_text),
        normalize_text(recognized_text),
    ).ratio()
    words = [
        word
        for segment in segments
        for word in (getattr(segment, "words", None) or [])
        if normalize_text(getattr(word, "word", ""))
    ]
    if words:
        ranges = _align_parts_to_words(parts, words)
    else:
        ranges = [None] * len(parts)
    return ranges, recognized_text, confidence


def recognize_speech_window(asset, source_text):
    alignment = recognize_speech_alignment(asset, source_text, [source_text])
    if not alignment:
        return None
    ranges, recognized_text, confidence = alignment
    matched = [item for item in ranges if item is not None]
    if not matched:
        return None
    return matched[0][0], matched[-1][1], recognized_text, confidence


def _align_parts_to_words(parts, words):
    expected_parts = [normalize_text(part) for part in parts]
    expected_text = "".join(expected_parts)
    recognized_characters = []
    character_times = []
    for word in words:
        token = normalize_text(word.word)
        if not token:
            continue
        start_ms = max(0, round(word.start * 1000))
        end_ms = max(start_ms + 1, round(word.end * 1000))
        duration_ms = end_ms - start_ms
        for index, character in enumerate(token):
            recognized_characters.append(character)
            character_times.append(
                (
                    start_ms + round(duration_ms * index / len(token)),
                    start_ms + round(duration_ms * (index + 1) / len(token)),
                )
            )

    recognized_text = "".join(recognized_characters)
    if not expected_text or not recognized_text:
        return [None] * len(parts)

    expected_to_recognized = {}
    matcher = SequenceMatcher(None, expected_text, recognized_text, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            expected_to_recognized[block.a + offset] = block.b + offset

    minimum_match_ratio = float(
        os.environ.get("SUBTITLE_ASR_PART_MIN_CONFIDENCE", "0.55")
    )
    ranges = []
    expected_offset = 0
    for part in expected_parts:
        matched_indexes = [
            expected_to_recognized[index]
            for index in range(expected_offset, expected_offset + len(part))
            if index in expected_to_recognized
        ]
        match_ratio = len(matched_indexes) / max(1, len(part))
        if not matched_indexes or match_ratio < minimum_match_ratio:
            ranges.append(None)
        else:
            start_ms = character_times[min(matched_indexes)][0]
            end_ms = character_times[max(matched_indexes)][1]
            ranges.append((start_ms, max(start_ms + 1, end_ms)))
        expected_offset += len(part)
    return _remove_alignment_overlaps(ranges)


def _remove_alignment_overlaps(ranges):
    normalized = list(ranges)
    previous_index = None
    for index, item in enumerate(normalized):
        if item is None:
            continue
        start_ms, end_ms = item
        if previous_index is not None:
            previous_start, previous_end = normalized[previous_index]
            if start_ms < previous_end:
                boundary = round((start_ms + previous_end) / 2)
                boundary = max(previous_start + 1, min(end_ms - 1, boundary))
                normalized[previous_index] = (previous_start, boundary)
                start_ms = boundary
        if end_ms <= start_ms:
            normalized[index] = None
            continue
        normalized[index] = (start_ms, end_ms)
        previous_index = index
    return normalized

def probe_duration_ms(asset):
    fallback = max(1000, int(asset.shot.duration_seconds * 1000))
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not getattr(asset.video, "path", None):
        return fallback
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                asset.video.path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return max(100, round(float(result.stdout.strip()) * 1000))
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return fallback


def normalize_text(value):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()


def subtitle_style(value=None):
    style = {**DEFAULT_STYLE, **(value or {})}
    style["font_size"] = max(24, min(64, int(style["font_size"])))
    style["outline_size"] = max(0, min(8, int(style["outline_size"])))
    style["margin_bottom"] = max(40, min(260, int(style["margin_bottom"])))
    for key in ("text_color", "outline_color"):
        color = str(style[key]).upper()
        style[key] = color if re.fullmatch(r"#[0-9A-F]{6}", color) else DEFAULT_STYLE[key]
    style["font_name"] = str(style["font_name"] or DEFAULT_STYLE["font_name"])[:120]
    return style


def save_subtitle_style(track, style):
    track.style_options = subtitle_style(style)
    track.revision += 1
    track.save(update_fields=["style_options", "revision", "updated_at"])
    return track


def _prepare_cue_updates(track, cues, confirm_all=False, shot=None):
    cue_map = {
        cue.id: cue
        for cue in track.cues.select_related("shot").all()
    }
    submitted = []
    for item in cues:
        cue = cue_map.get(int(item["id"]))
        if cue is None or (shot is not None and cue.shot_id != shot.id):
            raise ValueError("字幕条目不存在或已被重新生成。")
        text = str(item.get("text") or "").strip()
        start_ms = int(item["start_ms"])
        end_ms = int(item["end_ms"])
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError("字幕结束时间必须晚于开始时间。")

        original_start, original_end = _cue_local_times(cue)
        cue.text = text
        cue.start_ms = start_ms
        cue.end_ms = end_ms
        cue.local_start_ms = start_ms
        cue.local_end_ms = end_ms
        cue.needs_review = False if not text or confirm_all else not bool(item.get("reviewed"))
        cue.is_manually_edited = (
            cue.is_manually_edited
            or cue.text != cue.source_text
            or cue.start_ms != int(item.get("original_start_ms", original_start))
            or cue.end_ms != int(item.get("original_end_ms", original_end))
        )
        submitted.append(cue)

    by_shot = {}
    for cue in submitted:
        if not cue.text:
            continue
        by_shot.setdefault(cue.shot_id, []).append(cue)
    for shot_cues in by_shot.values():
        shot_cues.sort(key=lambda cue: (cue.local_start_ms, cue.position))
        for previous, current in zip(shot_cues, shot_cues[1:]):
            if current.local_start_ms < previous.local_end_ms:
                raise ValueError("同一分镜内的相邻字幕时间不能重叠。")
    return submitted


def save_subtitle_track(track, *, enabled, global_offset_ms, cues, confirm_all=False):
    submitted = _prepare_cue_updates(track, cues, confirm_all=confirm_all)

    with transaction.atomic():
        SubtitleCue.objects.bulk_update(
            submitted,
            [
                "text",
                "start_ms",
                "end_ms",
                "local_start_ms",
                "local_end_ms",
                "needs_review",
                "is_manually_edited",
                "updated_at",
            ],
        )
        track.enabled = bool(enabled)
        track.global_offset_ms = max(-10000, min(10000, int(global_offset_ms)))
        track.revision += 1

        for shot_id in {cue.shot_id for cue in submitted if cue.shot_id}:
            setting, _ = ShotSubtitleSetting.objects.get_or_create(shot_id=shot_id)
            shot_cues = [cue for cue in submitted if cue.shot_id == shot_id]
            setting.status = (
                ShotSubtitleSetting.STATUS_NEEDS_REVIEW
                if any(cue.needs_review for cue in shot_cues)
                else ShotSubtitleSetting.STATUS_CONFIRMED
            )
            setting.confirmed_at = (
                None
                if setting.status == ShotSubtitleSetting.STATUS_NEEDS_REVIEW
                else timezone.now()
            )
            setting.revision += 1
            setting.save()

        _refresh_track_status(track)
        track.save()
    from studio.services.subtitle_qc import run_and_persist_subtitle_qc

    run_and_persist_subtitle_qc(track)
    return track


def save_shot_subtitle(
    track,
    setting,
    *,
    enabled,
    offset_ms,
    cues,
    confirm_all=False,
):
    submitted = _prepare_cue_updates(
        track,
        cues,
        confirm_all=confirm_all,
        shot=setting.shot,
    )
    with transaction.atomic():
        SubtitleCue.objects.bulk_update(
            submitted,
            [
                "text",
                "start_ms",
                "end_ms",
                "local_start_ms",
                "local_end_ms",
                "needs_review",
                "is_manually_edited",
                "updated_at",
            ],
        )
        setting.enabled = bool(enabled)
        setting.offset_ms = max(-10000, min(10000, int(offset_ms)))
        setting.status = (
            ShotSubtitleSetting.STATUS_NEEDS_REVIEW
            if any(cue.needs_review for cue in submitted)
            else ShotSubtitleSetting.STATUS_CONFIRMED
        )
        setting.confirmed_at = (
            None
            if setting.status == ShotSubtitleSetting.STATUS_NEEDS_REVIEW
            else timezone.now()
        )
        setting.revision += 1
        setting.save()
        track.revision += 1
        _refresh_track_status(track)
        track.save()
    from studio.services.subtitle_qc import run_and_persist_subtitle_qc

    run_and_persist_subtitle_qc(track)
    return setting


def is_shot_subtitle_stale(setting, asset=None):
    if not setting or not setting.source_hash:
        return False
    if asset is None:
        asset = setting.shot.video_assets.filter(
            is_selected=True,
            status=VideoAsset.STATUS_READY,
        ).first()
    return setting.source_hash != shot_subtitle_source_hash(setting.shot, asset)


def is_subtitle_stale(track):
    settings = list(
        ShotSubtitleSetting.objects.filter(
            shot__storyboard__episode=track.episode,
        ).select_related("shot")
    )
    if any(
        setting.enabled and is_shot_subtitle_stale(setting)
        for setting in settings
    ):
        return True
    managed_shot_ids = [setting.shot_id for setting in settings]
    has_legacy_cues = track.cues.exclude(shot_id__in=managed_shot_ids).exists()
    return bool(
        has_legacy_cues
        and track.source_hash
        and track.source_hash != subtitle_source_hash(track.episode)
    )

def subtitle_snapshot(track, assets=None):
    shots = list(
        StoryboardShot.objects.filter(storyboard__episode=track.episode)
        .order_by("position", "shot_number", "id")
    )
    if assets is None:
        assets = selected_episode_assets(track.episode)
    asset_map = {
        asset.shot_id: asset
        for asset in assets
        if asset is not None
    }
    settings = {
        setting.shot_id: setting
        for setting in ShotSubtitleSetting.objects.filter(
            shot__storyboard__episode=track.episode
        )
    }
    cues_by_shot = {}
    for cue in track.cues.select_related("shot").order_by("position", "id"):
        cues_by_shot.setdefault(cue.shot_id, []).append(cue)

    base_style = subtitle_style(track.style_options)
    cues = []
    shot_timeline = []
    timeline_offset = 0
    for shot in shots:
        asset = asset_map.get(shot.id)
        duration_ms = (
            probe_duration_ms(asset)
            if asset is not None
            else max(1000, int(shot.duration_seconds * 1000))
        )
        shot_timeline.append(
            {"shot_id": str(shot.shot_id), "duration_ms": duration_ms}
        )
        setting = settings.get(shot.id)
        if setting is None or setting.enabled:
            shot_offset = setting.offset_ms if setting else 0
            for cue in cues_by_shot.get(shot.id, []):
                if not cue.text.strip():
                    continue
                local_start, local_end = _cue_local_times(
                    cue,
                    fallback_offset=timeline_offset,
                )
                start_ms = max(
                    0,
                    timeline_offset + local_start + shot_offset + track.global_offset_ms,
                )
                end_ms = max(
                    start_ms + 100,
                    timeline_offset + local_end + shot_offset + track.global_offset_ms,
                )
                cues.append(
                    {
                        "position": len(cues) + 1,
                        "shot_id": str(shot.shot_id),
                        "text": cue.text,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "local_start_ms": local_start,
                        "local_end_ms": local_end,
                        "shot_offset_ms": shot_offset,
                        "style": base_style,
                    }
                )
        timeline_offset += duration_ms

    return {
        "track_id": track.id,
        "revision": track.revision,
        "style": base_style,
        "global_offset_ms": track.global_offset_ms,
        "shots": shot_timeline,
        "cues": cues,
    }

def retime_subtitle_snapshot(
    snapshot,
    normalized_durations_ms,
    *,
    max_timeline_drift_ms=None,
):
    source_shots = list(snapshot.get("shots") or [])
    if not source_shots:
        return snapshot
    if len(source_shots) != len(normalized_durations_ms):
        raise ValueError("字幕时间轴与标准化视频片段数量不一致。")

    normalized_durations = [max(100, int(value)) for value in normalized_durations_ms]
    drift_limit = (
        int(max_timeline_drift_ms)
        if max_timeline_drift_ms is not None
        else int(os.environ.get("SUBTITLE_MAX_TIMELINE_DRIFT_MS", "1000"))
    )
    boundary_tolerance = int(
        os.environ.get("SUBTITLE_CUE_BOUNDARY_TOLERANCE_MS", "300")
    )
    source_offset = 0
    normalized_offset = 0
    max_drift = 0
    shot_offsets = {}
    for index, (source_shot, normalized_duration) in enumerate(
        zip(source_shots, normalized_durations),
        start=1,
    ):
        shot_id = str(source_shot["shot_id"])
        source_duration = max(100, int(source_shot["duration_ms"]))
        shot_offsets[shot_id] = {
            "source_offset_ms": source_offset,
            "normalized_offset_ms": normalized_offset,
            "normalized_duration_ms": normalized_duration,
        }
        source_offset += source_duration
        normalized_offset += normalized_duration
        drift = abs(normalized_offset - source_offset)
        max_drift = max(max_drift, drift)
        if drift > drift_limit:
            raise ValueError(
                "字幕时间轴校验失败："
                f"第 {index} 个镜头后累计偏差 {drift}ms，"
                f"超过允许值 {drift_limit}ms。"
            )

    global_offset = int(snapshot.get("global_offset_ms") or 0)
    retimed_cues = []
    for cue in snapshot.get("cues", []):
        timing = shot_offsets.get(str(cue.get("shot_id")))
        if timing is None:
            raise ValueError("字幕条目对应的镜头不在本次导出时间轴中。")
        source_timeline_offset = timing["source_offset_ms"]
        local_start = int(
            cue.get("local_start_ms", cue["start_ms"] - source_timeline_offset)
        )
        local_end = int(
            cue.get("local_end_ms", cue["end_ms"] - source_timeline_offset)
        )
        shot_offset = int(cue.get("shot_offset_ms") or 0)
        normalized_timeline_offset = timing["normalized_offset_ms"]
        start_ms = max(
            0,
            normalized_timeline_offset + local_start + shot_offset + global_offset,
        )
        end_ms = max(
            start_ms + 100,
            normalized_timeline_offset + local_end + shot_offset + global_offset,
        )
        shot_end = normalized_timeline_offset + timing["normalized_duration_ms"]
        if end_ms > shot_end + boundary_tolerance:
            raise ValueError(
                "字幕时间轴校验失败："
                f"第 {cue.get('position', '?')} 条字幕超出所属镜头 "
                f"{end_ms - shot_end}ms。"
            )
        retimed_cues.append(
            {**cue, "start_ms": start_ms, "end_ms": end_ms}
        )

    return {
        **snapshot,
        "shots": [
            {
                **shot,
                "normalized_duration_ms": normalized_duration,
            }
            for shot, normalized_duration in zip(
                source_shots,
                normalized_durations,
            )
        ],
        "cues": retimed_cues,
        "timeline": {
            "source_duration_ms": source_offset,
            "normalized_duration_ms": normalized_offset,
            "max_drift_ms": max_drift,
        },
    }


def render_srt(snapshot):
    blocks = []
    for index, cue in enumerate(snapshot.get("cues", []), start=1):
        blocks.append(
            f"{index}\n{format_srt_time(cue['start_ms'])} --> {format_srt_time(cue['end_ms'])}\n{cue['text']}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_ass(snapshot):
    base_style = subtitle_style(snapshot.get("style"))
    styles = [("Default", base_style)]
    style_names = {
        json.dumps(base_style, sort_keys=True, ensure_ascii=True): "Default"
    }
    cue_style_names = []
    for cue in snapshot.get("cues", []):
        cue_style = subtitle_style(cue.get("style") or base_style)
        key = json.dumps(cue_style, sort_keys=True, ensure_ascii=True)
        if key not in style_names:
            name = f"ShotStyle{len(styles)}"
            style_names[key] = name
            styles.append((name, cue_style))
        cue_style_names.append(style_names[key])

    style_rows = "\n".join(
        _ass_style_row(name, style)
        for name, style in styles
    )
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
{style_rows}

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events = []
    for cue, style_name in zip(snapshot.get("cues", []), cue_style_names):
        text = ass_escape(cue["text"])
        events.append(
            f"Dialogue: 0,{format_ass_time(cue['start_ms'])},{format_ass_time(cue['end_ms'])},{style_name},,0,0,0,,{text}"
        )
    return header + "\n".join(events) + ("\n" if events else "")


def _ass_style_row(name, style):
    return (
        f"Style: {name},{style['font_name']},{style['font_size']},"
        f"{ass_color(style['text_color'])},{ass_color(style['text_color'])},"
        f"{ass_color(style['outline_color'])},&H64000000,0,0,0,0,100,100,0,0,1,"
        f"{style['outline_size']},0,2,46,46,{style['margin_bottom']},1"
    )

def format_srt_time(milliseconds):
    hours, remainder = divmod(max(0, int(milliseconds)), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def format_ass_time(milliseconds):
    hours, remainder = divmod(max(0, int(milliseconds)), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


def ass_color(value):
    red, green, blue = value[1:3], value[3:5], value[5:7]
    return f"&H00{blue}{green}{red}"


def ass_escape(value):
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )

def subtitle_page_data(episode, shots):
    track = (
        SubtitleTrack.objects.filter(episode=episode)
        .prefetch_related("cues__shot")
        .first()
    )
    base_style = subtitle_style(track.style_options if track else None)
    from studio.services.subtitle_qc import subtitle_qc_is_current

    qc_current = subtitle_qc_is_current(track) if track else False
    settings = {
        setting.shot_id: setting
        for setting in ShotSubtitleSetting.objects.filter(
            shot__storyboard__episode=episode
        ).select_related("shot")
    }
    cues_by_shot = {}
    if track is not None:
        for cue in track.cues.select_related("shot").order_by("position", "id"):
            cues_by_shot.setdefault(cue.shot_id, []).append(cue)

    all_cues = []
    timeline_offset = 0
    any_stale = False
    legacy_track_stale = bool(
        track
        and track.source_hash
        and track.source_hash != subtitle_source_hash(episode)
    )
    for shot in shots:
        selected = getattr(shot, "selected_video", None)
        selected_url = selected.video.url if selected and selected.video else ""
        setting = settings.get(shot.id)
        shot_cues = cues_by_shot.get(shot.id, [])
        shot_stale = (
            is_shot_subtitle_stale(setting, selected)
            if setting
            else bool(shot_cues and legacy_track_stale)
        )
        any_stale = any_stale or shot_stale
        for cue in shot_cues:
            local_start, local_end = _cue_local_times(
                cue,
                fallback_offset=timeline_offset,
            )
            cue.start_seconds = f"{local_start / 1000:.2f}"
            cue.end_seconds = f"{local_end / 1000:.2f}"
            cue.preview_start_seconds = local_start / 1000
            cue.preview_url = selected_url
            cue.confidence_percent = round(cue.confidence * 100)
            cue.is_hidden = not bool(cue.text.strip())
        all_cues.extend(shot_cues)

        if setting and setting.status == ShotSubtitleSetting.STATUS_ALIGNING:
            status = "aligning"
            status_label = "对齐中"
        elif setting and setting.status == ShotSubtitleSetting.STATUS_FAILED:
            status = "failed"
            status_label = "生成失败"
        elif shot_stale:
            status = "stale"
            status_label = "需重新对齐"
        elif setting and not setting.enabled:
            status = "disabled"
            status_label = "已关闭"
        elif not shot_cues:
            status = "missing"
            status_label = "未生成"
        elif all(cue.is_hidden for cue in shot_cues):
            status = "hidden"
            status_label = "已隐藏"
        elif any(cue.needs_review for cue in shot_cues):
            status = "needs_review"
            status_label = "待校对"
        else:
            status = "confirmed"
            status_label = "已确认"

        shot.subtitle_setting_for_page = setting
        shot.subtitle_cues_for_page = shot_cues
        shot.subtitle_count = len(shot_cues)
        shot.subtitle_visible_count = sum(not cue.is_hidden for cue in shot_cues)
        shot.subtitle_hidden_count = sum(cue.is_hidden for cue in shot_cues)
        shot.subtitle_review_count = sum(cue.needs_review for cue in shot_cues)
        shot.subtitle_status = status
        shot.subtitle_status_label = status_label
        shot.subtitle_is_stale = shot_stale
        shot.subtitle_style = base_style
        shot.subtitle_offset_ms = setting.offset_ms if setting else 0
        timeline_offset += max(1000, int(shot.duration_seconds * 1000))

    if track is None:
        return {
            "track": None,
            "style": base_style,
            "cues": [],
            "cue_count": 0,
            "visible_count": 0,
            "hidden_count": 0,
            "review_count": 0,
            "is_stale": False,
            "qc_current": False,
            "qc_passed": False,
            "qc_blocked": False,
        }
    return {
        "track": track,
        "style": base_style,
        "cues": all_cues,
        "cue_count": len(all_cues),
        "visible_count": sum(not cue.is_hidden for cue in all_cues),
        "hidden_count": sum(cue.is_hidden for cue in all_cues),
        "review_count": sum(1 for cue in all_cues if cue.needs_review),
        "is_stale": any_stale,
        "qc_current": qc_current,
        "qc_passed": qc_current and track.qc_status == track.QC_PASSED,
        "qc_blocked": qc_current and track.qc_status == track.QC_FAILED,
    }


def subtitle_status_data(episode):
    from studio.services.subtitle_qc import subtitle_qc_is_current

    track = SubtitleTrack.objects.filter(episode=episode).first()
    settings = list(
        ShotSubtitleSetting.objects.filter(
            shot__storyboard__episode=episode
        ).select_related("shot")
    )
    if track is None:
        return {
            "status": "missing",
            "enabled": False,
            "cue_count": 0,
            "review_count": 0,
            "is_stale": False,
            "qc_status": SubtitleTrack.QC_PENDING,
            "qc_score": None,
            "qc_reason": "",
            "qc_current": False,
            "shots": [],
        }
    return {
        "status": track.status,
        "enabled": track.enabled,
        "cue_count": track.cues.count(),
        "review_count": track.cues.filter(needs_review=True).count(),
        "is_stale": is_subtitle_stale(track),
        "qc_status": track.qc_status,
        "qc_score": track.qc_score,
        "qc_reason": track.qc_reason,
        "qc_current": subtitle_qc_is_current(track),
        "shots": [
            {
                "shot_id": str(setting.shot.shot_id),
                "status": setting.status,
                "enabled": setting.enabled,
                "is_stale": is_shot_subtitle_stale(setting),
            }
            for setting in settings
        ],
    }
