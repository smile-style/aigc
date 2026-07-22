import hashlib
import json
import os
import re
import shutil
import subprocess
from difflib import SequenceMatcher

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from studio.models import GenerationTask, StoryboardShot, SubtitleCue, SubtitleTrack, VideoAsset


DEFAULT_STYLE = {
    "font_name": "Noto Sans CJK SC",
    "font_size": 38,
    "text_color": "#FFFFFF",
    "outline_color": "#000000",
    "outline_size": 3,
    "margin_bottom": 92,
}
MAX_CUE_TEXT_LENGTH = 18
ROLE_PREFIX_RE = re.compile(r"^[^：:\n]{1,12}[：:]\s*")
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


def queue_subtitle_alignment(episode):
    active = episode.script.project.generation_tasks.filter(
        task_type=GenerationTask.TYPE_SUBTITLE_ALIGN,
        status__in=[GenerationTask.STATUS_PENDING, GenerationTask.STATUS_RUNNING],
    ).filter(
        Q(input_snapshot__episode_id=episode.id) | Q(target_id=str(episode.id))
    ).first()
    if active:
        return active, False

    assets = selected_episode_assets(episode)
    if not assets or any(asset is None for asset in assets):
        raise ValueError("所有分镜都生成并选定视频后才能生成字幕。")

    track, _ = SubtitleTrack.objects.get_or_create(episode=episode)
    track.enabled = True
    track.status = SubtitleTrack.STATUS_ALIGNING
    track.error_message = ""
    track.save(update_fields=["enabled", "status", "error_message", "updated_at"])
    task = GenerationTask.objects.create(
        project=episode.script.project,
        task_type=GenerationTask.TYPE_SUBTITLE_ALIGN,
        target_id=str(track.id),
        input_snapshot={
            "episode_id": episode.id,
            "subtitle_track_id": track.id,
            "video_asset_ids": [asset.id for asset in assets],
            "source_hash": subtitle_source_hash(episode, assets),
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
            source_hash=task.input_snapshot["source_hash"],
        )
        GenerationTask.objects.filter(pk=task.pk).update(
            status=GenerationTask.STATUS_SUCCEEDED,
            result_snapshot={"subtitle_track_id": track.id, "cue_count": track.cues.count()},
            error_message="",
            finished_at=timezone.now(),
        )
    except Exception as exc:
        track.status = SubtitleTrack.STATUS_FAILED
        track.error_message = str(exc)
        track.save(update_fields=["status", "error_message", "updated_at"])
        GenerationTask.objects.filter(pk=task.pk).update(
            status=GenerationTask.STATUS_FAILED,
            error_message=str(exc),
            finished_at=timezone.now(),
        )
    return track


def generate_subtitle_cues(track, assets, source_hash=None):
    manual_by_shot = {}
    for cue in track.cues.filter(is_manually_edited=True).order_by("position"):
        manual_by_shot.setdefault(cue.shot_id, []).append(cue)

    rows = []
    timeline_offset = 0
    position = 1
    for asset in assets:
        shot = asset.shot
        duration_ms = probe_duration_ms(asset)
        parts = split_dialogue(shot.dialogue_or_narration)
        recognized = recognize_speech_window(asset, shot.dialogue_or_narration)
        if recognized:
            speech_start, speech_end, recognized_text, confidence = recognized
        else:
            speech_start = min(250, max(0, duration_ms // 10))
            speech_end = max(speech_start + 500, duration_ms - speech_start)
            recognized_text = ""
            confidence = 0.5

        ranges = distribute_cues(parts, speech_start, min(duration_ms, speech_end))
        previous_manual = manual_by_shot.get(shot.id, [])
        for local_index, (text, (start_ms, end_ms)) in enumerate(zip(parts, ranges)):
            old = previous_manual[local_index] if local_index < len(previous_manual) else None
            cue_text = old.text if old else text
            reviewed = bool(old and not old.needs_review)
            rows.append(
                SubtitleCue(
                    track=track,
                    shot=shot,
                    position=position,
                    source_text=text,
                    recognized_text=recognized_text if local_index == 0 else "",
                    text=cue_text,
                    start_ms=max(0, timeline_offset + start_ms),
                    end_ms=max(timeline_offset + start_ms + 100, timeline_offset + end_ms),
                    confidence=confidence,
                    needs_review=False if reviewed else confidence < 0.85,
                    is_manually_edited=bool(old),
                )
            )
            position += 1
        timeline_offset += duration_ms

    with transaction.atomic():
        track.cues.all().delete()
        SubtitleCue.objects.bulk_create(rows)
        needs_review = any(row.needs_review for row in rows)
        track.source_hash = source_hash or subtitle_source_hash(track.episode, assets)
        track.style_options = subtitle_style(track.style_options)
        track.status = (
            SubtitleTrack.STATUS_NEEDS_REVIEW
            if needs_review
            else SubtitleTrack.STATUS_CONFIRMED
        )
        track.error_message = ""
        track.aligned_at = timezone.now()
        track.confirmed_at = None if needs_review else timezone.now()
        track.revision += 1
        track.save()
    return track


def split_dialogue(value):
    text = str(value or "").strip()
    if not text:
        return []
    text = ROLE_PREFIX_RE.sub("", text)
    sentences = [
        item.strip(" ，,")
        for item in re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", text)
        if item.strip(" ，,")
    ]
    parts = []
    for sentence in sentences:
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


def recognize_speech_window(asset, source_text):
    if os.environ.get("SUBTITLE_ASR_BACKEND", "").lower() != "faster_whisper":
        return None
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "已启用 faster-whisper 字幕对齐，但当前环境没有安装 faster-whisper。"
        ) from exc

    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = WhisperModel(
            os.environ.get("SUBTITLE_WHISPER_MODEL", "large-v3-turbo"),
            device=os.environ.get("SUBTITLE_WHISPER_DEVICE", "auto"),
            compute_type=os.environ.get("SUBTITLE_WHISPER_COMPUTE_TYPE", "default"),
        )
    segments, _ = _WHISPER_MODEL.transcribe(
        asset.video.path,
        language=os.environ.get("SUBTITLE_LANGUAGE", "zh"),
        word_timestamps=True,
        vad_filter=True,
    )
    segments = list(segments)
    if not segments:
        return None
    recognized_text = "".join(segment.text.strip() for segment in segments)
    confidence = SequenceMatcher(
        None,
        normalize_text(source_text),
        normalize_text(recognized_text),
    ).ratio()
    return (
        max(0, round(segments[0].start * 1000)),
        max(100, round(segments[-1].end * 1000)),
        recognized_text,
        confidence,
    )


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


def save_subtitle_track(track, *, enabled, global_offset_ms, style, cues, confirm_all=False):
    cue_map = {cue.id: cue for cue in track.cues.all()}
    submitted = []
    for item in cues:
        cue = cue_map.get(int(item["id"]))
        if cue is None:
            raise ValueError("字幕条目不存在或已被重新生成。")
        text = str(item.get("text") or "").strip()
        start_ms = int(item["start_ms"])
        end_ms = int(item["end_ms"])
        if not text:
            raise ValueError("字幕文字不能为空。")
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError("字幕结束时间必须晚于开始时间。")
        cue.text = text
        cue.start_ms = start_ms
        cue.end_ms = end_ms
        cue.needs_review = False if confirm_all else not bool(item.get("reviewed"))
        cue.is_manually_edited = (
            cue.is_manually_edited
            or cue.text != cue.source_text
            or cue.start_ms != int(item.get("original_start_ms", cue.start_ms))
            or cue.end_ms != int(item.get("original_end_ms", cue.end_ms))
        )
        submitted.append(cue)

    submitted.sort(key=lambda cue: (cue.start_ms, cue.position))
    for previous, current in zip(submitted, submitted[1:]):
        if current.start_ms < previous.end_ms:
            raise ValueError("相邻字幕时间不能重叠。")

    with transaction.atomic():
        SubtitleCue.objects.bulk_update(
            submitted,
            ["text", "start_ms", "end_ms", "needs_review", "is_manually_edited", "updated_at"],
        )
        track.enabled = bool(enabled)
        track.global_offset_ms = max(-10000, min(10000, int(global_offset_ms)))
        track.style_options = subtitle_style(style)
        remaining = any(cue.needs_review for cue in submitted)
        track.status = (
            SubtitleTrack.STATUS_NEEDS_REVIEW
            if remaining
            else SubtitleTrack.STATUS_CONFIRMED
        )
        track.confirmed_at = None if remaining else timezone.now()
        track.revision += 1
        track.save()
    return track


def is_subtitle_stale(track):
    return bool(track.source_hash and track.source_hash != subtitle_source_hash(track.episode))


def subtitle_snapshot(track):
    offset = track.global_offset_ms
    cues = []
    for cue in track.cues.order_by("position", "id"):
        start_ms = max(0, cue.start_ms + offset)
        end_ms = max(start_ms + 100, cue.end_ms + offset)
        cues.append(
            {
                "position": cue.position,
                "text": cue.text,
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )
    return {
        "track_id": track.id,
        "revision": track.revision,
        "style": subtitle_style(track.style_options),
        "cues": cues,
    }


def render_srt(snapshot):
    blocks = []
    for index, cue in enumerate(snapshot.get("cues", []), start=1):
        blocks.append(
            f"{index}\n{format_srt_time(cue['start_ms'])} --> {format_srt_time(cue['end_ms'])}\n{cue['text']}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_ass(snapshot):
    style = subtitle_style(snapshot.get("style"))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{style['font_name']},{style['font_size']},{ass_color(style['text_color'])},{ass_color(style['text_color'])},{ass_color(style['outline_color'])},&H64000000,0,0,0,0,100,100,0,0,1,{style['outline_size']},0,2,46,46,{style['margin_bottom']},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events = []
    for cue in snapshot.get("cues", []):
        text = ass_escape(cue["text"])
        events.append(
            f"Dialogue: 0,{format_ass_time(cue['start_ms'])},{format_ass_time(cue['end_ms'])},Default,,0,0,0,,{text}"
        )
    return header + "\n".join(events) + ("\n" if events else "")


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
    if track is None:
        return {
            "track": None,
            "style": subtitle_style(),
            "cues": [],
            "cue_count": 0,
            "review_count": 0,
            "is_stale": False,
        }

    offsets = {}
    selected_urls = {}
    offset_ms = 0
    for shot in shots:
        offsets[shot.id] = offset_ms
        selected = getattr(shot, "selected_video", None)
        selected_urls[shot.id] = selected.video.url if selected and selected.video else ""
        offset_ms += max(1000, int(shot.duration_seconds * 1000))

    cues = list(track.cues.select_related("shot").order_by("position", "id"))
    for cue in cues:
        shot_offset = offsets.get(cue.shot_id, 0)
        cue.start_seconds = f"{cue.start_ms / 1000:.2f}"
        cue.end_seconds = f"{cue.end_ms / 1000:.2f}"
        cue.preview_start_seconds = max(0, cue.start_ms - shot_offset) / 1000
        cue.preview_url = selected_urls.get(cue.shot_id, "")
        cue.confidence_percent = round(cue.confidence * 100)

    return {
        "track": track,
        "style": subtitle_style(track.style_options),
        "cues": cues,
        "cue_count": len(cues),
        "review_count": sum(1 for cue in cues if cue.needs_review),
        "is_stale": is_subtitle_stale(track),
    }


def subtitle_status_data(episode):
    track = SubtitleTrack.objects.filter(episode=episode).first()
    if track is None:
        return {
            "status": "missing",
            "enabled": False,
            "cue_count": 0,
            "review_count": 0,
            "is_stale": False,
        }
    return {
        "status": track.status,
        "enabled": track.enabled,
        "cue_count": track.cues.count(),
        "review_count": track.cues.filter(needs_review=True).count(),
        "is_stale": is_subtitle_stale(track),
    }

