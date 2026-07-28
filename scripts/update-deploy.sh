#!/usr/bin/env bash
# CSVest — 服务器增量更新并部署
# 用法:
#   bash scripts/update-deploy.sh              # 拉一次并按变更智能部署
#   bash scripts/update-deploy.sh --watch      # 轮询远程仓库，有更新自动部署
#   bash scripts/update-deploy.sh --watch 15   # 每 15 秒检查一次（默认 30）
#   bash scripts/update-deploy.sh --force-api  # 强制重建 API 镜像
#   bash scripts/update-deploy.sh --reset-seed # 用镜像内 seed 覆盖 volume 运行库
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
RESET_SEED=0
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
    --reset-seed)
      RESET_SEED=1
      FORCE_API=1
      ;;
    --once)
      MODE="once"
      ;;
    -h|--help)
      sed -n '2,14p' "$0"
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

# API 重建后要等就绪；否则立刻 curl /api 会得到 nginx 502，看起来像整站挂了
wait_for_api() {
  local tries="${1:-36}"   # 默认最多约 3 分钟（5s × 36）
  local i code
  log "等待 API 就绪（最多 ${tries} 次，每 5s）..."
  for ((i = 1; i <= tries; i++)); do
    # 先直接打 API 容器端口，再经 nginx 反代确认
    if curl -fsS -m 3 "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
      code="$(curl -sS -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:8080/api/health" 2>/dev/null || echo 000)"
      if [[ "$code" == "200" ]]; then
        log "API 已就绪（第 ${i} 次，nginx /api/health → HTTP 200）"
        return 0
      fi
      log "API :8000 已通，等待 nginx 反代… (${i}/${tries}, /api → HTTP ${code})"
    else
      log "API 仍在启动… (${i}/${tries})"
    fi
    sleep 5
  done
  log "警告: API 在时限内未就绪。请检查: docker compose logs --tail=80 api"
  docker compose ps || true
  return 1
}

ensure_env() {
  if [[ ! -f backend/.env ]]; then
    cp backend/.env.example backend/.env
    log "已生成 backend/.env（可按需填写 DEEPSEEK_API_KEY）"
  fi
}

# 部署机本地戳 ?v=（不提交）；下次 pull 前会还原，避免 ff-only 冲突
restore_index_from_git() {
  if git status --porcelain -- index.html 2>/dev/null | grep -q .; then
    log "还原本地 index.html 缓存戳记，便于 pull"
    git checkout -- index.html
  fi
}

