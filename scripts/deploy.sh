#!/usr/bin/env bash
# SkinVision AI — Docker 一键部署
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
echo "  API:    http://localhost:8000"
echo "  Docs:   http://localhost:8000/docs"
echo "  Health: http://localhost:8000/api/health"
echo "  Web:    http://localhost:8080"
echo
echo "前端切真实后端（浏览器控制台）:"
echo "  localStorage.setItem('sv_api_url','http://localhost:8000');"
echo "  localStorage.setItem('sv_use_mock','false'); location.reload();"
