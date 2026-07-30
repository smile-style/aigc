from io import BytesIO

from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from studio.models import PublishingAccount, PublishingLoginSession, PublishingTask, VideoComposition

from .errors import PublishingError
from .service import (
    cancel_task,
    check_publishing_account,
    complete_oauth_login,
    connect_cookie_account,
    create_publishing_task,
    poll_login_session,
    reconcile_task,
    republish_task,
    retry_task,
    serialize_task,
    start_login_session,
)


@require_GET
def task_list_view(request):
    status_filter = request.GET.get("status", "")
    tasks = PublishingTask.objects.select_related(
        "account", "composition__episode__script__outline"
    ).prefetch_related("attempts")
    groups = {
        "active": [PublishingTask.STATUS_QUEUED, PublishingTask.STATUS_RUNNING, PublishingTask.STATUS_RETRY_WAIT],
        "attention": [PublishingTask.STATUS_FAILED, PublishingTask.STATUS_REJECTED, PublishingTask.STATUS_OUTCOME_UNKNOWN],
        "published": [PublishingTask.STATUS_SUBMITTED, PublishingTask.STATUS_PUBLISHED],
    }
    if status_filter in groups:
        tasks = tasks.filter(status__in=groups[status_filter])
    return render(
        request,
        "studio/publishing/tasks.html",
        {"publishing_tasks": tasks[:200], "status_filter": status_filter, "active_nav": "publishing"},
    )


@require_POST
def create_task_view(request):
    composition = get_object_or_404(
        VideoComposition.objects.select_related(
            "episode__script__outline", "episode__cover"
        ),
        pk=request.POST.get("composition_id"),
    )
    raw_account_ids = request.POST.getlist("account_ids")
    if not raw_account_ids and request.POST.get("account_id"):
        raw_account_ids = [request.POST["account_id"]]
    try:
        account_ids = list(dict.fromkeys(int(value) for value in raw_account_ids))
    except (TypeError, ValueError):
        account_ids = []
    accounts_by_id = {
        account.id: account
        for account in PublishingAccount.objects.filter(pk__in=account_ids)
    }
    accounts = [accounts_by_id[value] for value in account_ids if value in accounts_by_id]
    if not accounts or len(accounts) != len(account_ids):
        message = "\u8bf7\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u53d1\u5e03\u8d26\u53f7\u3002"
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": False, "error": message}, status=400)
        return HttpResponseBadRequest(message)
    platforms = [account.platform for account in accounts]
    if len(platforms) != len(set(platforms)):
        message = "\u6bcf\u4e2a\u5e73\u53f0\u4e00\u6b21\u53ea\u80fd\u9009\u62e9\u4e00\u4e2a\u8d26\u53f7\u3002"
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": False, "error": message}, status=400)
        return HttpResponseBadRequest(message)

    cover = getattr(composition.episode, "cover", None)
    try:
        results = []
        with transaction.atomic():
            for account in accounts:
                task, created = create_publishing_task(
                    composition,
                    account,
                    {
                        "title": request.POST.get("title"),
                        "description": request.POST.get("description"),
                        "tid": request.POST.get(f"tid_{account.platform}") or request.POST.get("tid"),
                        "tags": request.POST.get("tags"),
                        "copyright": request.POST.get("copyright"),
                        "source": request.POST.get("source"),
                        "cover": cover.image.name if cover and cover.image else "",
                    },
                    force_republish=request.POST.get("force_republish") == "1",
                )
                results.append((task, created))
    except PublishingError as exc:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": False, "error": str(exc), "details": exc.details}, status=400)
        return HttpResponseBadRequest(str(exc))
    if request.headers.get("Accept") == "application/json":
        serialized = [serialize_task(task) for task, _ in results]
        return JsonResponse(
            {
                "ok": True,
                "created": all(created for _, created in results),
                "task": serialized[0],
                "tasks": serialized,
            }
        )
    return redirect("studio:publishing_tasks")


@require_GET
def task_status_view(request):
    raw_ids = request.GET.get("ids", "")
    try:
        ids = [int(value) for value in raw_ids.split(",") if value.strip()][:50]
    except ValueError:
        return JsonResponse({"error": "任务 ID 无效。"}, status=400)
    tasks = PublishingTask.objects.filter(pk__in=ids).select_related("account")
    return JsonResponse({"tasks": [serialize_task(task) for task in tasks]})


