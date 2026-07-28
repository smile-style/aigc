import base64
import math
import mimetypes
import json
import time
from pathlib import PurePosixPath

from django.core.files.storage import default_storage

from studio.publishing.base import SubmissionResult, UploadResult
from studio.publishing.errors import PublishingOutcomeUnknown, PublishingRetryableError, PublishingValidationError

from .client import BilibiliClient


class BilibiliUploader:
    PREUPLOAD_URL = "https://member.bilibili.com/preupload"
    COVER_UPLOAD_URL = "https://member.bilibili.com/x/vu/web/cover/up"
    SUBMIT_URL = "https://member.bilibili.com/x/vu/web/add/v3"
    VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
    LINE = {
        "os": "upos",
        "upcdn": "bda2",
        "probe_version": 20221109,
    }
    PROFILE = "ugcfx/bup"

    def upload_media(self, composition, credentials, progress_callback):
        source = composition.video
        source.open("rb")
        try:
            total = source.size
            client = BilibiliClient(credentials)
            if hasattr(client, "ensure_device_cookies"):
                client.ensure_device_cookies()
            preupload = self._preupload(client, PurePosixPath(source.name).name, total)
            endpoint = str(preupload.get("endpoint") or "")
            upos_uri = str(preupload.get("upos_uri") or "")
            auth = preupload.get("auth")
            if not endpoint or not upos_uri or not auth:
                raise PublishingRetryableError("Bilibili 未返回完整的上传会话。")
            if endpoint.startswith("//"):
                endpoint = f"https:{endpoint}"
            upload_url = f"{endpoint.rstrip('/')}/{upos_uri.removeprefix('upos://')}"
            headers = {"X-Upos-Auth": auth, "Referer": "https://www.bilibili.com/"}
            try:
                chunk_size = max(1, int(preupload.get("chunk_size") or 10 * 1024 * 1024))
                init_params = {
                    "uploads": "",
                    "output": "json",
                    "profile": self.PROFILE,
                    "filesize": total,
                    "partsize": chunk_size,
                    "biz_id": preupload.get("biz_id", ""),
                }
                _, init_payload = client.request_json(
                    "POST", upload_url, params=init_params, headers=headers
                )
                if init_payload.get("OK") not in {None, 1}:
                    raise PublishingRetryableError(
                        init_payload.get("message") or "Bilibili 初始化分片上传失败。"
                    )
                upload_id = init_payload.get("upload_id") or (init_payload.get("data") or {}).get("upload_id")
                if not upload_id:
                    raise PublishingRetryableError("Bilibili 未返回 upload_id。")
                chunks = max(1, math.ceil(total / chunk_size))
                parts = []
                uploaded = 0
                for index in range(chunks):
                    content = source.read(chunk_size)
                    params = {
                        "partNumber": index + 1,
                        "uploadId": upload_id,
                        "chunk": index,
                        "chunks": chunks,
                        "size": len(content),
                        "start": uploaded,
                        "end": uploaded + len(content),
                        "total": total,
                    }
                    response = self._upload_chunk(client, upload_url, params, headers, content)
                    etag = str(response.headers.get("ETag") or "etag").strip('"')
                    parts.append({"partNumber": index + 1, "eTag": etag})
                    uploaded += len(content)
                    progress_callback(uploaded, total)
                complete_params = {
                    "name": PurePosixPath(source.name).name,
                    "uploadId": upload_id,
                    "biz_id": preupload.get("biz_id", ""),
                    "output": "json",
                    "profile": self.PROFILE,
                }
                _, complete_payload = client.request_json(
                    "POST", upload_url, params=complete_params, headers=headers, json={"parts": parts}
                )
                if complete_payload.get("OK") != 1:
                    raise PublishingRetryableError(
                        complete_payload.get("message") or "Bilibili 合并视频分片失败。"
                    )
            finally:
                client.close()
            complete_key = str(complete_payload.get("key") or "").removeprefix("/")
            media_id = PurePosixPath(complete_key or upos_uri).stem
            return UploadResult(media_id=media_id, payload={"preupload": self._safe_preupload(preupload), "complete": complete_payload})
        finally:
            source.close()
            if "client" in locals():
                client.close()

    def _preupload(self, client, filename, size):
        _, payload = client.request_json(
            "GET",
            self.PREUPLOAD_URL,
            params={
                "name": filename,
                "size": size,
                "r": self.LINE["os"],
                "profile": self.PROFILE,
                "ssl": 0,
                "version": "2.14.0",
                "build": "2100400",
                "upcdn": self.LINE["upcdn"],
                "probe_version": self.LINE["probe_version"],
            },
        )
        if payload.get("OK") not in {None, 1}:
            platform_code = payload.get("code")
            platform_message = payload.get("message") or payload.get("info")
            if platform_code == 601:
                message = str(platform_message or "Bilibili upload rate limit reached. Please retry later.")
                raise PublishingRetryableError(
                    message,
                    code="platform_rate_limited",
                    details={
                        "platform_code": platform_code,
                        "platform_message": message,
                        "retry_after_seconds": 600,
                    },
                )
            raise PublishingRetryableError(payload.get("message") or "Bilibili 创建上传会话失败。")
        return payload

    def _upload_chunk(self, client, url, params, headers, content):
        last_error = None
        for attempt in range(3):
            if attempt:
                time.sleep(2 ** (attempt - 1))
            try:
                if hasattr(client, "request"):
                    response = client.request(
                        "PUT", url, params=params, headers=headers, content=content
                    )
                    text = response.text
                else:
                    response, payload = client.request_json(
                        "PUT", url, params=params, headers=headers, content=content
                    )
                    text = "" if payload.get("OK") == 1 else json.dumps(payload)
                if text not in {"", "MULTIPART_PUT_SUCCESS"}:
                    raise PublishingRetryableError("Bilibili 分片上传响应异常。")
                return response
            except PublishingRetryableError as exc:
                last_error = exc
        raise last_error or PublishingRetryableError("Bilibili 分片上传失败。")

    def submit(self, upload_result, metadata, credentials):
        csrf = credentials.get("bili_jct")
        if not csrf:
            raise PublishingValidationError("登录凭据缺少 bili_jct，请重新登录。", code="missing_csrf")
        client = BilibiliClient(credentials)
        try:
            if hasattr(client, "ensure_device_cookies"):
                client.ensure_device_cookies()
            cover_url = self._upload_cover(metadata.get("cover", ""), csrf, client)
            payload = {
                "copyright": metadata["copyright"],
                "source": metadata.get("source", ""),
                "tid": metadata["tid"],
                "cover": cover_url,
                "title": metadata["title"],
                "desc_format_id": 0,
                "desc": metadata.get("description", ""),
                "dynamic": metadata.get("dynamic", ""),
                "tag": ",".join(metadata.get("tags") or []),
                "subtitle": {"open": 0, "lan": ""},
                "interactive": 0,
                "no_reprint": 0,
                "dolby": 0,
                "lossless_music": 0,
                "videos": [
                    {
                        "filename": upload_result.media_id,
                        "title": metadata["title"],
                        "desc": "",
                    }
                ],
            }
            try:
                data, raw = client.checked_data(
                    "POST", self.SUBMIT_URL, params={"csrf": csrf, "t": int(time.time() * 1000)}, json=payload
                )
            except PublishingRetryableError as exc:
                raise PublishingOutcomeUnknown() from exc
        finally:
            client.close()
        data = data or {}
        bvid = str(data.get("bvid") or "")
        aid = str(data.get("aid") or "")
        remote_id = bvid or aid
        if not remote_id:
            raise PublishingOutcomeUnknown("Bilibili 接受了请求，但没有返回稿件 ID。")
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}"
        return SubmissionResult(remote_video_id=remote_id, remote_url=url, payload=raw)

    def _upload_cover(self, cover, csrf, client):
        cover = str(cover or "").strip()
        if not cover or cover.startswith(("http://", "https://")):
            return cover
        try:
            with default_storage.open(cover, "rb") as source:
                content = source.read()
        except (OSError, FileNotFoundError) as exc:
            raise PublishingValidationError(
                "单集封面文件不存在，请重新生成封面。",
                code="missing_cover",
            ) from exc
        mime_type = mimetypes.guess_type(cover)[0] or "image/jpeg"
        encoded = base64.b64encode(content).decode("ascii")
        data, _ = client.checked_data(
            "POST",
            self.COVER_UPLOAD_URL,
            data={
                "cover": f"data:{mime_type};base64,{encoded}",
                "csrf": csrf,
            },
        )
        cover_url = str((data or {}).get("url") or "").strip()
        if not cover_url:
            raise PublishingValidationError("Bilibili 未返回封面地址。")
        return cover_url

    def query_submission(self, remote_video_id, credentials):
        if not remote_video_id:
            return SubmissionResult("", "", status="unknown")
        params = {"bvid": remote_video_id} if remote_video_id.upper().startswith("BV") else {"aid": remote_video_id}
        client = BilibiliClient(credentials, timeout=20)
        try:
            _, payload = client.request_json("GET", self.VIEW_URL, params=params)
        finally:
            client.close()
        if payload.get("code") == 0:
            bvid = (payload.get("data") or {}).get("bvid") or remote_video_id
            return SubmissionResult(str(bvid), f"https://www.bilibili.com/video/{bvid}", status="published", payload=payload)
        return SubmissionResult(remote_video_id, "", status="submitted", payload=payload)

    @staticmethod
    def _safe_preupload(payload):
        return {key: value for key, value in payload.items() if key not in {"auth", "endpoint"}}
