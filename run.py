#!/usr/bin/env python3
"""开发入口：python run.py（生产建议 gunicorn -w 1 'app:create_app()'）。"""

from __future__ import annotations

import logging
import os

from app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    # 关闭 reloader：它会以子进程方式重启，导致启动日志中的一次性密码被打印两次
    app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG") == "1", use_reloader=False, threaded=True)
