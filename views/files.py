"""附件原件端点。

原前端把附件 Blob 存在 IndexedDB 的 `files` store，用于 `file_id` 失效后补传。
迁移后原件落到服务端（元数据进 SQLite、字节进磁盘），语义保持不变：
按附件本地 id 存取，跨会话共用同一份。
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, send_file

from security import login_required

bp = Blueprint("files", __name__, url_prefix="/api/files")


def _database():
    return current_app.extensions["chat_db"]


@bp.put("/<file_id>")
@login_required
def put_file(file_id: str):
    raw = request.get_data(cache=False)
    if not raw:
        return jsonify({"error": "empty_body", "message": "文件内容为空"}), 400
    name = request.headers.get("X-File-Name", "")
    mime_type = request.headers.get("X-File-Type") or request.mimetype or "application/octet-stream"
    try:
        from urllib.parse import unquote

        name = unquote(name)
    except Exception:  # pragma: no cover - 解码失败时保留原值
        pass
    _database().put_file(file_id, raw, name, mime_type)
    return jsonify({"id": file_id, "size": len(raw), "mimeType": mime_type}), 201


@bp.get("/<file_id>")
@login_required
def get_file(file_id: str):
    meta = _database().get_file_meta(file_id)
    data = _database().read_file(file_id)
    if meta is None or data is None:
        return jsonify({"error": "not_found", "message": "附件不存在"}), 404
    response = send_file(
        _database().file_path(file_id),
        mimetype=meta["mime_type"] or "application/octet-stream",
        as_attachment=False,
        download_name=meta["name"] or file_id,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@bp.delete("/<file_id>")
@login_required
def delete_file(file_id: str):
    _database().delete_file(file_id)
    return "", 204


@bp.get("")
@login_required
def list_files():
    """调试用：只返回元数据，不返回字节。"""
    with _database().session() as conn:
        rows = conn.execute("SELECT id, name, mime_type, size, created_at FROM files").fetchall()
    return jsonify({"files": [dict(item) for item in rows]})
