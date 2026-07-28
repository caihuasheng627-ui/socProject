#!/usr/bin/env bash
# 容器启动入口：volume 为空时灌入镜像内的全量 seed 库（约 680 件有行情）
# RESET_DB_FROM_SEED=1 时用 seed 覆盖运行库，但保留管理员已保存的 app_settings。
set -euo pipefail
SEED_DB="${SEED_DB:-/app/backend/seed/skinvision.db}"
RUNTIME_DB="${RUNTIME_DB:-/app/backend/data/skinvision.db}"
SETTINGS_BACKUP="${SETTINGS_BACKUP:-/app/backend/data/app_settings_backup.json}"
RESET_DB_FROM_SEED="${RESET_DB_FROM_SEED:-0}"

mkdir -p "$(dirname "$RUNTIME_DB")"

backup_app_settings() {
  local db="$1"
  local out="$2"
  if [[ ! -f "$db" || ! -s "$db" ]]; then
    return 0
  fi
  python3 - "$db" "$out" <<'PY'
import json, sqlite3, sys
db, out = sys.argv[1], sys.argv[2]
try:
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if not cur.fetchone():
        conn.close()
        raise SystemExit(0)
    rows = conn.execute("SELECT key, value, updated_at FROM app_settings").fetchall()
    conn.close()
    payload = [{"key": k, "value": v, "updated_at": t} for k, v, t in rows]
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[entrypoint] 已备份 app_settings {len(payload)} 项 → {out}")
except Exception as e:
    print(f"[entrypoint] 备份 app_settings 失败(忽略): {e}")
PY
}

restore_app_settings() {
  local db="$1"
  local backup="$2"
  if [[ ! -f "$backup" || ! -s "$backup" ]]; then
    return 0
  fi
  python3 - "$db" "$backup" <<'PY'
import json, sqlite3, sys
db, backup = sys.argv[1], sys.argv[2]
try:
    with open(backup, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list) or not rows:
        raise SystemExit(0)
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        )"""
    )
    for row in rows:
        key = (row or {}).get("key")
        value = (row or {}).get("value")
        updated = (row or {}).get("updated_at")
        if not key or value is None:
            continue
        conn.execute(
            """INSERT INTO app_settings(key, value, updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value,
                 updated_at=excluded.updated_at""",
            (str(key), str(value), updated),
        )
    conn.commit()
    conn.close()
    print(f"[entrypoint] 已恢复 app_settings {len(rows)} 项 ← {backup}")
except Exception as e:
    print(f"[entrypoint] 恢复 app_settings 失败(忽略): {e}")
PY
}

if [[ -f "$SEED_DB" ]]; then
  if [[ ! -f "$RUNTIME_DB" || ! -s "$RUNTIME_DB" ]]; then
    echo "[entrypoint] volume 无库 → 从 seed 灌入全量 SQLite"
    # 若曾有 sidecar 备份，灌库后仍可恢复（例如上次 reset 留下的）
    cp -f "$SEED_DB" "$RUNTIME_DB"
    restore_app_settings "$RUNTIME_DB" "$SETTINGS_BACKUP"
  elif [[ "$RESET_DB_FROM_SEED" == "1" ]]; then
    echo "[entrypoint] RESET_DB_FROM_SEED=1 → 用 seed 覆盖运行库(保留 app_settings)"
    backup_app_settings "$RUNTIME_DB" "$SETTINGS_BACKUP"
    cp -f "$SEED_DB" "$RUNTIME_DB"
    restore_app_settings "$RUNTIME_DB" "$SETTINGS_BACKUP"
  else
    echo "[entrypoint] 使用已有 volume 库: $RUNTIME_DB"
    # 库在但 settings 空、sidecar 有备份时补回（例如曾被旧 entrypoint 冲掉）
    restore_app_settings "$RUNTIME_DB" "$SETTINGS_BACKUP"
  fi
else
  echo "[entrypoint] 无 seed 库，将走 CSV 初始化（约 154 件有行情）"
fi

exec "$@"
