# doubao-seedance-2-0-260128 接入文档

本文介绍通过 OpenAI / New API 兼容视频接口接入 `doubao-seedance-2-0-260128`。接口采用 Bearer Token 鉴权和异步任务模式：先创建视频任务，再轮询任务状态，成功后下载视频。

## 1. 接口信息

| 项目 | 说明 |
| --- | --- |
| 模型 ID | `doubao-seedance-2-0-260128` |
| 鉴权方式 | `Authorization: Bearer <API_TOKEN>` |
| 查询模型 | `GET {BASE_URL}/models` |
| 创建视频 | `POST {BASE_URL}/video/generations` |
| 查询任务 | `GET {BASE_URL}/video/generations/{task_id}` |
| 调用模式 | 异步任务 |

`BASE_URL` 是服务商提供的 API 根地址，通常包含版本路径，例如：

```text
https://<gateway-host>/v1
```

不要把 `/video/generations` 重复写入 `BASE_URL`。

## 2. 鉴权

所有请求携带以下请求头：

```http
Authorization: Bearer <API_TOKEN>
Content-Type: application/json
```

建议将 Token 保存在环境变量中：

```dotenv
SEEDANCE_BASE_URL=https://<gateway-host>/v1
SEEDANCE_API_TOKEN=replace-with-your-api-token
SEEDANCE_MODEL=doubao-seedance-2-0-260128
```

不要将真实 Token 写入源代码、日志或版本库。

## 3. 验证模型权限

正式调用前，可以通过模型列表接口检查 Token 是否有权使用目标模型。

```bash
curl "${SEEDANCE_BASE_URL}/models" \
  -H "Authorization: Bearer ${SEEDANCE_API_TOKEN}" \
  -H "Content-Type: application/json"
```

响应示例：

```json
{
  "data": [
    {"id": "doubao-seedance-2-0-260128"}
  ]
}
```

确认 `data[].id` 中包含目标模型。如果不存在，请检查模型 ID、Token 权限或服务商的模型开通状态。

## 4. 创建视频任务

### 4.1 文生视频

```http
POST {BASE_URL}/video/generations
Authorization: Bearer <API_TOKEN>
Content-Type: application/json
```

请求体：

```json
{
  "model": "doubao-seedance-2-0-260128",
  "prompt": "电影感中景，年轻女性站在雨夜街头，镜头缓慢向前推进，霓虹灯倒映在湿润路面",
  "seconds": "5",
  "metadata": {
    "resolution": "720p",
    "ratio": "9:16",
    "watermark": false,
    "generate_audio": true
  }
}
```

### 4.2 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | 模型 ID |
| `prompt` | string | 是 | 视频提示词 |
| `seconds` | string | 是 | 视频时长，建议按字符串传递 |
| `metadata` | object | 是 | 视频生成参数 |

常用 `metadata` 参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `resolution` | string | 分辨率，例如 `720p` |
| `ratio` | string | 画面比例，例如 `9:16`、`16:9` |
| `seed` | integer | 随机种子 |
| `camera_fixed` | boolean | 是否固定镜头 |
| `watermark` | boolean | 是否添加水印 |
| `generate_audio` | boolean | 是否生成音频 |
| `draft` | boolean | 是否启用草稿模式 |
| `service_tier` | string | 服务等级，需网关支持 |
| `priority` | string/integer | 任务优先级，需网关支持 |
| `negative_prompt` | string | 负向提示词 |

不同服务商支持的参数可能不同。未确认支持的可选参数应先省略，再逐项测试。

### 4.3 参考图生视频

参考图通过 `metadata.content` 传入。图片 URL 可以是网关可访问的公网地址；如果网关支持，也可以使用 Base64 Data URI。

```json
{
  "model": "doubao-seedance-2-0-260128",
  "prompt": "保持参考图中的角色外观一致，角色转身看向镜头，衣摆随风摆动",
  "seconds": "5",
  "metadata": {
    "resolution": "720p",
    "ratio": "9:16",
    "watermark": false,
    "generate_audio": true,
    "content": [
      {
        "type": "image_url",
        "image_url": {
          "url": "data:image/png;base64,<BASE64_DATA>"
        },
        "role": "reference_image"
      }
    ]
  }
}
```

Data URI 格式为 `data:<MIME_TYPE>;base64,<BASE64_DATA>`。Base64 会使请求体明显变大，实际图片数量和大小应遵守网关限制。

### 4.4 创建响应

服务端应返回 `task_id` 或 `id`：

```json
{
  "task_id": "task-20260722-001",
  "request_id": "request-001",
  "status": "queued"
}
```

也可能返回：

```json
{
  "id": "task-20260722-001"
}
```

客户端应优先读取 `task_id`，不存在时再读取 `id`。两者都不存在时，应将创建请求视为失败。

## 5. 查询任务状态

```http
GET {BASE_URL}/video/generations/{task_id}
Authorization: Bearer <API_TOKEN>
Content-Type: application/json
```

建议每 5 秒查询一次，避免高频轮询。

成功响应示例：

```json
{
  "code": "success",
  "data": {
    "status": "SUCCESS",
    "result_url": "https://cdn.example.com/result.mp4"
  }
}
```