stamp_asset_cache_bust() {
  local ver
  ver="$(git rev-parse --short HEAD)"
  if [[ ! -f index.html ]]; then
    return 0
  fi
  # 统一把 style.css / *.js 的 ?v= 换成当前 commit，强制刷新后台字体与样式
  sed -i -E "s/((style\\.css|data\\.js|i18n\\.js|app\\.js|js\\/[A-Za-z0-9._-]+\\.js)\\?v=)[^\"]+/\\1${ver}/g" index.html
  log "已戳静态资源版本 ?v=${ver}（仅本机，下次 pull 前自动还原）"
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

  local need_api=0 need_web_recreate=0 need_web_reload=0 need_reset_seed=0

  if [[ "$FORCE_API" -eq 1 ]]; then
    need_api=1
  fi
  if [[ "${RESET_SEED:-0}" -eq 1 ]]; then
    need_api=1
    need_reset_seed=1
  fi

  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    case "$f" in
      backend/seed/skinvision.db|backend/seed/*)
        need_api=1
        need_reset_seed=1
        ;;
      Dockerfile|backend/requirements.txt|backend/*|ml/*|docs/*)
        need_api=1
        ;;
      docker-compose.yml|deploy/nginx-default.conf|deploy/*)
        need_web_recreate=1
        ;;
      index.html|app.js|style.css|data.js|i18n.js|js/*|assets/*|assets/**|links.html)
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

  # 若 assets 目录存在但容器内挂载为空（旧 compose），强制 recreate web
  if [[ -d "$ROOT/assets/landing" ]] && docker compose ps --status running -q web >/dev/null 2>&1; then
    if ! docker compose exec -T web test -f /usr/share/nginx/html/assets/landing/landing-visual-forecast.jpg 2>/dev/null; then
      log "容器内缺少 landing 截图 → 强制 recreate web（检查 assets volume 挂载）"
      need_web_recreate=1
    fi
  fi

  if [[ "$need_api" -eq 1 ]]; then
    if [[ "$need_reset_seed" -eq 1 ]]; then
      log "检测到 seed DB 变更 → 重建 api，并用 seed 覆盖 volume 运行库(保留 app_settings)"
      # 部署前先主动把运行库里的配置刷到 volume sidecar，避免空库备份冲掉旧备份
      if docker compose ps --status running -q api >/dev/null 2>&1; then
        docker compose exec -T api python3 - <<'PY' || true
from pathlib import Path
try:
    from settings_store import get_all_settings, _write_settings_sidecar
    s = get_all_settings()
    _write_settings_sidecar(s)
    print(f"[deploy] pre-reset sidecar keys={list(s)}")
except Exception as e:
    print(f"[deploy] pre-reset sidecar skipped: {e}")
PY
      fi
      # 仅本次 recreate 注入；随后立刻用默认 0 再 recreate，避免环境变量粘在容器上
      # 导致之后每次 restart 都冲掉运行库。
      RESET_DB_FROM_SEED=1 docker compose up -d --build --force-recreate api
      wait_for_api || true
      log "清除 RESET_DB_FROM_SEED 粘性标志 → 再 recreate api（默认 0）"
      unset RESET_DB_FROM_SEED || true
      RESET_DB_FROM_SEED=0 docker compose up -d --force-recreate --no-deps api
    else
      log "检测到后端相关变更 → 重建 api（可能较久）..."
      docker compose up -d --build api
    fi
    wait_for_api || true
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

  if [[ "$need_web_recreate" -eq 1 || "$need_web_reload" -eq 1 ]]; then
    stamp_asset_cache_bust
    # stamp 改了 index.html，reload 一次让新 ?v= 立刻生效
    docker compose exec -T web nginx -s reload 2>/dev/null || docker compose restart web >/dev/null 2>&1 || true
  fi

  if [[ "$need_api" -eq 0 && "$need_web_recreate" -eq 0 && "$need_web_reload" -eq 0 ]]; then
    log "无需容器操作"
  fi

  # 仅重建了 web、或之前 wait 失败时，再确认一次 API（避免 502 误报）
  if [[ "$need_api" -eq 0 ]]; then
    :
  else
    curl -fsS -m 3 "http://127.0.0.1:8000/api/health" >/dev/null 2>&1 || wait_for_api || true
  fi

  log "当前状态:"
  docker compose ps
  log "健康检查:"
  page_code="$(curl -sS -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:8080/" 2>/dev/null || echo err)"
  api_direct="$(curl -sS -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:8000/api/health" 2>/dev/null || echo err)"
  api_proxy="$(curl -sS -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:8080/api/health" 2>/dev/null || echo err)"
  echo "  首页 /              → HTTP ${page_code}"
  echo "  API  :8000/api/health → HTTP ${api_direct}"
  echo "  反代 /api/health     → HTTP ${api_proxy}"
  if [[ "$api_proxy" != "200" ]]; then
    log "若反代仍非 200：API 可能还在启动或崩溃。查看 docker compose logs --tail=80 api"
  fi
  echo
  log "落地页截图检查:"
  for f in \
    assets/landing/landing-visual-forecast.jpg \
    assets/landing/landing-visual-debate.jpg \
    assets/landing/landing-visual-portfolio.jpg
  do
    code="$(curl -sS -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:8080/$f" || echo err)"
    echo "  /$f → HTTP $code"
  done
  log "样式缓存戳检查:"
  curl -sS -m 5 "http://127.0.0.1:8080/index.html" | grep -oE 'style\.css\?v=[^"]+' | head -3 || true
  log "请浏览器强制刷新（Ctrl+Shift+R）后查看 Daily Report / 辩论页标题字体"
}

run_once() {
  ensure_env

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    log "当前目录不是 git 仓库: $ROOT" >&2
    exit 1
  fi

  # 丢弃可能污染 pull 的本地缓存戳记
  restore_index_from_git

  if [[ -n "$(git status --porcelain)" ]]; then
    log "警告: 工作区有未提交改动，仍尝试 fast-forward pull"
    git status --short | head -20
  fi

  if ! has_remote_updates && [[ "$FORCE_API" -eq 0 ]]; then
    log "已与 $REMOTE/$BRANCH 同步，无新提交"
    # watch 模式下也确认容器在跑；仍戳一次版本，避免旧 ?v= 卡住字体
    docker compose up -d --no-build >/dev/null 2>&1 || true
    stamp_asset_cache_bust
    docker compose exec -T web nginx -s reload 2>/dev/null || true
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
