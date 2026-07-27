from types import SimpleNamespace

import httpx
import pytest
from django.core.files.base import ContentFile

from django.core.files.storage import default_storage
from django.test import override_settings
from studio.publishing.errors import PublishingOutcomeUnknown, PublishingRetryableError, PublishingValidationError
from studio.publishing.platforms.bilibili.client import BilibiliClient
from studio.publishing.platforms.bilibili.check import BilibiliChecker
from studio.publishing.platforms.bilibili.login import BilibiliLogin
from studio.publishing.platforms.bilibili.upload import BilibiliUploader

def test_http_406_rate_limit_preserves_provider_error_and_is_retryable():
    def handler(request):
        return httpx.Response(
            406,
            request=request,
            json={
                "OK": 0,
                "code": 601,
                "message": "You are uploading videos too quickly. Please try again later.",
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = BilibiliClient(client=http_client)

    with pytest.raises(PublishingRetryableError) as error:
        client.request_json("GET", BilibiliUploader.PREUPLOAD_URL)

    assert error.value.code == "platform_rate_limited"
    assert error.value.details == {
        "http_status": 406,
        "platform_code": 601,
        "platform_message": "You are uploading videos too quickly. Please try again later.",
        "retry_after_seconds": 600,
    }


def test_http_406_without_rate_limit_code_remains_non_retryable():
    def handler(request):
        return httpx.Response(406, request=request, json={"code": 1001, "message": "Invalid request"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = BilibiliClient(client=http_client)

    with pytest.raises(PublishingValidationError) as error:
        client.request_json("GET", BilibiliUploader.PREUPLOAD_URL)

    assert error.value.details["http_status"] == 406
    assert error.value.details["platform_message"] == "Invalid request"



def test_cookie_header_parser_ignores_invalid_segments():
    cookies = BilibiliLogin.parse_cookie_header(
        "SESSDATA=session-value; bili_jct=csrf-value; invalid; DedeUserID=123"
    )

    assert cookies == {
        "SESSDATA": "session-value",
        "bili_jct": "csrf-value",
        "DedeUserID": "123",
    }


def test_submission_validation_reports_field_errors():
    checker = BilibiliChecker()
    composition = SimpleNamespace(video=object())

    with pytest.raises(PublishingValidationError) as error:
        checker.check_submission(
            composition,
            {"title": "Title", "tid": 21, "copyright": 2, "source": ""},
        )

    assert error.value.details == {"field": "source"}


def test_upos_upload_reports_progress_and_returns_media_id(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def request_json(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url == BilibiliUploader.PREUPLOAD_URL:
                return None, {
                    "OK": 1,
                    "endpoint": "https://upos.example.com",
                    "upos_uri": "upos://bucket/final-video.mp4",
                    "auth": "secret-upload-auth",
                    "biz_id": 12,
                    "chunk_size": 5,
                }
            if method == "POST" and "uploads" in kwargs.get("params", {}):
                return None, {"upload_id": "upload-1"}
            if method == "PUT":
                return SimpleNamespace(headers={"ETag": "part-etag"}), {"OK": 1}
            return None, {"OK": 1}

        def close(self):
            pass

    monkeypatch.setattr("studio.publishing.platforms.bilibili.upload.BilibiliClient", FakeClient)
    content = ContentFile(b"0123456789ab", name="result.mp4")
    composition = SimpleNamespace(video=content)
    progress = []

    result = BilibiliUploader().upload_media(
        composition,
        {"SESSDATA": "session"},
        lambda uploaded, total: progress.append((uploaded, total)),
    )

    assert result.media_id == "final-video"
    assert progress == [(5, 12), (10, 12), (12, 12)]
    assert len([call for call in calls if call[0] == "PUT"]) == 3
    assert "secret-upload-auth" not in str(result.payload)


def test_submit_timeout_is_not_treated_as_normal_retry(monkeypatch):
    class TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def checked_data(self, *args, **kwargs):
            try:
                raise httpx.ReadTimeout("timed out")
            except httpx.ReadTimeout as exc:
                from studio.publishing.errors import PublishingRetryableError

                raise PublishingRetryableError("timeout") from exc

        def close(self):
            pass

    monkeypatch.setattr("studio.publishing.platforms.bilibili.upload.BilibiliClient", TimeoutClient)

    with pytest.raises(PublishingOutcomeUnknown):
        BilibiliUploader().submit(
            SimpleNamespace(media_id="media-id"),
            {"title": "Title", "tid": 21, "copyright": 1, "tags": []},
            {"bili_jct": "csrf"},
        )


def test_submit_uploads_local_cover_before_creating_submission(tmp_path, monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def checked_data(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url == BilibiliUploader.COVER_UPLOAD_URL:
                return {"url": "https://i.example.com/cover.jpg"}, {"code": 0}
            return {"bvid": "BV1COVER"}, {"code": 0, "data": {"bvid": "BV1COVER"}}

        def close(self):
            pass

    monkeypatch.setattr("studio.publishing.platforms.bilibili.upload.BilibiliClient", FakeClient)
    with override_settings(MEDIA_ROOT=tmp_path):
        cover_name = default_storage.save("covers/episode-1.jpg", ContentFile(b"jpeg-cover"))
        result = BilibiliUploader().submit(
            SimpleNamespace(media_id="media-id"),
            {
                "title": "Title",
                "tid": 21,
                "copyright": 1,
                "tags": [],
                "cover": cover_name,
            },
            {"bili_jct": "csrf"},
        )

    assert result.remote_video_id == "BV1COVER"
    cover_call, submit_call = calls
    assert cover_call[1] == BilibiliUploader.COVER_UPLOAD_URL
    assert cover_call[2]["data"]["cover"].startswith("data:image/jpeg;base64,")
    assert submit_call[2]["json"]["cover"] == "https://i.example.com/cover.jpg"
