"""附件原件端点。

原前端把附件 Blob 存在 IndexedDB 的 `files` store，用于 `file_id` 失效后补传。
迁移后原件落到服务端（元数据进 SQLite、字节进磁盘），语义保持不变：
按附件本地 id 存取，跨会话共用同一份。
"""

from __future__ import annotations

import re

from flask import Blueprint, current_app, jsonify, request, send_file

from security import login_required

bp = Blueprint("files", __name__, url_prefix="/api/files")

# 附件 id 由前端生成（uuid / 前缀 + 随机串），只允许文件安全字符。
# 不校验的话 `PUT /api/files/..` 会写到目录上（500），超长 id 会触发 ENAMETOOLONG。
FILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _database():
    return current_app.extensions["chat_db"]


def _valid_file_id(file_id: str) -> bool:
    return bool(FILE_ID_RE.fullmatch(file_id)) and ".." not in file_id


def _clean_name(raw: str) -> str:
    """文件名会进入 Content-Disposition，去掉控制字符并截断。"""
    cleaned = "".join(char for char in raw if char.isprintable()).strip()
    return cleaned[:255]


@bp.put("/<file_id>")
@login_required
def put_file(file_id: str):
    if not _valid_file_id(file_id):
        return jsonify({"error": "invalid_file_id", "message": "附件 id 不合法"}), 400
    raw = request.get_data(cache=False)
    if not raw:
        return jsonify({"error": "empty_body", "message": "文件内容为空"}), 400
    name = _clean_name(request.headers.get("X-File-Name", ""))
    mime_type = request.headers.get("X-File-Type") or request.mimetype or "application/octet-stream"
    try:
        from urllib.parse import unquote

        name = _clean_name(unquote(name))
    except Exception:  # pragma: no cover - 解码失败时保留原值
        pass
    _database().put_file(file_id, raw, name, mime_type)
    return jsonify({"id": file_id, "size": len(raw), "mimeType": mime_type}), 201


@bp.get("/<file_id>")
@login_required
def get_file(file_id: str):
    if not _valid_file_id(file_id):
        return jsonify({"error": "not_found", "message": "附件不存在"}), 404
    database = _database()
    meta = database.get_file_meta(file_id)
    # 只判断文件是否存在，不要把整份原件读进内存（大附件会白占几十 MB）
    if meta is None or not database.file_path(file_id).is_file():
        return jsonify({"error": "not_found", "message": "附件不存在"}), 404
    response = send_file(
        database.file_path(file_id),
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
    if _valid_file_id(file_id):
        _database().delete_file(file_id)
    # 删除是幂等的：非法 id 也返回 204，避免探测
    return "", 204


@bp.get("")
@login_required
def list_files():
    """调试用：只返回元数据，不返回字节。"""
    with _database().session() as conn:
        rows = conn.execute("SELECT id, name, mime_type, size, created_at FROM files").fetchall()
    return jsonify({"files": [dict(item) for item in rows]})
