#!/usr/bin/env bash
# CSVest — 服务器增量更新并部署
# 用法:
#   bash scripts/update-deploy.sh              # 拉一次并按变更智能部署
#   bash scripts/update-deploy.sh --watch      # 轮询远程仓库，有更新自动部署
#   bash scripts/update-deploy.sh --watch 15   # 每 15 秒检查一次（默认 30）
#   bash scripts/update-deploy.sh --force-api  # 强制重建 API 镜像
#
# 策略（避免无意义重装 pip）:
#   - 仅 Dockerfile / requirements / backend|ml|docs 变更 → 重建 api
#   - nginx / compose 前端相关变更 → 重建 web 容器
#   - 仅前端静态文件变更 → git pull 后 reload nginx（volume 已挂载，无需 build）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BRANCH="${DEPLOY_BRANCH:-main}"
REMOTE="${DEPLOY_REMOTE:-origin}"
WATCH_INTERVAL=30
FORCE_API=0
MODE="once"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --watch|-w)
      MODE="watch"
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        WATCH_INTERVAL="$2"
        shift
      fi
      ;;
    --force-api)
      FORCE_API=1
      ;;
    --once)
      MODE="once"
      ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "[update-deploy] 未知参数: $1" >&2
      exit 1
      ;;
  esac
  shift
done

log() { echo "[update-deploy $(date '+%F %T')] $*"; }

ensure_env() {
  if [[ ! -f backend/.env ]]; then
    cp backend/.env.example backend/.env
    log "已生成 backend/.env（可按需填写 DEEPSEEK_API_KEY）"
  fi
}

# 返回 0 = 有远程新提交；1 = 已是最新
has_remote_updates() {
  git fetch --quiet "$REMOTE" "$BRANCH"
  local local_sha remote_sha
  local_sha="$(git rev-parse HEAD)"
  remote_sha="$(git rev-parse "$REMOTE/$BRANCH")"
  [[ "$local_sha" != "$remote_sha" ]]
}

classify_and_deploy() {
  local before="$1"
  local after="$2"
  local changed
  changed="$(git diff --name-only "$before" "$after" || true)"

  if [[ -z "$changed" && "$FORCE_API" -eq 0 ]]; then
    log "无文件变更，跳过部署"
    return 0
  fi

  log "变更文件:"
  echo "$changed" | sed 's/^/  - /'

  local need_api=0 need_web_recreate=0 need_web_reload=0

  if [[ "$FORCE_API" -eq 1 ]]; then
    need_api=1
  fi

  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    case "$f" in
      Dockerfile|backend/requirements.txt|backend/*|ml/*|docs/*)
        need_api=1
        ;;
      docker-compose.yml|deploy/nginx-default.conf|deploy/*)
        need_web_recreate=1
        ;;
      index.html|app.js|style.css|data.js|i18n.js|js/*)
        need_web_reload=1
        ;;
      scripts/update-deploy.sh|scripts/deploy.sh|README.md|*.md)
        ;;
      *)
        # 其它仓库文件：默认只刷新 web，避免漏更前端
        need_web_reload=1
        ;;
    esac
  done <<< "$changed"

  if [[ "$need_api" -eq 1 ]]; then
    need_web_recreate=1
  fi

  if [[ "$need_api" -eq 1 ]]; then
    log "检测到后端相关变更 → 重建 api（可能较久）..."
    docker compose up -d --build api
  fi

  if [[ "$need_web_recreate" -eq 1 ]]; then
    log "重建 web 容器（nginx / compose）..."
    docker compose up -d --force-recreate --no-deps web
  elif [[ "$need_web_reload" -eq 1 ]]; then
    log "前端文件已通过 volume 更新 → reload nginx"
    if docker compose exec -T web nginx -s reload 2>/dev/null; then
      log "nginx reload 成功"
    else
      log "reload 失败，改用 restart web"
      docker compose restart web
    fi
  fi

  if [[ "$need_api" -eq 0 && "$need_web_recreate" -eq 0 && "$need_web_reload" -eq 0 ]]; then
    log "无需容器操作"
  fi

  log "当前状态:"
  docker compose ps
  log "健康检查:"
  curl -sS -m 5 "http://127.0.0.1:8080/api/health" || curl -sS -m 5 "http://127.0.0.1:8000/api/health" || true
  echo
}

run_once() {
  ensure_env

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    log "当前目录不是 git 仓库: $ROOT" >&2
    exit 1
  fi

  # 丢弃可能污染 pull 的本地无关改动（仅警告，不强制 reset）
  if [[ -n "$(git status --porcelain)" ]]; then
    log "警告: 工作区有未提交改动，仍尝试 fast-forward pull"
    git status --short | head -20
  fi

  if ! has_remote_updates && [[ "$FORCE_API" -eq 0 ]]; then
    log "已与 $REMOTE/$BRANCH 同步，无新提交"
    # watch 模式下也确认容器在跑
    docker compose up -d --no-build >/dev/null 2>&1 || true
    return 0
  fi

  local before after
  before="$(git rev-parse HEAD)"
  log "拉取 $REMOTE/$BRANCH ..."
  git pull --ff-only "$REMOTE" "$BRANCH"
  after="$(git rev-parse HEAD)"
  log "更新: ${before:0:7} → ${after:0:7}"

  classify_and_deploy "$before" "$after"
}

run_watch() {
  log "进入监视模式：每 ${WATCH_INTERVAL}s 检查 $REMOTE/$BRANCH（Ctrl+C 退出）"
  ensure_env
  docker compose up -d --no-build >/dev/null 2>&1 || docker compose up -d
  while true; do
    if run_once; then
      :
    else
      log "本轮失败，${WATCH_INTERVAL}s 后重试"
    fi
    sleep "$WATCH_INTERVAL"
  done
}

case "$MODE" in
  watch) run_watch ;;
  *)     run_once ;;
esac
