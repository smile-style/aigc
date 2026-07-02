from django.shortcuts import render

from .constants import EPISODE_COUNT, EPISODE_DURATION_MINUTES, GENRES


def outline_page(request):
    return render(
        request,
        "studio/outline.html",
        {
            "genres": GENRES,
            "episode_count": EPISODE_COUNT,
            "episode_duration_minutes": EPISODE_DURATION_MINUTES,
        },
    )
