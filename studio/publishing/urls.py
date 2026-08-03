from django.urls import path

from . import task_views, views


urlpatterns = [
    path("tasks/", task_views.task_list_view, name="publishing_tasks"),
    path("tasks/<int:task_id>/acknowledge/", task_views.acknowledge_task_view, name="publishing_task_acknowledge"),
    path("tasks/create/", views.create_task_view, name="publishing_task_create"),
    path("tasks/status/", views.task_status_view, name="publishing_task_status"),
    path("tasks/<int:task_id>/retry/", views.retry_task_view, name="publishing_task_retry"),
    path("tasks/<int:task_id>/republish/", views.republish_task_view, name="publishing_task_republish"),
    path("tasks/<int:task_id>/cancel/", views.cancel_task_view, name="publishing_task_cancel"),
    path("tasks/<int:task_id>/reconcile/", views.reconcile_task_view, name="publishing_task_reconcile"),
    path("accounts/cookie-login/", views.cookie_login_view, name="publishing_cookie_login"),
    path("accounts/qr-login/", views.qr_login_start_view, name="publishing_qr_login"),
    path("accounts/oauth/<str:platform>/", views.oauth_login_start_view, name="publishing_oauth_login"),
    path(
        "accounts/oauth/<str:platform>/callback/",
        views.oauth_login_callback_view,
        name="publishing_oauth_callback",
    ),
    path("accounts/<int:account_id>/check/", views.account_check_view, name="publishing_account_check"),
    path("accounts/<int:account_id>/logout/", views.account_logout_view, name="publishing_account_logout"),
    path("login/<uuid:session_id>/qr/", views.qr_login_image_view, name="publishing_login_qr"),
    path("login/<uuid:session_id>/status/", views.qr_login_status_view, name="publishing_login_status"),
]
