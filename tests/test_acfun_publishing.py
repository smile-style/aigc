from types import SimpleNamespace

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings

from studio.publishing.errors import PublishingValidationError
from studio.publishing.platforms.acfun.check import AcFunChecker
from studio.publishing.platforms.acfun.login import AcFunLogin
from studio.publishing.registry import get_platform
from studio.models import PublishingAccount
from studio.publishing.platforms.acfun.upload import AcFunUploader


def test_cookie_parser_and_submission_limits():
    assert AcFunLogin.parse_cookie_header("ac_userId=42; ac_username=test; invalid") == {
        "ac_userId": "42", "ac_username": "test"
    }
    with pytest.raises(PublishingValidationError) as error:
        AcFunChecker().check_submission(
            SimpleNamespace(video=object()),

            {"title": "x" * 51, "tid": 190, "copyright": 1, "tags": []},
        )
    assert error.value.details == {"field": "title"}


def test_acfun_platform_is_registered():
    platform = get_platform(PublishingAccount.PLATFORM_ACFUN)
    assert isinstance(platform.checker, AcFunChecker)
    assert isinstance(platform.uploader, AcFunUploader)



def test_acfun_upload_uses_kscloud_protocol(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def request_json(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url == AcFunUploader.TOKEN_URL:
                return None, {"result": 0, "taskId": "81", "token": "video-token", "uploadConfig": {"partSize": 5}}
            if url in {AcFunUploader.FRAGMENT_URL, AcFunUploader.COMPLETE_URL}:
                return None, {"result": 1}
            if url == AcFunUploader.CREATE_VIDEO_URL:
                return None, {"result": 0, "videoId": "video-7"}
            if url == AcFunUploader.FINISH_URL:
                return None, {"result": 0}
            raise AssertionError(url)
        def close(self): pass

    monkeypatch.setattr("studio.publishing.platforms.acfun.upload.AcFunClient", FakeClient)
    composition = SimpleNamespace(video=ContentFile(b"0123456789ab", name="result.mp4"))
    progress = []
    result = AcFunUploader().upload_media(composition, {"acPasstoken": "session"}, lambda a, b: progress.append((a, b)))
    assert result.media_id == "video-7"
    assert progress == [(5, 12), (10, 12), (12, 12)]
    fragments = [call for call in calls if call[1] == AcFunUploader.FRAGMENT_URL]
    assert [call[2]["params"]["fragment_id"] for call in fragments] == [0, 1, 2]


def test_acfun_submit_uploads_cover_and_creates_douga(tmp_path, monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def request_json(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url == AcFunUploader.QINIU_TOKEN_URL:
                return None, {"result": 0, "info": {"token": "cover-token"}}
            if url in {AcFunUploader.FRAGMENT_URL, AcFunUploader.COMPLETE_URL}:
                return None, {"result": 1}
            if url == AcFunUploader.COVER_URL:
                return None, {"result": 0, "url": "https://imgs.example/cover.jpg"}
            if url == AcFunUploader.SUBMIT_URL:
                return None, {"result": 0, "dougaId": 123456}
            raise AssertionError(url)
        def close(self): pass

    monkeypatch.setattr("studio.publishing.platforms.acfun.upload.AcFunClient", FakeClient)
    with override_settings(MEDIA_ROOT=tmp_path):
        cover = default_storage.save("covers/acfun.jpg", ContentFile(b"cover-content"))
        result = AcFunUploader().submit(
            SimpleNamespace(media_id="video-7"),
            {"title": "漫剧第一集", "description": "简介", "tid": 190, "copyright": 1, "tags": ["漫剧", "动画"], "cover": cover},
            {"acPasstoken": "session"},
        )
    assert result.remote_video_id == "123456"
    assert result.remote_url == "https://www.acfun.cn/v/ac123456"
    submit = next(call for call in calls if call[1] == AcFunUploader.SUBMIT_URL)
    assert submit[2]["data"]["channelId"] == 190
