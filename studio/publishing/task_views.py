from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from studio.models import PublishingAccount, PublishingTask


ACTIVE_STATUSES = [
    PublishingTask.STATUS_QUEUED,
    PublishingTask.STATUS_RUNNING,
    PublishingTask.STATUS_RETRY_WAIT,
    PublishingTask.STATUS_SUBMITTED,
]
ATTENTION_STATUSES = [
    PublishingTask.STATUS_FAILED,
    PublishingTask.STATUS_REJECTED,
    PublishingTask.STATUS_OUTCOME_UNKNOWN,
]


@require_GET
def task_list_view(request):
    view_name = request.GET.get("view", "active")
    if view_name not in {"active", "history"}:
        view_name = "active"

    base = PublishingTask.objects.select_related(
        "account", "composition__episode__script__outline"
    )
    status_filter = ""
    platform_filter = ""
    query = ""

    if view_name == "history":
        tasks = base.filter(
            Q(status__in=[PublishingTask.STATUS_PUBLISHED, PublishingTask.STATUS_CANCELLED])
            | Q(status__in=ATTENTION_STATUSES, acknowledged_at__isnull=False)
        ).order_by("-finished_at", "-updated_at", "-id")
        status_filter = request.GET.get("status", "")
        valid_statuses = {
            PublishingTask.STATUS_PUBLISHED,
            PublishingTask.STATUS_CANCELLED,
            *ATTENTION_STATUSES,
        }
        if status_filter in valid_statuses:
            tasks = tasks.filter(status=status_filter)

        platform_filter = request.GET.get("platform", "")
        valid_platforms = {value for value, _label in PublishingAccount.PLATFORM_CHOICES}
        if platform_filter in valid_platforms:
            tasks = tasks.filter(platform=platform_filter)
        else:
            platform_filter = ""

        query = request.GET.get("q", "").strip()[:80]
        if query:
            tasks = tasks.filter(
                Q(composition__episode__title__icontains=query)
                | Q(composition__episode__script__outline__title__icontains=query)
                | Q(remote_video_id__icontains=query)
            )
    else:
        tasks = base.filter(
            Q(status__in=ACTIVE_STATUSES)
            | Q(status__in=ATTENTION_STATUSES, acknowledged_at__isnull=True)
        ).order_by("-created_at", "-id")

    paginator = Paginator(tasks, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    for task in page_obj.object_list:
        task.needs_acknowledgement = (
            task.status in ATTENTION_STATUSES and task.acknowledged_at is None
        )
        task.is_active_status = task.status in ACTIVE_STATUSES

    active_count = PublishingTask.objects.filter(status__in=ACTIVE_STATUSES).count()
    attention_count = PublishingTask.objects.filter(
        status__in=ATTENTION_STATUSES,
        acknowledged_at__isnull=True,
    ).count()
    filter_values = {"view": view_name}
    if status_filter:
        filter_values["status"] = status_filter
    if platform_filter:
        filter_values["platform"] = platform_filter
    if query:
        filter_values["q"] = query

    return render(
        request,
        "studio/publishing/task_dashboard.html",
        {
            "publishing_tasks": page_obj.object_list,
            "active_tasks": [task for task in page_obj.object_list if task.is_active_status],
            "attention_tasks": [
                task for task in page_obj.object_list if task.needs_acknowledgement
            ],
            "page_obj": page_obj,
            "view_name": view_name,
            "status_filter": status_filter,
            "platform_filter": platform_filter,
            "query": query,
            "platform_choices": PublishingAccount.PLATFORM_CHOICES,
            "active_count": active_count,
            "attention_count": attention_count,
            "pagination_query": urlencode(filter_values),
            "legacy_heading": "\u5e73\u53f0\u6295\u7a3f\u8fdb\u5ea6",
            "active_nav": "publishing",
        },
    )


@require_POST
def acknowledge_task_view(request, task_id):
    task = get_object_or_404(PublishingTask, pk=task_id)
    if task.status not in ATTENTION_STATUSES:
        return redirect(reverse("studio:publishing_tasks") + "?view=active")
    if task.acknowledged_at is None:
        task.acknowledged_at = timezone.now()
        user = getattr(request, "user", None)
        task.acknowledged_by = (
            user.get_username() if user is not None and user.is_authenticated else ""
        )
        task.acknowledgement_note = request.POST.get("note", "").strip()[:500]
        task.save(
            update_fields=[
                "acknowledged_at",
                "acknowledged_by",
                "acknowledgement_note",
                "updated_at",
            ]
        )
    return redirect(reverse("studio:publishing_tasks") + "?view=history")
