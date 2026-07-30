from types import SimpleNamespace

import pytest

from studio.generation_handlers import handle_cover_generation
from studio.models import Outline, Project, Script
from studio.services.covers import (
    DOUYIN_GENERATION_SIZE,
    LANDSCAPE_GENERATION_SIZE,
    PORTRAIT_GENERATION_SIZE,
    XIAOHONGSHU_GENERATION_SIZE,
)


pytestmark = pytest.mark.django_db


def test_cover_generation_creates_landscape_and_portrait_masters(monkeypatch):
    project = Project.objects.create(
        workspace_id="dual-cover-generation",
        name="Dual cover",
        genre="都市",
        episode_count=1,
        episode_duration_minutes=2,
    )
    outline = Outline.objects.create(
        project=project,
        outline_id="dual-cover-outline",
        position=1,
        title="逆风翻盘",
        core_premise="重新创业",
        protagonist="林夏",
        hook="危机",
        arc_summary="成长",
    )
    script = Script.objects.create(project=project, outline=outline)
    calls = []

    class FakeProvider:
        def generate_image(self, prompt, size=None):
            calls.append((prompt, size))
            return SimpleNamespace(
                content=b"image",
                extension=".png",
                source_url="",
                model="fake-cover-model",
            )

    saved = {}

    def fake_save(
        current_script, landscape, portrait, prompt_snapshot="", **platform_results
    ):
        saved.update(
            script=current_script,
            landscape=landscape,
            portrait=portrait,
            prompt=prompt_snapshot,
            **platform_results,
        )
        return SimpleNamespace(id=41, version=3)

    monkeypatch.setattr(
        "studio.generation_handlers.image_provider_for",
        lambda: FakeProvider(),
    )
    monkeypatch.setattr(
        "studio.generation_handlers.save_cover_template",
        fake_save,
    )
    task = SimpleNamespace(
        project=project,
        input_snapshot={"script_id": script.id, "prompt": "cover base prompt"},
    )

    result = handle_cover_generation(task)

    assert [size for _, size in calls] == [
        LANDSCAPE_GENERATION_SIZE,
        PORTRAIT_GENERATION_SIZE,
        XIAOHONGSHU_GENERATION_SIZE,
        DOUYIN_GENERATION_SIZE,
    ]
    assert "4:3" in calls[0][0]
    assert "3:4" in calls[1][0]
    assert "7:10" in calls[2][0]
    assert "2:3" in calls[3][0]
    assert saved["script"] == script
    assert saved["prompt"] == "cover base prompt"
    assert saved["xiaohongshu_result"]
    assert saved["douyin_result"]
    assert result["cover_formats"] == [
        "landscape_4_3",
        "portrait_3_4",
        "xiaohongshu_7_10_master",
        "douyin_2_3_master",
    ]
