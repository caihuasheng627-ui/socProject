#!/usr/bin/env bash
# SkinVision AI — Docker 一键部署（首次/全量）
# 日常增量更新请用: bash scripts/update-deploy.sh
# 监视仓库自动部署: bash scripts/update-deploy.sh --watch
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f backend/.env ]]; then
  cp backend/.env.example backend/.env
  echo "[deploy] 已生成 backend/.env（可按需填写 DEEPSEEK_API_KEY）"
fi

echo "[deploy] docker compose up --build -d ..."
docker compose up --build -d

echo
echo "[deploy] 完成"
echo "  Web(+API 反代): http://localhost:8080"
echo "  API:            http://localhost:8000"
echo "  Docs:           http://localhost:8000/docs"
echo "  Health:         http://localhost:8080/api/health"
echo
echo "增量更新: bash scripts/update-deploy.sh"
echo "自动监视: bash scripts/update-deploy.sh --watch"