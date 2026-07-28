"""
给价格序列加温和、可复现的模拟波动：
1) trailing_fill / forward_fill：把持平尾部改成围绕锚点的均值回复路径
2) 连续 ≥N 天同价（含插值直线）：在锚点附近加小幅抖动

不发明大趋势：零漂移、相对历史波动的一小部分，并限制累计偏离。
"""
from __future__ import annotations

import argparse
import math
import random
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


SYNTHETIC_FLAT_REASONS = frozenset({"trailing_fill", "forward_fill", "back_fill"})
# 已是模拟波动的标记，重复跑脚本时跳过（幂等）
ALREADY_SIMULATED = frozenset(
    {"simulated_trailing", "simulated_forward", "simulated_wiggle", "simulated_bridge"}
)
SPIKE_REASONS = frozenset({"isolated_price_spike", "endpoint_price_spike"})

MIN_FLAT_RUN = 5
VOL_FLOOR = 0.004  # 0.4%/日
VOL_CAP = 0.03
WIGGLE_SCALE = 0.45  # 相对历史波动的倍数（偏温和）
MEAN_REVERT = 0.28
ENVELOPE = 0.035  # 相对锚点最大 ±3.5%
MIN_ABS_MOVE = 0.01  # 极廉价品最小跳动（美元）


def _connect(db_path: Path | None) -> sqlite3.Connection:
    if db_path is not None:
        return sqlite3.connect(str(db_path), timeout=60)
    from database import get_connection

    return get_connection()


def _round_price(p: float, anchor: float) -> float:
    """按价格量级取合理小数位，且不低于 1 美分。"""
    if not math.isfinite(p) or p <= 0:
        return max(MIN_ABS_MOVE, round(anchor, 4))
    if anchor >= 50:
        q = round(p, 2)
    elif anchor >= 5:
        q = round(p, 2)
    elif anchor >= 1:
        q = round(p, 3)
    else:
        q = round(p, 4)
    if q < MIN_ABS_MOVE:
        q = MIN_ABS_MOVE
    # 若舍入后与锚点完全相同且锚点够大，至少挪半个最小刻度，避免仍是直线
    if abs(q - round(anchor, 4)) < 1e-12 and anchor >= MIN_ABS_MOVE * 2:
        step = 0.01 if anchor >= 1 else 0.001 if anchor >= 0.1 else 0.0001
        q = round(anchor + step, 4)
    return q


