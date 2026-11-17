"""认证与请求防护。

- 密码只以 werkzeug 哈希形式存在；明文仅作为环境变量在进程内短暂出现。
- session 由 Flask 签名 cookie 承载，服务端不存 session 文件。
- 所有写操作校验 Origin/Referer（同源），配合 SameSite=Lax 抵御 CSRF。
"""

from __future__ import annotations

import functools
import hmac
import secrets
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

from flask import Response, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import Database


class LoginRateLimiter:
    """按客户端 IP 的滑动窗口限流，纯内存实现，单进程够用。"""

    def __init__(self, limit: int, window: int) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        hits = [stamp for stamp in self._hits.get(key, []) if now - stamp < self.window]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def blocked(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            return len(self._prune(key, now)) >= self.limit

    def hit(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._prune(key, now)
            self._hits.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_key() -> str:
    return request.remote_addr or "unknown"


def ensure_user(database: Database, username: str, password_hash: str) -> None:
    """环境变量里的哈希/密码是唯一真相：变更后重启即生效，不必手动改库。"""
    user = database.get_user(username)
    if user is None:
        database.upsert_user(username, password_hash)
    elif user["password_hash"] != password_hash:
        database.update_password_hash(user["id"], password_hash)


def bootstrap_credentials(database: Database, config: Any) -> str | None:
    """确保单用户存在。返回自动生成的明文密码（仅首次、仅用于控制台提示）。"""
    username = config.CHAT_USERNAME
    if config.CHAT_PASSWORD_HASH:
        ensure_user(database, username, config.CHAT_PASSWORD_HASH)
        return None

    if config.CHAT_PASSWORD:
        if database.get_user(username) is None or not check_password_hash(
            database.get_user(username)["password_hash"], config.CHAT_PASSWORD
        ):
            database.upsert_user(username, generate_password_hash(config.CHAT_PASSWORD))
        return None

    if database.get_user(username) is not None:
        return None

    if not config.ALLOW_GENERATED_PASSWORD:
        raise RuntimeError(
            "未配置 CHAT_PASSWORD_HASH / CHAT_PASSWORD，且 CHAT_ALLOW_GENERATED_PASSWORD=0，无法创建登录账号。"
            "请运行 `python scripts/hash_password.py` 生成哈希后写入 .env。"
        )

    generated = secrets.token_urlsafe(12)
    database.upsert_user(username, generate_password_hash(generated))
    return generated


def current_user() -> dict[str, Any] | None:
    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        return None
    user = current_app.extensions["chat_db"].get_user_by_id(user_id)
    if user is None:
        session.clear()
        return None
    return {"id": user["id"], "username": user["username"]}


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        user = current_user()
        if user is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized", "message": "登录状态已失效，请重新登录"}), 401
            return current_app.extensions["chat_login_redirect"](request.full_path)
        request.chat_user = user  # type: ignore[attr-defined]
        return view(*args, **kwargs)

    return wrapped


def _same_origin() -> bool:
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    host = request.host
    source = origin or referer
    if not source:
        # 非浏览器客户端（curl / 脚本）通常不带 Origin，交给 session 校验兜底
        return True
    parsed = urlparse(source)
    return bool(parsed.netloc) and hmac.compare_digest(parsed.netloc, host)


def csrf_guard() -> Response | None:
    """写方法必须同源。返回 None 表示放行。"""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    if _same_origin():
        return None
    return jsonify({"error": "forbidden", "message": "跨站请求被拒绝"}), 403
