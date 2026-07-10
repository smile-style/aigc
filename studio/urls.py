from django.urls import path

from . import views


app_name = "studio"

urlpatterns = [
    path("", views.outline_page, name="outline"),
    path("outlines/generate/", views.generate_outlines_view, name="generate_outlines"),
    path("outlines/usable/", views.mark_outline_usable_view, name="mark_outline_usable"),
    path("outlines/select/", views.select_outline_view, name="select_outline"),
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
        "script/<str:workspace_id>/episode/<int:episode_number>/generate/",
        views.generate_episode_script_view,
        name="generate_episode_script",
    ),
    path("tasks/<int:task_id>/", views.task_status_view, name="task_status"),
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
]
