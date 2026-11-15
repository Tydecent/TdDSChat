#!/usr/bin/env bash
# 一键启动：首次运行会创建虚拟环境、安装依赖、生成 .env
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "[start] 创建虚拟环境 .venv"
  "$PY" -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[start] 已根据 .env.example 生成 .env"
  echo "[start] 若未配置 CHAT_PASSWORD_HASH / CHAT_PASSWORD，首次启动会打印一次性随机密码"
fi

exec ./.venv/bin/python run.py
