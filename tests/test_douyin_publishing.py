import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from django.core.files.base import ContentFile

from studio.models import PublishingAccount, PublishingLoginSession
from studio.publishing.base import AccountProfile, LoginStart
from studio.publishing.errors import PublishingValidationError
from studio.publishing.platforms.douyin.check import DouyinChecker
from studio.publishing.platforms.douyin.login import DouyinLogin
from studio.publishing.platforms.douyin.upload import DouyinUploader
from studio.publishing.registry import get_platform_definition
from studio.publishing.service import (
    account_credentials,
    active_account_credentials,
    complete_oauth_login,
    encode_credentials,
    start_login_session,
)


pytestmark = pytest.mark.django_db


def douyin_env(monkeypatch):
    monkeypatch.setenv("DOUYIN_CLIENT_KEY", "client-key")
    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "DOUYIN_REDIRECT_URI",
        "https://studio.example.com/publishing/accounts/oauth/douyin/callback/",
    )


def test_douyin_platform_capabilities_are_registered():
    definition = get_platform_definition(PublishingAccount.PLATFORM_DOUYIN)

    assert definition.login_mode == "oauth"
    assert definition.title_limit == 55
    assert definition.requires_partition is False


def test_oauth_start_builds_authorization_url(monkeypatch):
    douyin_env(monkeypatch)

    result = DouyinLogin().start()
    query = parse_qs(urlparse(result.login_url).query)

    assert result.mode == "oauth"
    assert result.expires_in == 600
    assert query["client_key"] == ["client-key"]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == [
        "https://studio.example.com/publishing/accounts/oauth/douyin/callback/"
    ]
    assert query["state"] == [result.provider_key]
    assert set(query["scope"][0].split(",")) == {"user_info", "video.create", "video.data"}


def test_oauth_state_is_verified_and_account_is_saved(monkeypatch):
    platform = SimpleNamespace(
        login=SimpleNamespace(
            start=lambda: LoginStart(
                provider_key="expected-state",
                login_url="https://open.douyin.com/authorize",
                expires_in=600,
                mode="oauth",
            ),
            exchange_code=lambda code: {
                "access_token": f"token-{code}",
                "refresh_token": "refresh",
                "open_id": "open-1",
            },
        ),
        checker=SimpleNamespace(
            check_account=lambda credentials: AccountProfile(
                "open-1",
                "抖音测试账号",
                {"nickname": "抖音测试账号"},
            )
        ),
    )
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: platform)

    session = start_login_session(PublishingAccount.PLATFORM_DOUYIN)
    with pytest.raises(PublishingValidationError):
        complete_oauth_login(PublishingAccount.PLATFORM_DOUYIN, "wrong-state", "code-1")

    account = complete_oauth_login(
        PublishingAccount.PLATFORM_DOUYIN,
        "expected-state",
        "code-1",
    )
    session.refresh_from_db()

    assert account.platform == PublishingAccount.PLATFORM_DOUYIN
    assert account.display_name == "抖音测试账号"
    assert account_credentials(account)["access_token"] == "token-code-1"
    assert session.status == PublishingLoginSession.STATUS_SUCCEEDED
    assert session.account == account


def test_expired_credentials_are_refreshed_and_persisted(monkeypatch):
    account = PublishingAccount.objects.create(
        platform=PublishingAccount.PLATFORM_DOUYIN,
        remote_account_id="open-1",
        display_name="测试账号",
        credential_ciphertext=encode_credentials(
            {
                "access_token": "old",
                "refresh_token": "refresh",
                "open_id": "open-1",
                "expires_at": "1",
            }
        ),
    )
    refreshed = {
        "access_token": "new",
        "refresh_token": "refresh-2",
        "open_id": "open-1",
        "expires_at": str(int(time.time()) + 3600),
    }
    platform = SimpleNamespace(
        login=SimpleNamespace(ensure_credentials=lambda credentials: refreshed)
    )
    monkeypatch.setattr("studio.publishing.service.get_platform", lambda name: platform)

    result = active_account_credentials(account)
    account.refresh_from_db()

    assert result["access_token"] == "new"
    assert account_credentials(account)["refresh_token"] == "refresh-2"


def test_douyin_submission_validation_builds_bounded_topics():
    checker = DouyinChecker()
    composition = SimpleNamespace(video=SimpleNamespace(name="episode.mp4"))

    result = checker.check_submission(
        composition,
        {
            "title": "第一集",
            "description": "简介",
            "tags": ["AI漫剧", "短剧"],
            "copyright": 2,
            "source": "https://example.com",
        },
    )

    assert result["platform_text"] == "第一集 #AI漫剧 #短剧"
    assert result["copyright"] == 1
    assert result["source"] == ""
    assert result["tid"] is None


def test_douyin_submission_rejects_unsupported_video():
    with pytest.raises(PublishingValidationError) as error:
        DouyinChecker().check_submission(
            SimpleNamespace(video=SimpleNamespace(name="episode.avi")),
            {"title": "第一集", "tags": []},
        )

    assert error.value.code == "unsupported_video_format"


def test_douyin_upload_submit_and_reconcile(monkeypatch):
    calls = []
    progress = []

    class FakeClient:
        def __init__(self, credentials=None, **kwargs):
            self.credentials = credentials

        def api_url(self, path):
            return path

        def auth_params(self):
            return {"access_token": "token", "open_id": "open-1"}

        def checked_data(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url == "/video/upload/":
                reader = kwargs["files"]["video"][1]
                while reader.read(4):
                    pass
                return {"video": {"video_id": "video-1"}}, {"data": {"video": {"video_id": "video-1"}}}
            if url == "/video/create/":
                return {"item_id": "item-1"}, {"data": {"item_id": "item-1"}}
            return {"list": [{"item_id": "item-1"}]}, {"data": {"list": [{"item_id": "item-1"}]}}

        def close(self):
            pass

    monkeypatch.setattr("studio.publishing.platforms.douyin.upload.DouyinClient", FakeClient)
    composition = SimpleNamespace(video=ContentFile(b"0123456789", name="episode.mp4"))
    uploader = DouyinUploader()

    upload = uploader.upload_media(
        composition,
        {"access_token": "token", "open_id": "open-1"},
        lambda uploaded, total: progress.append((uploaded, total)),
    )
    submitted = uploader.submit(
        upload,
        {"title": "第一集", "platform_text": "第一集 #AI漫剧"},
        {"access_token": "token", "open_id": "open-1"},
    )
    reconciled = uploader.query_submission(
        submitted.remote_video_id,
        {"access_token": "token", "open_id": "open-1"},
    )

    assert upload.media_id == "video-1"
    assert progress[-1] == (10, 10)
    assert submitted.remote_video_id == "item-1"
    assert submitted.remote_url == "https://www.douyin.com/video/item-1"
    assert reconciled.status == "published"
    assert calls[1][2]["json"] == {
        "video_id": "video-1",
        "text": "第一集 #AI漫剧",
    }
