"""运行期配置：全部来自环境变量 / .env，代码中不出现任何密钥明文。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw).expanduser().resolve() if raw else default


class Config:
    """单用户场景的极简配置。"""

    # ---- 数据落地 ----
    DATA_DIR: Path = _env_path("CHAT_DATA_DIR", BASE_DIR / "data")
    FILES_DIR: Path = DATA_DIR / "files"
    DATABASE: Path = DATA_DIR / "chat-demo.sqlite3"

    # ---- 认证 ----
    # 用户名与密码哈希来自环境变量；未提供哈希时可用 CHAT_PASSWORD 由服务端现场哈希。
    CHAT_USERNAME: str = os.getenv("CHAT_USERNAME", "admin").strip() or "admin"
    CHAT_PASSWORD_HASH: str = os.getenv("CHAT_PASSWORD_HASH", "").strip()
    CHAT_PASSWORD: str = os.getenv("CHAT_PASSWORD", "")
    # 两个都没配时，首次启动生成随机密码并打印一次（不落明文，只落哈希）
    ALLOW_GENERATED_PASSWORD: bool = _env_bool("CHAT_ALLOW_GENERATED_PASSWORD", True)

    # ---- session ----
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "").strip()
    SESSION_COOKIE_NAME: str = "chat_demo_session"
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    SESSION_COOKIE_SECURE: bool = _env_bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME_HOURS: int = _env_int("SESSION_LIFETIME_HOURS", 24 * 14)

    # ---- DeepSeek 上游 ----
    DEEPSEEK_API_ORIGIN: str = os.getenv("DEEPSEEK_API_ORIGIN", "https://api.deepseek.com").rstrip("/")
    UPSTREAM_CONNECT_TIMEOUT: int = _env_int("UPSTREAM_CONNECT_TIMEOUT", 15)
    UPSTREAM_READ_TIMEOUT: int = _env_int("UPSTREAM_READ_TIMEOUT", 300)

    # ---- 上传 / 请求体 ----
    MAX_CONTENT_LENGTH: int = _env_int("MAX_CONTENT_LENGTH_MB", 64) * 1024 * 1024

    # ---- 前端产物 ----
    STATIC_DIST: Path = _env_path("STATIC_DIST", BASE_DIR / "public")

    # ---- 登录限流 ----
    LOGIN_MAX_ATTEMPTS: int = _env_int("LOGIN_MAX_ATTEMPTS", 8)
    LOGIN_WINDOW_SECONDS: int = _env_int("LOGIN_WINDOW_SECONDS", 300)


def resolve_secret_key(config: type[Config]) -> str:
    """FLASK_SECRET_KEY 优先；否则读取 data/secret_key；再否则生成并持久化（0600）。

    不把任何密钥写进代码或仓库，也不因重启丢失 session。
    """

    if config.SECRET_KEY:
        return config.SECRET_KEY

    key_file = config.DATA_DIR / "secret_key"
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        stored = key_file.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    generated = secrets.token_urlsafe(48)
    key_file.write_text(generated, encoding="utf-8")
    try:
        key_file.chmod(0o600)
    except OSError:  # Windows / 容器卷可能不支持
        pass
    return generated
