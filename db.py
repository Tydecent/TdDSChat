"""SQLite 访问层。

设计要点：
- `conversations.payload` 保存完整 Session JSON，`title/created_at/updated_at` 冗余成列，
  列表查询不必解析 JSON，且能无损保留 `rootMessageId` 的 undefined / null 语义。
- 附件原件同时写 `files` 元数据行与磁盘文件，供 Files API 的 file_id 失效后重新补传。
- 所有写操作使用显式事务，导入/清空走同一事务，保证原子性。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    api_key          TEXT,
    locale           TEXT,
    theme_preference TEXT,
    location         TEXT,
    tts_voice        TEXT,
    thinking         INTEGER,
    search           INTEGER,
    sider_collapsed  INTEGER,
    updated_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT    PRIMARY KEY,
    title      TEXT    NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    payload    TEXT    NOT NULL,
    owner_id   INTEGER REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);

CREATE TABLE IF NOT EXISTS files (
    id         TEXT    PRIMARY KEY,
    name       TEXT    NOT NULL DEFAULT '',
    mime_type  TEXT    NOT NULL DEFAULT '',
    size       INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
"""


def referenced_file_ids(sessions: list[Any]) -> set[str]:
    """汇总若干 Session 中被用户消息引用的附件 id。"""
    referenced: set[str] = set()
    for session in sessions:
        if not isinstance(session, dict):
            continue
        for message in session.get("messages") or []:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            for file in message.get("files") or []:
                if isinstance(file, dict) and isinstance(file.get("id"), str):
                    referenced.add(file["id"])
    return referenced


