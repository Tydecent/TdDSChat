"""设置端点（含 API Key 的安全边界）。

硬约束：`GET /api/settings` 永不返回 `apiKey` 明文，只返回 `hasApiKey`。
API Key 只在服务端使用：聊天 / 上传 / 朗读请求由 `/api/proxy/*` 转发时注入。
"""

from __future__ import annotations

import json

import requests
from flask import Blueprint, current_app, jsonify, request

from security import login_required
from validators import ValidationError, parse_settings_patch

bp = Blueprint("settings", __name__, url_prefix="/api/settings")


def _database():
    return current_app.extensions["chat_db"]


def _public_settings() -> dict:
    """对外视图：不含任何密文以外的秘密，apiKey 只以布尔形式出现。"""
    row = _database().get_settings()
    location = None
    if row.get("location"):
        try:
            location = json.loads(row["location"])
        except (TypeError, ValueError):
            location = None

    def _bool(value: object) -> bool:
        return bool(value)

    return {
        "hasApiKey": bool(row.get("api_key")),
        "locale": row.get("locale"),
        "themePreference": row.get("theme_preference"),
        "location": location,
        "ttsVoice": row.get("tts_voice"),
        "thinking": _bool(row.get("thinking")),
        "search": _bool(row.get("search")),
        "siderCollapsed": _bool(row.get("sider_collapsed")),
        # 明确告知前端：Key 由服务端持有，请求经服务端代理转发
        "apiKeyMode": "server",
    }


@bp.get("")
@login_required
def get_settings():
    return jsonify(_public_settings())


@bp.put("")
@login_required
def update_settings():
    body = request.get_json(silent=True)
    patch = parse_settings_patch(body)
    if patch:
        _database().update_settings(patch)
    return jsonify(_public_settings())


@bp.post("/api-key")
@login_required
def save_api_key():
    """校验后写入：先用候选 Key 调上游 GET /models（不产生费用），通过才落库。"""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("请求体必须是 JSON 对象")
    api_key = body.get("apiKey")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValidationError("apiKey 不能为空")

    api_key = api_key.strip()
    origin = current_app.config["DEEPSEEK_API_ORIGIN"]
    try:
        response = requests.get(
            f"{origin}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(current_app.config["UPSTREAM_CONNECT_TIMEOUT"], 30),
        )
    except requests.RequestException as exc:
        return jsonify({"error": {"message": f"无法连接 DeepSeek API：{exc}"}}), 502

    if response.status_code == 401:
        return jsonify({"error": {"message": "API Key 无效，请检查后重试"}}), 400
    if not response.ok:
        message = _upstream_message(response) or f"校验失败（HTTP {response.status_code}）"
        return jsonify({"error": {"message": message}}), 400

    _database().update_settings({"api_key": api_key})
    return jsonify({"hasApiKey": True})


@bp.delete("/api-key")
@login_required
def delete_api_key():
    _database().update_settings({"api_key": None})
    return jsonify({"hasApiKey": False})


def _upstream_message(response: requests.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
    return None
