"""认证端点：登录 / 登出 / 查询当前会话状态。"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash

from security import client_key, current_user

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# 统一的失败文案：不区分「用户不存在」与「密码错误」，避免账号枚举
INVALID_CREDENTIALS = {"error": "invalid_credentials", "message": "用户名或密码错误"}
# 用户不存在时也跑一次哈希校验，避免通过响应时间判断账号是否存在
DUMMY_HASH = "scrypt:32768:8:1$AAAAAAAAAAAA$" + "0" * 64


@bp.post("/login")
def login():
    limiter = current_app.extensions["chat_login_limiter"]
    key = client_key()
    if limiter.blocked(key):
        return jsonify({"error": "too_many_attempts", "message": "尝试次数过多，请稍后再试"}), 429

    body = request.get_json(silent=True)
    payload = body if isinstance(body, dict) else request.form.to_dict()
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password:
        return jsonify({"error": "invalid_request", "message": "请输入账号和密码"}), 400

    database = current_app.extensions["chat_db"]
    user = database.get_user(username)
    stored_hash = user["password_hash"] if user else DUMMY_HASH
    try:
        valid = check_password_hash(stored_hash, password)
    except ValueError:
        valid = False
    if user is None or not valid:
        limiter.hit(key)
        return jsonify(INVALID_CREDENTIALS), 401

    limiter.reset(key)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"authenticated": True, "username": user["username"]})


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"authenticated": False})


@bp.get("/me")
def me():
    user = current_user()
    if user is None:
        return jsonify({"authenticated": False, "username": None})
    return jsonify({"authenticated": True, "username": user["username"]})
