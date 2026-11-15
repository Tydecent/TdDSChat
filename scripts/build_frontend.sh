#!/usr/bin/env bash
# 构建前端并把产物同步到 server/public（Flask 直接托管）
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SERVER_DIR/.." && pwd)"

cd "$ROOT"
if [ ! -d node_modules ]; then
  pnpm install
fi
pnpm build

rm -rf "$SERVER_DIR/public"
mkdir -p "$SERVER_DIR/public"
cp -r "$ROOT/dist/." "$SERVER_DIR/public/"

echo "[build] 前端产物已同步到 $SERVER_DIR/public"
