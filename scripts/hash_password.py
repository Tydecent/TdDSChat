#!/usr/bin/env python3
"""生成 werkzeug 密码哈希，写入 .env 的 CHAT_PASSWORD_HASH。

用法：
    python scripts/hash_password.py            # 交互式输入（不回显）
    python scripts/hash_password.py mypass     # 直接传入（会留在 shell history 中，不推荐）
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash  # noqa: E402


def main() -> None:
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("请输入登录密码：")
        confirm = getpass.getpass("请再次输入确认：")
        if password != confirm:
            raise SystemExit("两次输入不一致。")

    if len(password) < 8:
        raise SystemExit("密码至少 8 位。")

    # pbkdf2 兼容性最好（无需额外依赖）；scrypt 亦可，但部分精简版 Python 未启用
    print(generate_password_hash(password, method="pbkdf2:sha256:600000"))


if __name__ == "__main__":
    main()
