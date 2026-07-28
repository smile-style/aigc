import json
import math
import secrets
import time
from pathlib import PurePosixPath

from django.core.files.storage import default_storage

from studio.publishing.base import SubmissionResult, UploadResult
from studio.publishing.errors import (
    PublishingOutcomeUnknown,
    PublishingRetryableError,
    PublishingValidationError,
)

from .client import AcFunClient


class AcFunUploader:
    TOKEN_URL = "https://member.acfun.cn/video/api/getKSCloudToken"
    FRAGMENT_URL = "https://upload.kuaishouzt.com/api/upload/fragment"
    COMPLETE_URL = "https://upload.kuaishouzt.com/api/upload/complete"
    FINISH_URL = "https://member.acfun.cn/video/api/uploadFinish"
    CREATE_VIDEO_URL = "https://member.acfun.cn/video/api/createVideo"
    QINIU_TOKEN_URL = "https://member.acfun.cn/common/api/getQiniuToken"
    COVER_URL = "https://member.acfun.cn/common/api/getUrlAfterUpload"
    SUBMIT_URL = "https://member.acfun.cn/video/api/createDouga"

    def upload_media(self, composition, credentials, progress_callback):
        source = composition.video
        source.open("rb")
        client = AcFunClient(credentials)
        try:
            total = source.size
            filename = PurePosixPath(source.name).name
            _, token_payload = client.request_json(
                "POST",
                self.TOKEN_URL,
                data={"fileName": filename, "size": total, "template": "1"},
            )
            self._require_result(token_payload, expected=0, action="创建上传会话")
            task_id = token_payload.get("taskId")
            token = token_payload.get("token")
            config = token_payload.get("uploadConfig") or {}
            part_size = int(config.get("partSize") or 0)
            if not task_id or not token or part_size <= 0:
                raise PublishingRetryableError("AcFun 未返回完整的上传会话。")
            fragment_count = max(1, math.ceil(total / part_size))
            uploaded = 0
            for fragment_id in range(fragment_count):
                content = source.read(part_size)
                payload = self._retry_fragment(client, token, fragment_id, content)
                self._require_result(payload, expected=1, action=f"上传第 {fragment_id + 1} 个分片")
                uploaded += len(content)
                progress_callback(uploaded, total)
            _, complete = client.request_json(
                "POST",
                self.COMPLETE_URL,
                params={"fragment_count": fragment_count, "upload_token": token},
                headers={"Content-Length": "0"},
            )
            self._require_result(complete, expected=1, action="合并视频分片")
            _, created = client.request_json(
                "POST",
                self.CREATE_VIDEO_URL,
                data={"videoKey": task_id, "fileName": filename, "vodType": "ksCloud"},
            )
            self._require_result(created, expected=0, action="创建视频")
            video_id = created.get("videoId")
            if not video_id:
                raise PublishingRetryableError("AcFun 创建视频后未返回 videoId。")
            _, finished = client.request_json("POST", self.FINISH_URL, data={"taskId": task_id})
            self._require_result(finished, expected=0, action="确认视频上传")
            return UploadResult(
                media_id=str(video_id),
                payload={"task_id": str(task_id), "video_id": str(video_id), "fragments": fragment_count},
            )
        finally:
            client.close()
            source.close()

    def submit(self, upload_result, metadata, credentials):
        client = AcFunClient(credentials)
        try:
            cover_url = self._upload_cover(metadata.get("cover"), client)
            copyright_value = int(metadata.get("copyright", 1))
            data = {
                "title": metadata["title"],
                "description": metadata.get("description", ""),
                "tagNames": json.dumps(metadata.get("tags") or [], ensure_ascii=False),
                "creationType": 3 if copyright_value == 1 else 1,
                "channelId": metadata["tid"],
                "coverUrl": cover_url,
                "videoInfos": json.dumps(
                    [{"videoId": upload_result.media_id, "title": metadata["title"]}],
                    ensure_ascii=False,
                ),
                "isJoinUpCollege": "0",
                "isSyncKs": "0",
                "originalDeclare": "1" if copyright_value == 1 else "0",
            }
            if copyright_value == 2:
                data["originalLinkUrl"] = metadata.get("source", "")
            try:
                _, payload = client.request_json("POST", self.SUBMIT_URL, data=data)
            except PublishingRetryableError as exc:
                raise PublishingOutcomeUnknown("AcFun 投稿结果未知，请到创作中心核对。") from exc
            self._require_result(payload, expected=0, action="提交稿件")
        finally:
            client.close()
        douga_id = payload.get("dougaId")
        if not douga_id:
            raise PublishingOutcomeUnknown("AcFun 接受了投稿请求，但没有返回 AC 号。")
        remote_id = str(douga_id)
        return SubmissionResult(
            remote_video_id=remote_id,
            remote_url=f"https://www.acfun.cn/v/ac{remote_id}",
            payload=payload,
        )

    def _upload_cover(self, cover, client):
        cover = str(cover or "").strip()
        if not cover:
            raise PublishingValidationError("发布到 AcFun 需要单集封面。", code="missing_cover")
        try:
            with default_storage.open(cover, "rb") as source:
                content = source.read()
        except (OSError, FileNotFoundError) as exc:
            raise PublishingValidationError("单集封面文件不存在，请重新生成封面。", code="missing_cover") from exc
        suffix = PurePosixPath(cover).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        token_name = f"{secrets.token_hex(12)}{'.jpg' if suffix == '.jpeg' else suffix}"
        _, token_payload = client.request_json("POST", self.QINIU_TOKEN_URL, data={"fileName": token_name})
        self._require_result(token_payload, expected=0, action="获取封面上传令牌")
        token = (token_payload.get("info") or {}).get("token")
        if not token:
            raise PublishingRetryableError("AcFun 未返回封面上传令牌。")
        _, fragment = client.request_json(
            "POST",
            self.FRAGMENT_URL,
            params={"fragment_id": 0, "upload_token": token},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        self._require_result(fragment, expected=1, action="上传封面")
        _, complete = client.request_json(
            "POST",
            self.COMPLETE_URL,
            params={"fragment_count": 1, "upload_token": token},
            headers={"Content-Length": "0"},
        )
        self._require_result(complete, expected=1, action="合并封面")
        _, url_payload = client.request_json(
            "POST", self.COVER_URL, data={"bizFlag": "web-douga-cover", "token": token}
        )
        self._require_result(url_payload, expected=0, action="获取封面地址")
        cover_url = str(url_payload.get("url") or "").strip()
        if not cover_url:
            raise PublishingRetryableError("AcFun 未返回封面地址。")
        return cover_url

    def _retry_fragment(self, client, token, fragment_id, content):
        last_error = None
        for attempt in range(3):
            if attempt:
                time.sleep(2 ** (attempt - 1))
            try:
                _, payload = client.request_json(
                    "POST",
                    self.FRAGMENT_URL,
                    params={"fragment_id": fragment_id, "upload_token": token},
                    content=content,
                    headers={"Content-Type": "application/octet-stream"},
                )
                if payload.get("result") == 1:
                    return payload
                last_error = PublishingRetryableError(AcFunClient.message(payload))
            except PublishingRetryableError as exc:
                last_error = exc
        raise last_error or PublishingRetryableError("AcFun 分片上传失败。")

    @staticmethod
    def _require_result(payload, *, expected, action):
        if payload.get("result") == expected:
            return
        message = AcFunClient.message(payload)
        raise PublishingValidationError(
            f"AcFun {action}失败：{message}",
            code=f"acfun_{payload.get('result', 'unknown')}",
            details={"platform_code": payload.get("result"), "platform_message": message},
        )

    def query_submission(self, remote_video_id, credentials):
        if not remote_video_id:
            return SubmissionResult("", "", status="unknown")
        return SubmissionResult(
            str(remote_video_id),
            f"https://www.acfun.cn/v/ac{remote_video_id}",
            status="submitted",
        )
