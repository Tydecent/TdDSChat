"""DeepSeek API 反向代理：前端不再持有 API Key。

原前端以 `x-api-key` / `Authorization` 头直连 `api.deepseek.com`：
- `POST /anthropic/v1/messages`（SSE 流式对话）
- `POST /anthropic/v1/files`、`DELETE /anthropic/v1/files/{id}`（附件）
- `GET /models`（校验 Key）
- `POST /v1/tools/asr`、`/v1/tools/tts`（语音）

统一改为指向 `/api/proxy/<原路径>`，由本模块注入 Key 后转发。
端点保持完全一致的路径与响应形态，前端只需换 base URL，SSE / 二进制逻辑无需改动。
"""

from __future__ import annotations

import requests
from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from security import login_required

bp = Blueprint("proxy", __name__, url_prefix="/api/proxy")

# 逐跳头不转发
SKIP_REQUEST_HEADERS = {
    "host",
    "connection",
    "content-length",
    "accept-encoding",
    "authorization",
    "x-api-key",
    "cookie",
    "origin",
    "referer",
}
SKIP_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-encoding",
    "content-length",
    "set-cookie",
}

NO_KEY = {
    "error": {
        "message": "尚未配置 DeepSeek API Key，请先在「设置 → 数据管理」中填写并保存。",
    }
}


def _api_key() -> str | None:
    return current_app.extensions["chat_db"].get_api_key()


@bp.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@login_required
def forward(path: str):
    api_key = _api_key()
    if not api_key:
        return jsonify(NO_KEY), 400

    config = current_app.config
    url = f"{config['DEEPSEEK_API_ORIGIN']}/{path}"

    headers = {
        "x-api-key": api_key,
        "Authorization": f"Bearer {api_key}",
    }
    for name, value in request.headers.items():
        lowered = name.lower()
        if lowered in SKIP_REQUEST_HEADERS or lowered.startswith("x-forwarded"):
            continue
        headers[lowered] = value
    headers.setdefault("accept", "*/*")

    # 请求体必须原样透传，绝不能让 requests 重新编码 multipart：
    # 一旦把 request.files / request.form 交给 requests.files，它会用自己的新 boundary
    # 重新拼 body，而 Content-Type 头仍带着客户端原来的 boundary，上游（DeepSeek）
    # 按错误的 boundary 切分必然解析失败，所有文件上传 / 语音 ASR 都会 400。
    body = None
    if request.method in {"POST", "PUT", "PATCH"}:
        body = request.get_data(cache=True)

    try:
        upstream = requests.request(
            request.method,
            url,
            headers=headers,
            data=body,
            stream=True,
            timeout=(config["UPSTREAM_CONNECT_TIMEOUT"], config["UPSTREAM_READ_TIMEOUT"]),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        return jsonify({"error": {"message": f"上游请求失败：{exc}"}}), 502

    content_type = upstream.headers.get("Content-Type", "")

    if "text/event-stream" in content_type:
        return Response(
            stream_with_context(_relay(upstream)),
            status=upstream.status_code,
            content_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                # 流式响应结束后关闭连接：避免客户端复用仍在收尾的长连接发后续写请求
                "Connection": "close",
            },
        )

    body = upstream.content
    passthrough = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in SKIP_RESPONSE_HEADERS
    }
    upstream.close()
    return Response(body, status=upstream.status_code, headers=passthrough, content_type=content_type or None)


def _relay(upstream: requests.Response):
    """逐块转发 SSE；客户端断开时关闭上游连接，避免泄漏。"""
    try:
        for chunk in upstream.iter_content(chunk_size=None):
            if chunk:
                yield chunk
    except (requests.RequestException, GeneratorExit):
        return
    finally:
        upstream.close()