@require_POST
def retry_task_view(request, task_id):
    task = get_object_or_404(PublishingTask, pk=task_id)
    try:
        retry_task(task)
    except PublishingError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("studio:publishing_tasks")


@require_POST
def republish_task_view(request, task_id):
    task = get_object_or_404(PublishingTask, pk=task_id)
    try:
        republish_task(task)
    except PublishingError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("studio:publishing_tasks")


@require_POST
def cancel_task_view(request, task_id):
    task = get_object_or_404(PublishingTask, pk=task_id)
    try:
        cancel_task(task)
    except PublishingError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("studio:publishing_tasks")


@require_POST
def reconcile_task_view(request, task_id):
    task = get_object_or_404(PublishingTask, pk=task_id)
    try:
        reconcile_task(task)
    except PublishingError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("studio:publishing_tasks")


@require_POST
def cookie_login_view(request):
    try:
        connect_cookie_account(
            request.POST.get("cookie"),
            request.POST.get("platform") or PublishingAccount.PLATFORM_BILIBILI,
        )
    except PublishingError as exc:
        return redirect(f"/system/?publishing_error={str(exc)}")
    return redirect("/system/?publishing_success=1")


@require_POST
def qr_login_start_view(request):
    try:
        platform_name = request.POST.get("platform") or PublishingAccount.PLATFORM_BILIBILI
        session = start_login_session(platform_name)
    except (PublishingError, ValueError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)
    return JsonResponse(
        {
            "ok": True,
            "session_id": str(session.session_id),
            "qr_url": f"/publishing/login/{session.session_id}/qr/",
            "status_url": f"/publishing/login/{session.session_id}/status/",
            "expires_at": session.expires_at.isoformat(),
        }
    )


@require_GET
def oauth_login_start_view(request, platform):
    try:
        session = start_login_session(platform)
    except (PublishingError, ValueError) as exc:
        return redirect(f"/system/?publishing_error={str(exc)}")
    return redirect(session.login_url)


@require_GET
def oauth_login_callback_view(request, platform):
    provider_error = request.GET.get("error") or request.GET.get("error_description")
    if provider_error:
        return redirect(f"/system/?publishing_error={provider_error}")
    try:
        complete_oauth_login(platform, request.GET.get("state"), request.GET.get("code"))
    except (PublishingError, ValueError) as exc:
        return redirect(f"/system/?publishing_error={str(exc)}")
    return redirect(reverse("studio:system_settings") + "?publishing_success=1")


@require_GET
def qr_login_image_view(request, session_id):
    session = get_object_or_404(PublishingLoginSession, session_id=session_id)
    try:
        import qrcode
    except ImportError as exc:
        raise Http404("二维码组件未安装") from exc
    image = qrcode.make(session.login_url)
    output = BytesIO()
    image.save(output, format="PNG")
    return HttpResponse(output.getvalue(), content_type="image/png")


@require_GET
def qr_login_status_view(request, session_id):
    try:
        session = poll_login_session(session_id)
    except PublishingLoginSession.DoesNotExist as exc:
        raise Http404("登录会话不存在") from exc
    return JsonResponse(
        {
            "status": session.status,
            "label": session.get_status_display(),
            "message": session.error_message,
            "account_id": session.account_id,
        }
    )


@require_POST
def account_check_view(request, account_id):
    account = get_object_or_404(PublishingAccount, pk=account_id)
    try:
        check_publishing_account(account)
    except PublishingError as exc:
        return redirect(f"/system/?publishing_error={str(exc)}")
    return redirect("/system/?publishing_success=checked")


@require_POST
def account_logout_view(request, account_id):
    account = get_object_or_404(PublishingAccount, pk=account_id)
    account.status = account.STATUS_EXPIRED
    account.credential_ciphertext = ""
    account.last_error = "账号已注销。"
    account.save(update_fields=["status", "credential_ciphertext", "last_error", "updated_at"])
    return redirect("/system/?publishing_success=logged_out")
