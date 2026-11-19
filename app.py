"""Flask 应用工厂：认证会话、REST API、登录页与前端静态资源。"""

from __future__ import annotations

import logging
import mimetypes
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, unquote

from flask import Flask, Response, current_app, jsonify, redirect, request, send_from_directory

from config import Config, resolve_secret_key
from db import Database
from security import LoginRateLimiter, bootstrap_credentials, csrf_guard, current_user
from validators import ValidationError
from views import auth, conversations, files, proxy, settings

LOGIN_DIR = Path(__file__).resolve().parent / "login"

logger = logging.getLogger("chat-demo")


def _safe_next(raw: str | None) -> str:
    """只允许站内相对路径，避免开放重定向。"""
    if not raw:
        return "/"
    candidate = unquote(raw)
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    return candidate


def create_app(config_object: type[Config] = Config) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config_object)
    app.config["SECRET_KEY"] = resolve_secret_key(config_object)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        hours=config_object.PERMANENT_SESSION_LIFETIME_HOURS
    )
    app.json.ensure_ascii = False

    # ---- 数据层 ----
    database = Database(config_object.DATABASE, config_object.FILES_DIR)
    database.init()
    generated = bootstrap_credentials(database, config_object)
    app.extensions["chat_db"] = database
    app.extensions["chat_login_limiter"] = LoginRateLimiter(
        config_object.LOGIN_MAX_ATTEMPTS, config_object.LOGIN_WINDOW_SECONDS
    )

    def login_redirect(next_path: str) -> Response:
        return redirect(f"/login?next={quote(_safe_next(next_path), safe='')}")

    app.extensions["chat_login_redirect"] = login_redirect

    # ---- 蓝图 ----
    app.register_blueprint(auth.bp)
    app.register_blueprint(conversations.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(files.bp)
    app.register_blueprint(proxy.bp)

    @app.before_request
    def _protect_api():
        if request.path.startswith("/api/"):
            return csrf_guard()
        return None

    # ---- 错误处理 ----
    @app.errorhandler(ValidationError)
    def _bad_request(error: ValidationError):
        return jsonify({"error": "invalid_request", "message": str(error)}), 400

    @app.errorhandler(413)
    def _too_large(_error):
        limit = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        message = f"请求体超过 {limit} MB 上限"
        if request.path.startswith("/api/proxy/"):
            # 前端按 Anthropic 的错误结构取文案，超限时不该把整段 JSON 抛到 toast 上
            return jsonify({"error": {"type": "too_large", "message": message}}), 413
        return jsonify({"error": "too_large", "message": message}), 413

    @app.errorhandler(404)
    def _not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found", "message": "接口不存在"}), 404
        return _spa_index_or_404()

    # ---- 登录页 ----
    @app.get("/login")
    def login_page():
        if current_user() is not None:
            return redirect(_safe_next(request.args.get("next")))
        return send_from_directory(LOGIN_DIR, "index.html")

    @app.get("/login/<path:filename>")
    def login_asset(filename: str):
        return send_from_directory(LOGIN_DIR, filename)

    # ---- 前端 ----
    @app.get("/")
    def index():
        if current_user() is None:
            return login_redirect("/")
        return _spa_index_or_404()

    @app.get("/<path:filename>")
    def static_asset(filename: str):
        if filename.startswith("api/"):
            return jsonify({"error": "not_found", "message": "接口不存在"}), 404
        dist: Path = current_app.config["STATIC_DIST"]
        target = (dist / filename).resolve()
        if dist.exists() and target.is_file() and dist.resolve() in target.parents:
            mimetype, _ = mimetypes.guess_type(target.name)
            return send_from_directory(dist, filename, mimetype=mimetype)
        # 前端无客户端路由，未知路径回到首页
        return _spa_index_or_404()

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "authenticated": current_user() is not None})

    if generated:
        logger.warning(
            "未检测到 CHAT_PASSWORD / CHAT_PASSWORD_HASH，已生成一次性登录密码：\n"
            "    用户名：%s\n    密码：%s\n"
            "请立即登录并在 .env 中改用 CHAT_PASSWORD_HASH（python scripts/hash_password.py 生成）。",
            config_object.CHAT_USERNAME,
            generated,
        )

    return app


def _spa_index_or_404():
    dist: Path = current_app.config["STATIC_DIST"]
    index_file = Path(dist) / "index.html"
    if not index_file.is_file():
        return (
            Response(
                "<!doctype html><meta charset='utf-8'><title>前端未构建</title>"
                "<div style='font:16px/1.7 system-ui;padding:48px;max-width:720px;margin:auto'>"
                "<h1>前端产物缺失</h1>"
                f"<p>未找到 <code>{index_file}</code>。</p>"
                "<p>请在仓库根目录执行 <code>pnpm install &amp;&amp; pnpm build</code>，"
                "然后把 <code>dist/</code> 拷贝到 <code>server/public/</code>，"
                "或通过环境变量 <code>STATIC_DIST</code> 指向构建产物目录。</p>"
                "</div>",
                status=503,
                mimetype="text/html",
            )
        )
    return send_from_directory(dist, "index.html", max_age=0)
