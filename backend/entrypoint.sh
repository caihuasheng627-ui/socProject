#!/usr/bin/env bash
# 容器启动入口：volume 为空时灌入镜像内的全量 seed 库（约 681 件有行情）
set -euo pipefail
SEED_DB="${SEED_DB:-/app/backend/seed/skinvision.db}"
RUNTIME_DB="${RUNTIME_DB:-/app/backend/data/skinvision.db}"
RESET_DB_FROM_SEED="${RESET_DB_FROM_SEED:-0}"

mkdir -p "$(dirname "$RUNTIME_DB")"

if [[ -f "$SEED_DB" ]]; then
  if [[ ! -f "$RUNTIME_DB" || ! -s "$RUNTIME_DB" ]]; then
    echo "[entrypoint] volume 无库 → 从 seed 灌入全量 SQLite"
    cp -f "$SEED_DB" "$RUNTIME_DB"
  elif [[ "$RESET_DB_FROM_SEED" == "1" ]]; then
    echo "[entrypoint] RESET_DB_FROM_SEED=1 → 用 seed 覆盖运行库"
    cp -f "$SEED_DB" "$RUNTIME_DB"
  else
    echo "[entrypoint] 使用已有 volume 库: $RUNTIME_DB"
  fi
else
  echo "[entrypoint] 无 seed 库，将走 CSV 初始化（约 154 件有行情）"
fi

exec "$@"