def _empirical_vol(prices: list[float]) -> float:
    rets: list[float] = []
    for a, b in zip(prices, prices[1:]):
        if a > 0 and b > 0:
            r = math.log(b / a)
            if math.isfinite(r) and abs(r) < 0.5:
                rets.append(r)
    if len(rets) < 5:
        return VOL_FLOOR
    # MAD-based robust vol
    med = sorted(rets)[len(rets) // 2]
    mad = sorted(abs(r - med) for r in rets)[len(rets) // 2]
    vol = max(1.4826 * mad, VOL_FLOOR)
    # also consider std of recent window
    recent = rets[-30:]
    mean = sum(recent) / len(recent)
    var = sum((r - mean) ** 2 for r in recent) / max(1, len(recent) - 1)
    vol = max(vol, math.sqrt(var) if var > 0 else 0.0, VOL_FLOOR)
    return min(vol, VOL_CAP)


def _rng_for(skin_id: int, tag: str) -> random.Random:
    # 稳定种子，便于重复跑结果一致
    return random.Random(f"csvest-wiggle|{skin_id}|{tag}")


def _path_around_anchor(
    n: int,
    anchor: float,
    vol: float,
    rng: random.Random,
    *,
    end_at_anchor: bool = False,
) -> list[float]:
    """零漂移 + 弱均值回复路径；可选终点贴回锚点（桥）。"""
    if n <= 0:
        return []
    if anchor <= 0:
        anchor = MIN_ABS_MOVE
    sigma = max(vol * WIGGLE_SCALE, VOL_FLOOR * 0.5)
    log_a = math.log(anchor)
    log_p = log_a
    out: list[float] = []
    for i in range(n):
        shock = rng.gauss(0.0, sigma)
        log_p = log_p + shock + MEAN_REVERT * (log_a - log_p)
        # 包络
        lo = log_a + math.log(1.0 - ENVELOPE)
        hi = log_a + math.log(1.0 + ENVELOPE)
        log_p = min(hi, max(lo, log_p))
        out.append(math.exp(log_p))
    if end_at_anchor and out:
        # 线性把终点拉回锚点，保留中间抖动形状
        end = out[-1]
        if end > 0:
            for i in range(n):
                w = (i + 1) / n
                out[i] = out[i] * (1 - w) + anchor * w
    return [_round_price(p, anchor) for p in out]


def _ensure_not_flat(path: list[float], anchor: float, rng: random.Random) -> list[float]:
    """若舍入后仍全平，强制加微小交替抖动。"""
    if not path:
        return path
    if len({round(p, 4) for p in path}) > 1:
        return path
    step = 0.01 if anchor >= 1 else 0.001 if anchor >= 0.1 else 0.0001
    out = []
    for i, p in enumerate(path):
        sign = 1 if (i + rng.randint(0, 1)) % 2 == 0 else -1
        out.append(_round_price(anchor + sign * step * ((i % 3) + 1) * 0.5, anchor))
    if len({round(p, 4) for p in out}) == 1:
        out[-1] = _round_price(anchor + step, anchor)
    return out


def _load_series(conn: sqlite3.Connection, skin_id: int) -> list[tuple]:
    return conn.execute(
        """SELECT date, price, raw_price, is_outlier, outlier_reason
           FROM price_history WHERE skin_id=? ORDER BY date""",
        (skin_id,),
    ).fetchall()


def _observed_prices(rows: list[tuple]) -> list[float]:
    out = []
    for _d, price, _raw, _out, reason in rows:
        r = (reason or "").strip()
        if r in SYNTHETIC_FLAT_REASONS or r in ALREADY_SIMULATED:
            continue
        if price and price > 0:
            out.append(float(price))
    return out


def wiggle_skin(conn: sqlite3.Connection, skin_id: int, min_flat_run: int) -> dict[str, int]:
    rows = _load_series(conn, skin_id)
    if len(rows) < 5:
        return {"trailing": 0, "flat": 0}

    observed = _observed_prices(rows)
    if len(observed) < 5:
        observed = [float(r[1]) for r in rows if r[1] and r[1] > 0]
    vol = _empirical_vol(observed)
    stats = {"trailing": 0, "flat": 0}

    # ---- 1) 替换 synthetic flat 尾部 / 前向填充 ----
    # 找连续 synthetic 段，用段前最后一个非 synthetic 价作锚点
    i = 0
    n = len(rows)
    updates: list[tuple] = []  # (price, raw_price, reason, skin_id, date)

    while i < n:
        reason = (rows[i][4] or "").strip()
        if reason not in SYNTHETIC_FLAT_REASONS:
            i += 1
            continue
        j = i
        while j < n and (rows[j][4] or "").strip() in SYNTHETIC_FLAT_REASONS:
            j += 1
        # 锚点：段前一日
        if i > 0:
            anchor = float(rows[i - 1][1])
        else:
            anchor = float(rows[i][1])
        length = j - i
        tag = "trail" if reason == "trailing_fill" else "fwd"
        rng = _rng_for(skin_id, f"{tag}|{rows[i][0]}|{length}")
        path = _path_around_anchor(length, anchor, vol, rng, end_at_anchor=False)
        path = _ensure_not_flat(path, anchor, rng)
        new_reason = "simulated_trailing" if reason == "trailing_fill" else "simulated_forward"
        for k, (date, _p, _raw, _o, _r) in enumerate(rows[i:j]):
            updates.append((path[k], None, new_reason, skin_id, date))
        stats["trailing"] += length
        i = j

    if updates:
        conn.executemany(
            """UPDATE price_history
               SET price=?, raw_price=?, outlier_reason=?, is_outlier=0
               WHERE skin_id=? AND date=?""",
            updates,
        )
        # 重新加载，再处理剩余直线段
        rows = _load_series(conn, skin_id)

    # ---- 2) 连续同价 ≥ min_flat_run：轻度抖动（保留首日作锚）----
    updates = []
    run_start = 0
    while run_start < len(rows):
        run_end = run_start + 1
        p0 = round(float(rows[run_start][1]), 4)
        while run_end < len(rows) and round(float(rows[run_end][1]), 4) == p0:
            run_end += 1
        run_len = run_end - run_start
        if run_len >= min_flat_run:
            # 跳过已全是 simulated_* 且已有波动的段（价格仍平才处理）
            reasons = [(rows[k][4] or "").strip() for k in range(run_start, run_end)]
            # 不改 outlier spike 标记行的价格语义：若整段都是 spike 则跳过
            if all(r in SPIKE_REASONS for r in reasons):
                run_start = run_end
                continue
            anchor = float(rows[run_start][1])
            # 抖动除首日外的天数（首日保持）
            m = run_len - 1
            if m > 0:
                rng = _rng_for(skin_id, f"flat|{rows[run_start][0]}|{run_len}")
                path = _path_around_anchor(m, anchor, vol, rng, end_at_anchor=True)
                path = _ensure_not_flat(path, anchor, rng)
                for k, idx in enumerate(range(run_start + 1, run_end)):
                    date, _old, raw, is_out, old_reason = rows[idx]
                    old_reason = (old_reason or "").strip()
                    if old_reason in SPIKE_REASONS:
                        continue
                    # 保留 synthetic/simulated 标记族；真实观测标 simulated_wiggle
                    if old_reason in ALREADY_SIMULATED or old_reason in SYNTHETIC_FLAT_REASONS:
                        new_reason = old_reason if old_reason in ALREADY_SIMULATED else "simulated_wiggle"
                    elif old_reason in ("interpolated", "forward_fill", "trailing_fill", "back_fill"):
                        new_reason = "simulated_wiggle"
                    elif old_reason:
                        new_reason = old_reason  # 保守：有其他 reason 只改价
                    else:
                        new_reason = "simulated_wiggle"
                    updates.append((path[k], None if new_reason.startswith("simulated_") else raw, new_reason, skin_id, date))
                stats["flat"] += m
        run_start = run_end

    if updates:
        conn.executemany(
            """UPDATE price_history
               SET price=?, raw_price=?, outlier_reason=?, is_outlier=0
               WHERE skin_id=? AND date=?""",
            updates,
        )

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate mild price wiggles for flat/gap fills")
    parser.add_argument("--db", type=Path, default=None, help="sqlite 路径；默认 runtime")
    parser.add_argument("--min-days", type=int, default=61)
    parser.add_argument("--min-flat-run", type=int, default=MIN_FLAT_RUN)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    owns = args.db is not None
    conn = _connect(args.db)
    try:
        skin_ids = [
            r[0]
            for r in conn.execute(
                """SELECT skin_id FROM price_history
                   GROUP BY skin_id HAVING COUNT(DISTINCT date) >= ?""",
                (args.min_days,),
            ).fetchall()
        ]
        print(
            f"[wiggle] db={args.db or 'runtime'} | skins={len(skin_ids)} | "
            f"min_flat_run={args.min_flat_run}"
        )
        total_t = total_f = 0
        touched = 0
        for i, sid in enumerate(skin_ids, 1):
            st = wiggle_skin(conn, sid, args.min_flat_run)
            if st["trailing"] or st["flat"]:
                touched += 1
            total_t += st["trailing"]
            total_f += st["flat"]
            if i % 50 == 0 or i == len(skin_ids):
                print(
                    f"[{i}/{len(skin_ids)}] touched={touched} "
                    f"trailing_rows={total_t} flat_rows={total_f}"
                )
        if args.dry_run:
            conn.rollback()
            print("[wiggle] dry-run → rollback")
        else:
            conn.commit()
            print(f"[wiggle] done: trailing={total_t} flat={total_f} skins={touched}")

        # 抽查：尾部 7 日仍全平的数量
        still = conn.execute(
            """
            WITH recent AS (
              SELECT skin_id, price,
                     ROW_NUMBER() OVER (PARTITION BY skin_id ORDER BY date DESC) rn
              FROM price_history
            )
            SELECT COUNT(*) FROM (
              SELECT skin_id FROM recent WHERE rn<=7
              GROUP BY skin_id
              HAVING COUNT(*)=7 AND COUNT(DISTINCT ROUND(price,4))=1
            )
            """
        ).fetchone()[0]
        print(f"[wiggle] skins with exact-flat last 7d: {still}")
    finally:
        if owns:
            conn.close()


if __name__ == "__main__":
    main()
