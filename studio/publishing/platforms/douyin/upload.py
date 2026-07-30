import mimetypes
from pathlib import PurePosixPath

from studio.publishing.base import SubmissionResult, UploadResult
from studio.publishing.errors import PublishingOutcomeUnknown, PublishingRetryableError

from .client import DouyinClient


class ProgressReader:
    def __init__(self, source, total, callback):
        self.source = source
        self.total = total
        self.callback = callback
        self.uploaded = 0

    def read(self, size=-1):
        content = self.source.read(size)
        if content:
            self.uploaded += len(content)
            self.callback(self.uploaded, self.total)
        return content

    def __getattr__(self, name):
        return getattr(self.source, name)


class DouyinUploader:
    def upload_media(self, composition, credentials, progress_callback):
        source = composition.video
        source.open("rb")
        client = DouyinClient(credentials)
        try:
            total = source.size
            filename = PurePosixPath(source.name).name
            content_type = mimetypes.guess_type(filename)[0] or "video/mp4"
            reader = ProgressReader(source, total, progress_callback)
            data, raw = client.checked_data(
                "POST",
                client.api_url("/video/upload/"),
                params=client.auth_params(),
                files={"video": (filename, reader, content_type)},
            )
            video = data.get("video") if isinstance(data.get("video"), dict) else {}
            video_id = str(video.get("video_id") or data.get("video_id") or "")
            if not video_id:
                raise PublishingRetryableError("抖音上传成功响应中缺少 video_id。")
            return UploadResult(
                media_id=video_id,
                payload={"video_id": video_id, "provider": raw},
            )
        finally:
            client.close()
            source.close()

    def submit(self, upload_result, metadata, credentials):
        client = DouyinClient(credentials)
        try:
            try:
                data, raw = client.checked_data(
                    "POST",
                    client.api_url("/video/create/"),
                    params=client.auth_params(),
                    json={
                        "video_id": upload_result.media_id,
                        "text": metadata.get("platform_text") or metadata["title"],
                    },
                )
            except PublishingRetryableError as exc:
                raise PublishingOutcomeUnknown(
                    "抖音可能已经收到发布请求，请在任务页核对结果。"
                ) from exc
        finally:
            client.close()
        item_id = str(data.get("item_id") or "")
        if not item_id:
            raise PublishingOutcomeUnknown("抖音接受了发布请求，但没有返回 item_id。")
        return SubmissionResult(
            remote_video_id=item_id,
            remote_url=f"https://www.douyin.com/video/{item_id}",
            status="submitted",
            payload=raw,
        )

    def query_submission(self, remote_video_id, credentials):
        if not remote_video_id:
            return SubmissionResult("", "", status="unknown")
        client = DouyinClient(credentials, timeout=30)
        try:
            data, raw = client.checked_data(
                "POST",
                client.api_url("/video/data/"),
                params=client.auth_params(),
                json={"item_ids": [remote_video_id]},
            )
        finally:
            client.close()
        items = data.get("list") or data.get("items") or []
        matched = next(
            (
                item
                for item in items
                if str(item.get("item_id") or item.get("item_id_str") or "") == str(remote_video_id)
            ),
            None,
        )
        status = "published" if matched else "submitted"
        return SubmissionResult(
            str(remote_video_id),
            f"https://www.douyin.com/video/{remote_video_id}",
            status=status,
            payload=raw,
        )
