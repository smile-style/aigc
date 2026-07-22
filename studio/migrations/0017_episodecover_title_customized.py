from django.db import migrations, models


MIN_TITLE_CHARACTERS = 6
MAX_TITLE_CHARACTERS = 10


def _compact(value):
    return "".join(character for character in str(value or "") if character.isalnum())


def _legacy_default_title(episode):
    candidates = [
        (episode.plan_payload or {}).get("next_crisis"),
        episode.cliffhanger,
        (episode.plan_payload or {}).get("resolution_or_reversal"),
        episode.title,
        episode.key_conflict,
        episode.summary,
    ]
    units = ""
    seen = set()
    for candidate in candidates:
        compact = _compact(candidate)
        if not compact or compact in seen:
            continue
        seen.add(compact)
        if len(compact) >= MIN_TITLE_CHARACTERS:
            return compact[:MAX_TITLE_CHARACTERS]
        units += compact
        if len(units) >= MIN_TITLE_CHARACTERS:
            break
    for character in "危机突然提前降临":
        if len(units) >= MIN_TITLE_CHARACTERS:
            break
        units += character
    return units[:MAX_TITLE_CHARACTERS]


def _episode_default_title(episode):
    episode_title = _compact(episode.title)
    if len(episode_title) >= MIN_TITLE_CHARACTERS:
        return episode_title[:MAX_TITLE_CHARACTERS]

    units = episode_title
    candidates = [
        (episode.plan_payload or {}).get("next_crisis"),
        episode.cliffhanger,
        (episode.plan_payload or {}).get("resolution_or_reversal"),
        episode.key_conflict,
        episode.summary,
    ]
    seen = set()
    for candidate in candidates:
        compact = _compact(candidate)
        if not compact or compact in seen:
            continue
        seen.add(compact)
        if not units and len(compact) >= MIN_TITLE_CHARACTERS:
            return compact[:MAX_TITLE_CHARACTERS]
        for character in compact:
            if len(units) >= MIN_TITLE_CHARACTERS:
                break
            if character not in units:
                units += character
    for character in "危机突然提前降临":
        if len(units) >= MIN_TITLE_CHARACTERS:
            break
        units += character
    return units[:MAX_TITLE_CHARACTERS]


def mark_existing_custom_titles(apps, schema_editor):
    EpisodeCover = apps.get_model("studio", "EpisodeCover")
    for cover in EpisodeCover.objects.select_related("episode").iterator():
        automatic_titles = {
            _legacy_default_title(cover.episode),
            _episode_default_title(cover.episode),
        }
        if cover.title not in automatic_titles:
            cover.title_customized = True
            cover.save(update_fields=["title_customized"])


def reset_custom_titles(apps, schema_editor):
    EpisodeCover = apps.get_model("studio", "EpisodeCover")
    EpisodeCover.objects.update(title_customized=False)


class Migration(migrations.Migration):
    dependencies = [("studio", "0016_cover_assets")]

    operations = [
        migrations.AddField(
            model_name="episodecover",
            name="title_customized",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_existing_custom_titles, reset_custom_titles),
    ]
