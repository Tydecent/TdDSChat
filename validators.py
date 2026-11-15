"""请求体校验：把前端传来的 Session 收敛成已知字段，拒绝脏数据进库。"""

from __future__ import annotations

import json
import time
from typing import Any


class ValidationError(ValueError):
    """请求体不合法，由蓝图转成 400。"""


def _require_str(value: Any, field: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} 必须是字符串")
    if not allow_empty and not value.strip():
        raise ValidationError(f"{field} 不能为空")
    return value


def _optional_int(value: Any, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} 必须是数字")
    return int(value)


def _normalize_file(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("message.files 中的元素必须是对象")
    file_id = _require_str(raw.get("id"), "file.id", allow_empty=False)
    server_id = raw.get("serverId")
    if server_id is not None and not isinstance(server_id, str):
        raise ValidationError("file.serverId 必须是字符串或 null")
    return {
        "id": file_id,
        "serverId": server_id,
        "name": _require_str(raw.get("name", ""), "file.name"),
        "size": _optional_int(raw.get("size"), "file.size", 0),
        "isImage": bool(raw.get("isImage", False)),
        "mimeType": _require_str(raw.get("mimeType", ""), "file.mimeType"),
    }


def _normalize_message(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("messages 中的元素必须是对象")
    message_id = _require_str(raw.get("id"), "message.id", allow_empty=False)
    role = raw.get("role")
    if role not in {"user", "assistant"}:
        raise ValidationError("message.role 只能是 user / assistant")

    normalized: dict[str, Any] = {"id": message_id, "role": role}
    if "parentId" in raw:
        parent = raw["parentId"]
        if parent is not None and not isinstance(parent, str):
            raise ValidationError("message.parentId 必须是字符串或 null")
        normalized["parentId"] = parent
    if "selectedChildId" in raw:
        selected = raw["selectedChildId"]
        if selected is not None and not isinstance(selected, str):
            raise ValidationError("message.selectedChildId 必须是字符串或 null")
        normalized["selectedChildId"] = selected

    if role == "user":
        normalized["content"] = _require_str(raw.get("content", ""), "message.content")
        files = raw.get("files", [])
        if not isinstance(files, list):
            raise ValidationError("message.files 必须是数组")
        normalized["files"] = [_normalize_file(item) for item in files]
        normalized["createdAt"] = _optional_int(raw.get("createdAt"), "message.createdAt", int(time.time() * 1000))
        return normalized

    # assistant：字段较多且多为可选，逐个白名单拷贝，未知字段丢弃（前端仍在演进）
    normalized["createdAt"] = _optional_int(raw.get("createdAt"), "message.createdAt", int(time.time() * 1000))
    normalized["model"] = raw.get("model") if isinstance(raw.get("model"), str) else None
    for flag in ("thinking", "search", "prefix"):
        if flag in raw:
            normalized[flag] = bool(raw[flag])
    if isinstance(raw.get("latestReminder"), str):
        normalized["latestReminder"] = raw["latestReminder"]
    status = raw.get("status")
    normalized["status"] = status if status in {"pending", "streaming", "done", "stopped", "error"} else "done"
    blocks = raw.get("blocks", [])
    if not isinstance(blocks, list):
        raise ValidationError("message.blocks 必须是数组")
    normalized["blocks"] = [block for block in blocks if isinstance(block, dict)]
    for key in ("stopReason", "stopDetails", "usage", "error"):
        if raw.get(key) is not None:
            normalized[key] = raw[key]
    return normalized


def parse_session(raw: Any, *, expect_id: str | None = None) -> dict[str, Any]:
    """把任意请求体收敛成合法 Session；`rootMessageId` 的 undefined / null 语义原样保留。"""
    if not isinstance(raw, dict):
        raise ValidationError("请求体必须是 JSON 对象")

    session = raw["session"] if isinstance(raw.get("session"), dict) else raw
    session_id = _require_str(session.get("id"), "session.id", allow_empty=False)
    if expect_id is not None and session_id != expect_id:
        raise ValidationError("URL 中的会话 id 与请求体不一致")

    now = int(time.time() * 1000)
    messages = session.get("messages", [])
    if not isinstance(messages, list):
        raise ValidationError("session.messages 必须是数组")

    parsed: dict[str, Any] = {
        "id": session_id,
        "title": _require_str(session.get("title", ""), "session.title"),
        "createdAt": _optional_int(session.get("createdAt"), "session.createdAt", now),
        "updatedAt": _optional_int(session.get("updatedAt"), "session.updatedAt", now),
        "messages": [_normalize_message(item) for item in messages],
    }
    if "rootMessageId" in session:
        root = session["rootMessageId"]
        if root is not None and not isinstance(root, str):
            raise ValidationError("session.rootMessageId 必须是字符串或 null")
        parsed["rootMessageId"] = root
    return parsed


def parse_settings_patch(raw: Any) -> dict[str, Any]:
    """设置项的增量更新（PUT /api/settings 的语义）。只接受已知字段。"""
    if not isinstance(raw, dict):
        raise ValidationError("请求体必须是 JSON 对象")

    patch: dict[str, Any] = {}

    if "apiKey" in raw:
        api_key = raw["apiKey"]
        if api_key is not None and not isinstance(api_key, str):
            raise ValidationError("apiKey 必须是字符串或 null")
        # 空字符串等同于未配置
        patch["api_key"] = (api_key or "").strip() or None

    if "locale" in raw:
        locale = raw["locale"]
        if locale not in {None, "zh-CN", "en-US"}:
            raise ValidationError("locale 只能是 zh-CN / en-US")
        patch["locale"] = locale

    if "themePreference" in raw:
        theme = raw["themePreference"]
        if theme not in {None, "light", "dark", "system"}:
            raise ValidationError("themePreference 只能是 light / dark / system")
        patch["theme_preference"] = theme

    if "location" in raw:
        location = raw["location"]
        if location is not None:
            if not isinstance(location, dict) or not isinstance(location.get("region"), str):
                raise ValidationError("location 必须是含 region 的对象或 null")
            for key in ("province", "city"):
                if location.get(key) is not None and not isinstance(location.get(key), str):
                    raise ValidationError(f"location.{key} 必须是字符串")
            location = {"region": location["region"], "province": location.get("province"), "city": location.get("city")}
            location = {key: value for key, value in location.items() if value is not None}
        patch["location"] = None if location is None else json.dumps(location, ensure_ascii=False)

    if "ttsVoice" in raw:
        voice = raw["ttsVoice"]
        if voice not in {None, "stella", "tide"}:
            raise ValidationError("ttsVoice 只能是 stella / tide")
        patch["tts_voice"] = voice

    for camel, column in (("thinking", "thinking"), ("search", "search"), ("siderCollapsed", "sider_collapsed")):
        if camel in raw:
            value = raw[camel]
            if value is not None and not isinstance(value, bool):
                raise ValidationError(f"{camel} 必须是布尔值")
            patch[column] = None if value is None else int(value)

    return patch