class Database:
    def __init__(self, path: Path, files_dir: Path) -> None:
        self.path = path
        self.files_dir = files_dir
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.session() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO settings (id, updated_at) VALUES (1, ?)", (int(time.time() * 1000),)
            )

    # ------------------------------------------------------------------ users

    def get_user(self, username: str) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def get_user_by_id(self, user_id: int) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def upsert_user(self, username: str, password_hash: str) -> sqlite3.Row:
        now = int(time.time() * 1000)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash,
                                                    updated_at   = excluded.updated_at
                """,
                (username, password_hash, now, now),
            )
            return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def update_password_hash(self, user_id: int, password_hash: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, int(time.time() * 1000), user_id),
            )

    # --------------------------------------------------------------- settings
    #
    # 注意：api_key 只在本模块与 proxy 中读取，任何响应构造都不得带出明文。

    def get_settings(self) -> dict[str, Any]:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        return dict(row) if row else {}

    def update_settings(self, fields: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "api_key",
            "locale",
            "theme_preference",
            "location",
            "tts_voice",
            "thinking",
            "search",
            "sider_collapsed",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        with self.transaction() as conn:
            if updates:
                assignments = ", ".join(f"{key} = ?" for key in updates)
                conn.execute(
                    f"UPDATE settings SET {assignments}, updated_at = ? WHERE id = 1",
                    (*updates.values(), int(time.time() * 1000)),
                )
        return self.get_settings()

    def get_api_key(self) -> str | None:
        value = self.get_settings().get("api_key")
        return value or None

    # ---------------------------------------------------------- conversations

    def list_conversations(self) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.session() as conn:
            row = conn.execute(
                "SELECT payload FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload"])

    def upsert_conversation(self, session: dict[str, Any], owner_id: int | None) -> None:
        payload = json.dumps(session, ensure_ascii=False, separators=(",", ":"))
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, title, created_at, updated_at, payload, owner_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET title      = excluded.title,
                                              created_at = excluded.created_at,
                                              updated_at = excluded.updated_at,
                                              payload    = excluded.payload
                """,
                (
                    session["id"],
                    session.get("title") or "",
                    int(session.get("createdAt") or 0),
                    int(session.get("updatedAt") or 0),
                    payload,
                    owner_id,
                ),
            )

    def rename_conversation(self, conversation_id: str, title: str) -> bool:
        """只改标题：读取 payload 后回写，保证 payload 的 title 与列一致。"""
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT payload FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                return False
            session = json.loads(row["payload"])
            session["title"] = title
            conn.execute(
                "UPDATE conversations SET title = ?, payload = ? WHERE id = ?",
                (title, json.dumps(session, ensure_ascii=False, separators=(",", ":")), conversation_id),
            )
        return True

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除会话，并清理**仅被该会话引用**的附件原件。

        不能顺手清理"所有无引用附件"：用户刚上传、还没发出去的原件同样处于
        "无引用"状态，那样会被连坐删除，之后 file_id 失效时无法补传。
        """
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT payload FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        if cursor.rowcount <= 0 or row is None:
            return False
        try:
            payload = json.loads(row["payload"])
        except ValueError:
            return True
        self.purge_unreferenced_files(only=referenced_file_ids([payload]))
        return True

    def purge_unreferenced_files(self, only: set[str] | None = None) -> int:
        """清理已无任何会话引用的附件，返回清理数量。

        `only` 为候选集合：只在这些 id 里挑孤儿，避免误删别处刚上传的原件。
        """
        with self.session() as conn:
            rows = conn.execute("SELECT payload FROM conversations").fetchall()
            existing = {row["id"] for row in conn.execute("SELECT id FROM files").fetchall()}
        sessions = []
        for row in rows:
            try:
                sessions.append(json.loads(row["payload"]))
            except ValueError:
                continue
        orphans = existing - referenced_file_ids(sessions)
        if only is not None:
            orphans &= only
        for file_id in sorted(orphans):
            self.delete_file(file_id)
        return len(orphans)

    def replace_conversations(self, sessions: list[dict[str, Any]], owner_id: int | None) -> list[dict[str, Any]]:
        """整库替换（导入 / 清空），单事务：失败整体回滚，成功后清理失去引用的原件。"""
        rows = [
            (
                item["id"],
                item.get("title") or "",
                int(item.get("createdAt") or 0),
                int(item.get("updatedAt") or 0),
                json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                owner_id,
            )
            for item in sessions
        ]
        with self.transaction() as conn:
            conn.execute("DELETE FROM conversations")
            if rows:
                conn.executemany(
                    "INSERT INTO conversations (id, title, created_at, updated_at, payload, owner_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
            removed = self._collect_orphan_files(conn, sessions)
        for file_id in removed:
            self.delete_file_blob(file_id)
        return self.list_conversations()

    def _collect_orphan_files(self, conn: sqlite3.Connection, sessions: list[dict[str, Any]]) -> list[str]:
        """删除已无任何会话引用的附件行，返回需要从磁盘移除的 id。"""
        referenced = referenced_file_ids(sessions)

        existing = {row["id"] for row in conn.execute("SELECT id FROM files").fetchall()}
        orphans = sorted(existing - referenced)
        if orphans:
            conn.executemany("DELETE FROM files WHERE id = ?", [(file_id,) for file_id in orphans])
        return orphans

    # ------------------------------------------------------------------ files

    def file_path(self, file_id: str) -> Path:
        return self.files_dir / file_id

    def put_file(self, file_id: str, data: bytes, name: str, mime_type: str) -> None:
        path = self.file_path(file_id)
        # 先写临时文件再原子替换：并发 GET 不会读到写了一半的原件
        tmp_path = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
        try:
            tmp_path.write_bytes(data)
            os.replace(tmp_path, path)
        finally:
            tmp_path.unlink(missing_ok=True)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO files (id, name, mime_type, size, created_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name,
                                              mime_type = excluded.mime_type,
                                              size = excluded.size
                """,
                (file_id, name, mime_type, len(data), int(time.time() * 1000)),
            )

    def get_file_meta(self, file_id: str) -> dict[str, Any] | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None

    def read_file(self, file_id: str) -> bytes | None:
        meta = self.get_file_meta(file_id)
        if meta is None:
            return None
        path = self.file_path(file_id)
        return path.read_bytes() if path.exists() else None

    def delete_file(self, file_id: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self.delete_file_blob(file_id)
        return cursor.rowcount > 0

    def delete_file_blob(self, file_id: str) -> None:
        try:
            self.file_path(file_id).unlink(missing_ok=True)
        except OSError:
            pass
