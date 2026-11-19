"""上传链路回归测试（stdlib unittest，无需额外依赖）。

跑法：python3 -m unittest discover -s tests -v

核心用例是「multipart 请求体必须原样透传」：曾经代理把 request.files 交给
requests 重新编码，body 换了 boundary 而 Content-Type 头还是客户端的旧
boundary，上游按旧 boundary 切分，所有文件上传都解析失败。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_BOUNDARY = "----WebKitFormBoundaryTestOnly"
CAPTURED: list[dict] = []

_upstream: ThreadingHTTPServer | None = None
_tmp: tempfile.TemporaryDirectory | None = None
_client = None


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        CAPTURED.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": self.rfile.read(length) if length else b"",
            }
        )
        raw = json.dumps({"id": "file_upstream_1", "type": "file"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_PUT = do_DELETE = _handle

    def log_message(self, *args):
        pass


def setUpModule() -> None:  # noqa: N802 - unittest 约定的模块级夹具
    """Config 在 import 时读取环境变量，因此这里一次性把环境与上游都准备好。"""
    global _upstream, _tmp, _client
    _upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=_upstream.serve_forever, daemon=True).start()
    _tmp = tempfile.TemporaryDirectory()
    os.environ.update(
        CHAT_USERNAME="admin",
        CHAT_PASSWORD="test-password",
        CHAT_PASSWORD_HASH="",
        CHAT_DATA_DIR=_tmp.name,
        FLASK_SECRET_KEY="t" * 32,
        DEEPSEEK_API_ORIGIN=f"http://127.0.0.1:{_upstream.server_address[1]}",
        MAX_CONTENT_LENGTH_MB="2",
    )

    from app import create_app

    app = create_app()
    app.extensions["chat_db"].update_settings({"api_key": "sk-test"})
    _client = app.test_client()
    login = _client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})
    assert login.status_code == 200, login.get_data(as_text=True)


def tearDownModule() -> None:  # noqa: N802
    if _upstream is not None:
        _upstream.shutdown()
        _upstream.server_close()
    if _tmp is not None:
        _tmp.cleanup()


def _build_multipart(purpose: str, filename: str, payload: bytes) -> bytes:
    return (
        f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\n{purpose}\r\n'
        f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + payload + f"\r\n--{_BOUNDARY}--\r\n".encode()


class UploadProxyTest(unittest.TestCase):
    def setUp(self) -> None:
        CAPTURED.clear()

    def test_multipart_body_is_forwarded_byte_identical(self) -> None:
        payload = "%PDF-1.4 中文内容".encode()
        body = _build_multipart("file", "季度报告.pdf", payload)
        response = _client.post(
            "/api/proxy/anthropic/v1/files",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
                "Origin": "http://localhost",
            },
        )
        self.assertEqual(response.status_code, 200)
        sent = CAPTURED[-1]
        self.assertEqual(sent["body"], body, "请求体被重新编码，boundary 与 Content-Type 不再匹配")
        self.assertEqual(sent["headers"]["content-type"], f"multipart/form-data; boundary={_BOUNDARY}")
        self.assertEqual(sent["headers"]["content-length"], str(len(body)))
        self.assertIn("季度报告.pdf".encode(), sent["body"])

    def test_json_body_is_forwarded_and_key_injected(self) -> None:
        response = _client.post(
            "/api/proxy/anthropic/v1/messages",
            json={"model": "deepseek-chat", "stream": True},
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 200)
        sent = CAPTURED[-1]
        self.assertEqual(sent["headers"]["content-type"], "application/json")
        self.assertEqual(json.loads(sent["body"]), {"model": "deepseek-chat", "stream": True})
        self.assertEqual(sent["headers"]["x-api-key"], "sk-test")

    def test_oversized_upload_returns_anthropic_error_shape(self) -> None:
        response = _client.post(
            "/api/proxy/anthropic/v1/files",
            data=b"x" * (3 * 1024 * 1024),
            headers={
                "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
                "Origin": "http://localhost",
            },
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("message", response.get_json()["error"])


class AttachmentEndpointTest(unittest.TestCase):
    def test_invalid_file_id_is_rejected(self) -> None:
        headers = {"Origin": "http://localhost", "X-File-Name": "a.txt"}
        self.assertEqual(_client.put("/api/files/..", data=b"x", headers=headers).status_code, 400)
        self.assertEqual(_client.put("/api/files/" + "a" * 300, data=b"x", headers=headers).status_code, 400)

    def test_roundtrip_and_header_safe_name(self) -> None:
        # 文件名头部允许 percent-encoded 的换行，解码后必须被清洗掉，避免头部注入
        encoded = "a%0d%0aX-Evil%3A%201.txt"
        headers = {"Origin": "http://localhost", "X-File-Name": encoded}
        self.assertEqual(_client.put("/api/files/f_1", data=b"hello", headers=headers).status_code, 201)
        response = _client.get("/api/files/f_1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"hello")
        self.assertNotIn("\r", response.headers["Content-Disposition"])
        self.assertNotIn("\n", response.headers["Content-Disposition"])
        self.assertIn("X-Evil: 1.txt", response.headers["Content-Disposition"])
        response.close()

    def test_deleting_conversation_keeps_unrelated_uploads(self) -> None:
        headers = {"Origin": "http://localhost"}
        conversation = {
            "id": "c1",
            "title": "t",
            "createdAt": 1,
            "updatedAt": 1,
            "messages": [
                {
                    "id": "m1",
                    "role": "user",
                    "content": "hi",
                    "files": [
                        {"id": "f_used", "name": "a.pdf", "size": 1, "isImage": False, "mimeType": "application/pdf"}
                    ],
                }
            ],
        }
        _client.put("/api/conversations/c1", json=conversation, headers=headers)
        _client.put("/api/files/f_used", data=b"a", headers=headers)
        _client.put("/api/files/f_pending", data=b"b", headers=headers)
        _client.delete("/api/conversations/c1", headers=headers)
        self.assertEqual(_client.get("/api/files/f_used").status_code, 404)
        pending = _client.get("/api/files/f_pending")
        self.assertEqual(pending.status_code, 200)
        pending.close()


if __name__ == "__main__":
    unittest.main()
