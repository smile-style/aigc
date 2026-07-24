from io import BytesIO
from types import SimpleNamespace

import pytest
from django.test import override_settings
from PIL import Image

from studio.models import Episode, Outline, Project, Script
from studio.services.covers import (
    build_cover_prompt,
    decorate_cover_workspace,
    default_episode_cover_title,
    normalize_cover_title,
    render_episode_cover,
    save_cover_template,
)


def make_image_result(size=(1024, 1536)):
    buffer = BytesIO()
    Image.new("RGB", size, "#385269").save(buffer, format="PNG")
    return SimpleNamespace(
        content=buffer.getvalue(),
        extension=".png",
        source_url="",
        model="cover-test-model",
    )


def create_script_with_episodes():
    project = Project.objects.create(
        workspace_id="cover-workspace",
        name="Cover project",
        genre="都市",
        episode_count=2,
        episode_duration_minutes=2,
    )
    outline = Outline.objects.create(
        project=project,
        outline_id="cover-outline",
        position=1,
        title="逆风翻盘",
        core_premise="被夺走公司后重新创业",
        protagonist="林夏，冷静果断的创业者",
        hook="她发现合伙人早已背叛",
        arc_summary="林夏逐步夺回公司",
    )
    project.selected_outline = outline
    project.save(update_fields=["selected_outline"])
    script = Script.objects.create(project=project, outline=outline)
    Episode.objects.create(
        script=script,
        episode_number=1,
        title="仓库危机爆发",
        summary="主角赶往仓库",
        key_conflict="货款和仓库同时出事",
        cliffhanger="仓库一夜被抢空",
        plan_payload={"next_crisis": "仓库一夜被抢空"},
    )
    Episode.objects.create(
        script=script,
        episode_number=2,
        title="真相终于出现",
        summary="主角寻找内鬼",
        key_conflict="家人拒绝提供证据",
        cliffhanger="内鬼竟是身边人",
        plan_payload={"resolution_or_reversal": "亲妈当众撕毁婚约"},
    )
    return script


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("仓库一夜被抢空", "仓库一夜被抢空"),
        ("粮山入库", "粮山入库"),
        ("钱没到账，灾难先来！", "钱没到账灾难先来"),
        (" 亲妈当众撕毁婚约 ", "亲妈当众撕毁婚约"),
    ],
)
def test_normalize_cover_title_accepts_one_to_ten_effective_characters(value, expected):
    assert normalize_cover_title(value) == expected


@pytest.mark.parametrize("value", ["", "！？", "这是一条明显超过十个字符的封面标题"])
def test_normalize_cover_title_rejects_invalid_length(value):
    with pytest.raises(ValueError, match="最多 10"):
        normalize_cover_title(value)


@pytest.mark.django_db
def test_default_episode_cover_title_prefers_episode_title():
    episode = create_script_with_episodes().episodes.get(episode_number=1)

    assert default_episode_cover_title(episode) == "仓库危机爆发"


@pytest.mark.django_db
def test_default_episode_cover_title_truncates_long_episode_title():
    episode = create_script_with_episodes().episodes.get(episode_number=1)
    episode.title = "仓库危机突然爆发幕后黑手现身"

    assert default_episode_cover_title(episode) == "仓库危机突然爆发幕后"


@pytest.mark.django_db
def test_default_episode_cover_title_keeps_short_episode_title_as_prefix():
    episode = create_script_with_episodes().episodes.get(episode_number=1)
    episode.title = "仓库危机"

    title = default_episode_cover_title(episode)

    assert title.startswith("仓库危机")
    assert 6 <= len(title) <= 10


@pytest.mark.django_db
def test_build_cover_prompt_reserves_title_area_and_forbids_text():
    script = create_script_with_episodes()

    prompt = build_cover_prompt(script)

    assert script.outline.title in prompt
    assert script.outline.protagonist in prompt
    assert "16:10" in prompt
    assert "无文字" in prompt
    assert "标题区域" in prompt


@pytest.mark.django_db
def test_save_cover_template_renders_all_episode_covers(tmp_path):
    script = create_script_with_episodes()
    with override_settings(MEDIA_ROOT=tmp_path):
        template = save_cover_template(
            script,
            make_image_result(),
            prompt_snapshot="无字封面测试提示词",
        )

        assert template.version == 1
        assert template.background.name
        covers = list(template.episode_covers.select_related("episode").order_by("episode__episode_number"))
        assert len(covers) == 2
        assert covers[0].title == "仓库危机爆发"
        assert covers[0].title_customized is False
        covers[0].image.open("rb")
        try:
            rendered = Image.open(covers[0].image)
            assert rendered.size == (1600, 1000)
            assert rendered.format == "JPEG"
        finally:
            covers[0].image.close()


@pytest.mark.django_db
def test_render_episode_cover_reuses_background_when_title_changes(tmp_path):
    script = create_script_with_episodes()
    with override_settings(MEDIA_ROOT=tmp_path):
        template = save_cover_template(script, make_image_result(), "prompt")
        episode = script.episodes.get(episode_number=1)
        background_name = template.background.name

        cover = render_episode_cover(
            template,
            episode,
            "钱没到账灾难先来",
            title_customized=True,
        )

        template.refresh_from_db()
        assert template.background.name == background_name
        assert cover.title == "钱没到账灾难先来"
        assert cover.title_customized is True
        assert cover.template_version == template.version

        episode.title = "重生末日前三十天"
        episode.save(update_fields=["title"])
        workspace = {"script_id": script.id, "episodes": [{"episode": 1}]}
        decorate_cover_workspace(workspace)
        assert workspace["episodes"][0]["cover_title"] == "钱没到账灾难先来"


@pytest.mark.django_db
def test_automatic_cover_title_follows_updated_episode_title(tmp_path):
    script = create_script_with_episodes()
    with override_settings(MEDIA_ROOT=tmp_path):
        save_cover_template(script, make_image_result(), "prompt")
        episode = script.episodes.get(episode_number=1)
        episode.title = "重生末日前三十天"
        episode.save(update_fields=["title"])
        workspace = {"script_id": script.id, "episodes": [{"episode": 1}]}

        decorate_cover_workspace(workspace)

        assert workspace["episodes"][0]["cover_title"] == "重生末日前三十天"