部分网关可能使用 `url`，或者使用嵌套字段 `data.data.content.video_url`。视频地址建议按以下顺序读取：

1. `data.result_url`
2. `data.url`
3. `data.data.content.video_url`

### 状态处理

| 服务端状态 | 客户端处理 |
| --- | --- |
| `QUEUED`, `PENDING` | 等待并继续轮询 |
| `IN_PROGRESS`, `PROCESSING`, `RUNNING` | 等待并继续轮询 |
| `SUCCESS`, `SUCCEEDED`, `COMPLETED` | 读取并下载视频 |
| `FAILURE`, `FAILED`, `CANCELLED`, `EXPIRED` | 停止轮询并记录错误 |

失败原因可依次读取 `data.fail_reason`、`data.message`、`data.data.error.message`。

## 6. cURL 示例

创建任务：

```bash
curl -X POST "${SEEDANCE_BASE_URL}/video/generations" \
  -H "Authorization: Bearer ${SEEDANCE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "doubao-seedance-2-0-260128",
    "prompt": "电影感近景，人物抬头看向天空，镜头缓慢环绕，柔和自然光",
    "seconds": "5",
    "metadata": {
      "resolution": "720p",
      "ratio": "9:16",
      "watermark": false,
      "generate_audio": true
    }
  }'
```

查询任务：

```bash
curl "${SEEDANCE_BASE_URL}/video/generations/<TASK_ID>" \
  -H "Authorization: Bearer ${SEEDANCE_API_TOKEN}" \
  -H "Content-Type: application/json"
```

## 7. Python 完整示例

安装依赖：

```bash
pip install httpx
```

调用代码：

```python
import os
import time

import httpx


BASE_URL = os.environ["SEEDANCE_BASE_URL"].rstrip("/")
API_TOKEN = os.environ["SEEDANCE_API_TOKEN"]
MODEL_ID = os.getenv("SEEDANCE_MODEL", "doubao-seedance-2-0-260128")

headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json",
}


def response_json(response):
    response.raise_for_status()
    payload = response.json()
    code = str(payload.get("code") or "").lower()
    if code and code not in {"success", "ok", "200"}:
        raise RuntimeError(payload.get("message") or code)
    return payload


with httpx.Client(timeout=600, trust_env=False) as client:
    create_response = client.post(
        f"{BASE_URL}/video/generations",
        headers=headers,
        json={
            "model": MODEL_ID,
            "prompt": "电影感近景，人物抬头看向天空，镜头缓慢环绕",
            "seconds": "5",
            "metadata": {
                "resolution": "720p",
                "ratio": "9:16",
                "watermark": False,
                "generate_audio": True,
            },
        },
    )
    create_data = response_json(create_response)
    task_id = create_data.get("task_id") or create_data.get("id")
    if not task_id:
        raise RuntimeError("服务端没有返回任务 ID")

    while True:
        query_response = client.get(
            f"{BASE_URL}/video/generations/{task_id}",
            headers=headers,
        )
        payload = response_json(query_response)
        task = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        upstream = task.get("data") if isinstance(task.get("data"), dict) else {}
        status = str(task.get("status") or upstream.get("status") or "UNKNOWN").upper()

        if status in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
            content = upstream.get("content") if isinstance(upstream.get("content"), dict) else {}
            video_url = task.get("result_url") or task.get("url") or content.get("video_url")
            if not video_url:
                raise RuntimeError("任务成功，但响应中没有视频地址")

            video_response = client.get(video_url, follow_redirects=True)
            video_response.raise_for_status()
            with open("result.mp4", "wb") as output:
                output.write(video_response.content)
            print("视频已保存为 result.mp4")
            break

        if status in {"FAILURE", "FAILED", "CANCELLED", "EXPIRED"}:
            error = upstream.get("error") if isinstance(upstream.get("error"), dict) else {}
            message = task.get("fail_reason") or task.get("message") or error.get("message")
            raise RuntimeError(message or f"视频任务失败：{status}")

        time.sleep(5)
```

## 8. 参考图转 Data URI

```python
import base64
import mimetypes
from pathlib import Path


def file_to_data_uri(path):
    path = Path(path)
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
```

## 9. 错误处理

- `401 / 403`：Token 无效、已过期或没有模型权限。
- `404`：Base URL 或接口路径错误。
- `429`：请求频率或并发超过限制，应使用指数退避重试。
- `5xx`：服务端临时异常，可进行有限次数重试。
- 没有任务 ID：确认创建响应顶层包含 `task_id` 或 `id`。
- 没有视频地址：检查 `result_url`、`url` 或 `data.content.video_url`。
- 下载链接失效：视频地址可能有有效期，生成成功后应及时下载。

## 10. 上线建议

- Token 只保存在密钥管理服务或环境变量中。
- 使用后台任务执行轮询，不要阻塞 Web 请求。
- 控制轮询频率、并发数和总等待时间。
- 保存 `task_id`、`request_id`、请求参数和原始响应，便于排查。
- 下载视频时允许 HTTP 重定向，并校验响应状态和文件大小。
- 对 `429` 和临时 `5xx` 使用带抖动的指数退避；鉴权和参数错误不要盲目重试。

