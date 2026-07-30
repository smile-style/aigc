import os
from pathlib import Path

from PIL import ImageFont


DEFAULT_COVER_TITLE_MIN_CHARACTERS = 6
MAX_COVER_TITLE_CHARACTERS = 10


def normalize_cover_title(value):
    compact = "".join(
        character for character in str(value or "").strip() if character.isalnum()
    )
    if not compact or len(compact) > MAX_COVER_TITLE_CHARACTERS:
        raise ValueError("封面标题不能为空，且最多 10 个有效字符。")
    return compact


def default_episode_cover_title(episode):
    episode_title = "".join(
        character for character in str(episode.title or "") if character.isalnum()
    )
    if len(episode_title) >= DEFAULT_COVER_TITLE_MIN_CHARACTERS:
        return episode_title[:MAX_COVER_TITLE_CHARACTERS]

    candidates = [
        (episode.plan_payload or {}).get("next_crisis"),
        episode.cliffhanger,
        (episode.plan_payload or {}).get("resolution_or_reversal"),
        episode.key_conflict,
        episode.summary,
    ]
    units = episode_title
    seen_candidates = set()
    for candidate in candidates:
        compact = "".join(
            character for character in str(candidate or "") if character.isalnum()
        )
        if not compact or compact in seen_candidates:
            continue
        seen_candidates.add(compact)
        if not units and len(compact) >= DEFAULT_COVER_TITLE_MIN_CHARACTERS:
            return compact[:MAX_COVER_TITLE_CHARACTERS]
        for character in compact:
            if len(units) >= DEFAULT_COVER_TITLE_MIN_CHARACTERS:
                break
            if character not in units:
                units += character
    fallback = "危机突然提前降临"
    for character in fallback:
        if len(units) >= DEFAULT_COVER_TITLE_MIN_CHARACTERS:
            break
        units += character
    return units[:MAX_COVER_TITLE_CHARACTERS]


def split_cover_title(title):
    normalized = normalize_cover_title(title)
    if len(normalized) <= 7:
        return [normalized]
    split_at = (len(normalized) + 1) // 2
    return [normalized[:split_at], normalized[split_at:]]


def text_size(draw, text, font):
    bbox = draw.textbbox(
        (0, 0), text, font=font, stroke_width=max(2, font.size // 20)
    )
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def load_font(size):
    configured = os.environ.get("COVER_FONT_PATH", "").strip()
    candidates = [
        configured,
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    raise ValueError(
        "找不到可用的中文封面字体，请通过 COVER_FONT_PATH 配置字体文件。"
    )
