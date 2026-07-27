from django.urls import include, path

from . import cover_views, video_views, views


app_name = "studio"

urlpatterns = [
    path("publishing/", include("studio.publishing.urls")),
    path("", views.outline_page, name="outline"),
    path("outlines/generate/", views.generate_outlines_view, name="generate_outlines"),
    path("outlines/usable/", views.mark_outline_usable_view, name="mark_outline_usable"),
    path("outlines/select/", views.select_outline_view, name="select_outline"),
    path(
        "projects/<int:project_id>/",
        views.project_workbench_page,
        name="project_workbench",
    ),
    path("films/", video_views.finished_films_page, name="finished_films"),
    path(
        "films/<int:composition_id>/download/",
        video_views.download_finished_film_view,
        name="download_finished_film",
    ),
    path("script/", views.script_library_page, name="script_index"),
    path("script/<str:workspace_id>/", views.script_page, name="script"),
    path(
        "script/<str:workspace_id>/generate/",
        views.generate_script_view,
        name="generate_script",
    ),
    path(
        "script/<str:workspace_id>/episode/<int:episode_number>/",
        views.episode_script_page,
        name="episode_script",
    ),
    path(
        "script/<str:workspace_id>/item/<int:script_id>/episode/<int:episode_number>/",
        views.script_episode_context_page,
        name="script_episode_context",
    ),
    path(
        "script/<str:workspace_id>/episode/<int:episode_number>/generate/",
        views.generate_episode_script_view,
        name="generate_episode_script",
    ),
    path(
        "script/<str:workspace_id>/characters/generate/",
        views.generate_characters_view,
        name="generate_characters",
    ),
    path(
        "script/<str:workspace_id>/characters/<int:character_id>/image/",
        views.generate_character_image_view,
        name="generate_character_image",
    ),
    path(
        "script/<str:workspace_id>/characters/<int:character_id>/image/download/",
        views.download_character_image_view,
        name="download_character_image",
    ),
    path(
        "script/<str:workspace_id>/covers/generate/",
        cover_views.generate_cover_view,
        name="generate_cover",
    ),
    path(
        "script/<str:workspace_id>/covers/episode/<int:episode_number>/",
        cover_views.update_episode_cover_view,
        name="update_episode_cover",
    ),
    path(
        "script/<str:workspace_id>/covers/episode/<int:episode_number>/download/",
        cover_views.download_episode_cover_view,
        name="download_episode_cover",
    ),
    path(
        "script/<str:workspace_id>/covers/download/",
        cover_views.download_all_episode_covers_view,
        name="download_all_episode_covers",
    ),
    path("tasks/<int:task_id>/", views.task_status_view, name="task_status"),
    path(
        "tasks/<int:task_id>/retry/",
        views.retry_generation_task_view,
        name="retry_generation_task",
    ),
    path("storyboard/<str:workspace_id>/", views.storyboard_page, name="storyboard"),
    path(
        "storyboard/<str:workspace_id>/generate/",
        views.generate_storyboard_view,
        name="generate_storyboard",
    ),
    path(
        "storyboard/<str:workspace_id>/episode/<int:episode_number>/",
        views.storyboard_episode_page,
        name="storyboard_episode",
    ),
    path(
        "storyboard/<str:workspace_id>/episode/<int:episode_number>/generate/",
        views.generate_storyboard_view,
        name="generate_storyboard_episode",
    ),
    path(
        "workbench/<str:workspace_id>/open/",
        views.workbench_context_view,
        name="workbench_context",
    ),
    path(
        "storyboard/<str:workspace_id>/script/<int:script_id>/episode/<int:episode_number>/",
        views.storyboard_script_episode_page,
        name="storyboard_script_episode",
    ),
    path(
        "storyboard/<str:workspace_id>/script/<int:script_id>/episode/<int:episode_number>/generate/",
        views.generate_storyboard_script_view,
        name="generate_storyboard_script",
    ),
    path("system/", video_views.system_settings_page, name="system_settings"),
    path("video/<str:workspace_id>/", video_views.video_page, name="video"),
    path(
        "video/<str:workspace_id>/script/<int:script_id>/episode/<int:episode_number>/",
        video_views.video_script_episode_page,
        name="video_script_episode",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/",
        video_views.video_episode_page,
        name="video_episode",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/status/",
        video_views.video_status_view,
        name="video_status",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/batch/",
        video_views.batch_video_view,
        name="batch_videos",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/sync-character-assets/",
        video_views.sync_character_assets_view,
        name="sync_character_assets",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/shot/<uuid:shot_id>/generate/",
        video_views.generate_shot_video_view,
        name="generate_shot_video",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/shot/<uuid:shot_id>/prompt/",
        video_views.save_shot_video_prompt_view,
        name="save_shot_video_prompt",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/shot/<uuid:shot_id>/characters/",
        video_views.bind_shot_characters_view,
        name="bind_shot_characters",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/reorder/",
        video_views.reorder_video_shots_view,
        name="reorder_video_shots",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/subtitles/generate/",
        video_views.generate_subtitles_view,
        name="generate_subtitles",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/shot/<uuid:shot_id>/subtitles/generate/",
        video_views.generate_shot_subtitles_view,
        name="generate_shot_subtitles",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/shot/<uuid:shot_id>/subtitles/save/",
        video_views.save_shot_subtitles_view,
        name="save_shot_subtitles",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/subtitles/save/",
        video_views.save_subtitles_view,
        name="save_subtitles",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/subtitles/style/",
        video_views.save_subtitle_style_view,
        name="save_subtitle_style",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/subtitles/download/",
        video_views.download_subtitles_view,
        name="download_subtitles",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/export/",
        video_views.export_video_view,
        name="export_video",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/shot-video/<int:video_id>/download/",
        video_views.download_shot_video_view,
        name="download_shot_video",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/download/",
        video_views.download_composition_view,
        name="download_composition",
    ),
]

urlpatterns += [
    path(
        "script/<str:workspace_id>/characters/<int:character_id>/delete/",
        views.delete_character_view,
        name="delete_character",
    ),
    path(
        "video/<str:workspace_id>/episode/<int:episode_number>/characters/generate/",
        video_views.generate_storyboard_characters_view,
        name="generate_storyboard_characters",
    ),
]
