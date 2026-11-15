"""会话（对话历史）端点。

语义对齐前端 `services/chat/db.ts`：
- `GET /api/conversations`            ≈ `loadSessionSummaries()`（仅列表元数据）
- `GET /api/conversations/<id>`       ≈ `loadSession(id)`（完整消息树）
- `PUT /api/conversations/<id>`       ≈ `saveSession()` / `renameSession()`（upsert，可只传 title）
- `DELETE /api/conversations/<id>`    ≈ `deleteSession()`
- `POST /api/conversations/replace`   ≈ `commitStaging()`（导入 / 清空，单事务替换）
"""

from __future__ import annotations

import time

from flask import Blueprint, current_app, jsonify, request

from security import login_required
from validators import ValidationError, parse_session

bp = Blueprint("conversations", __name__, url_prefix="/api/conversations")


def _database():
    return current_app.extensions["chat_db"]


def _owner_id() -> int | None:
    user = getattr(request, "chat_user", None)
    return user["id"] if user else None


@bp.get("")
@login_required
def list_conversations():
    return jsonify({"conversations": _database().list_conversations()})


@bp.post("")
@login_required
def create_conversation():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    now = int(time.time() * 1000)
    conversation_id = body.get("id") or f"c_{now:x}_{int(time.time_ns() % 100000):05d}"
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValidationError("id 必须是非空字符串")

    database = _database()
    if database.get_conversation(conversation_id) is not None:
        return jsonify({"error": "conflict", "message": "会话已存在"}), 409

    session_payload = {
        "id": conversation_id,
        "title": body.get("title") or "",
        "createdAt": int(body.get("createdAt") or now),
        "updatedAt": int(body.get("updatedAt") or now),
        "messages": body.get("messages") or [],
        "rootMessageId": None,
    }
    parsed = parse_session(session_payload)
    database.upsert_conversation(parsed, _owner_id())
    return jsonify(parsed), 201


@bp.get("/<conversation_id>")
@login_required
def get_conversation(conversation_id: str):
    session_payload = _database().get_conversation(conversation_id)
    if session_payload is None:
        return jsonify({"error": "not_found", "message": "会话不存在"}), 404
    return jsonify(session_payload)


@bp.put("/<conversation_id>")
@login_required
def save_conversation(conversation_id: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("请求体必须是 JSON 对象")

    database = _database()
    # 只传 title 时走轻量改名，避免为了改标题而回传整个消息树
    if set(body) <= {"title"} and "title" in body:
        title = body["title"]
        if not isinstance(title, str):
            raise ValidationError("title 必须是字符串")
        if not database.rename_conversation(conversation_id, title):
            return jsonify({"error": "not_found", "message": "会话不存在"}), 404
        summary = next(
            (item for item in database.list_conversations() if item["id"] == conversation_id), None
        )
        return jsonify(summary or {})

    session_payload = parse_session(body, expect_id=conversation_id)
    database.upsert_conversation(session_payload, _owner_id())
    return jsonify(
        {
            "id": session_payload["id"],
            "title": session_payload["title"],
            "createdAt": session_payload["createdAt"],
            "updatedAt": session_payload["updatedAt"],
        }
    )


@bp.delete("/<conversation_id>")
@login_required
def delete_conversation(conversation_id: str):
    database = _database()
    database.delete_conversation(conversation_id)
    # 与原 deleteSession 保持一致：会话删除后清理不再被引用的附件原件
    database.purge_unreferenced_files()
    # 删除是幂等的：会话不存在也返回 204
    return "", 204


@bp.delete("")
@login_required
def clear_conversations():
    _database().replace_conversations([], _owner_id())
    return "", 204


@bp.post("/replace")
@login_required
def replace_conversations():
    """导入 / 清空历史：整库原子替换，等价于原 `commitStaging()`。"""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("sessions", []), list):
        raise ValidationError("请求体需形如 {\"sessions\": [...]}")

    parsed = [parse_session(item) for item in body.get("sessions", [])]
    replaced = _database().replace_conversations(parsed, _owner_id())
    return jsonify({"count": len(parsed), "conversations": replaced})
