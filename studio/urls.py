from django.urls import path

from . import views


app_name = "studio"

urlpatterns = [
    path("", views.outline_page, name="outline"),
    path("outlines/generate/", views.generate_outlines_view, name="generate_outlines"),
    path("outlines/select/", views.select_outline_view, name="select_outline"),
    path("script/<str:workspace_id>/", views.script_page, name="script"),
    path(
        "script/<str:workspace_id>/generate/",
        views.generate_script_view,
        name="generate_script",
    ),
    path("storyboard/<str:workspace_id>/", views.storyboard_page, name="storyboard"),
    path(
        "storyboard/<str:workspace_id>/generate/",
        views.generate_storyboard_view,
        name="generate_storyboard",
    ),
]
