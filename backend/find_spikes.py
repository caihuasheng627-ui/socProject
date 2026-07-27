"""
找所有"快速上升立即回落"的价格尖刺并标记为异常。
逻辑：偏移周围±10天中位数 > 阈值，且前后点都在正常范围。
阈值：普通 1.8x，特殊图案/低价 3.0x（与 price_cleaning 对齐）。
"""
import sqlite3, statistics, sys

DB = "data/skinvision.db"

SPECIAL_PATTERNS = [
    "Case Hardened", "Doppler", "Fade", "Marble Fade",
    "Gamma Doppler", "Heat Treated", "Crimson Web",
    "Slaughter", "Ultraviolet", "Night", "Lore",
]

def is_special(name):
    return any(p.lower() in name.lower() for p in SPECIAL_PATTERNS)

def find_spikes(prices, dates, threshold):
    """prices/dates 已按日期排序。返回 [(date, price, reason), ...]"""
    spikes = []
    n = len(prices)
    for i in range(n):
        price = prices[i]
        if price <= 0:
            continue
        # 取±3点窗口（排除自身），更准确地捕捉局部尖刺
        window = []
        for j in range(max(0, i - 3), min(n, i + 4)):
            if j != i:
                window.append(prices[j])
        if len(window) < 3:
            continue
        median = statistics.median(window)
        if median <= 0:
            continue

        ratio = price / median
        is_spike = False
        direction = ""

        if ratio > threshold:
            # 高价尖刺：检查前后2个点是否都回落到正常范围
            nearby = prices[max(0, i-2):i] + prices[i+1:min(n, i+3)]
            if nearby and all(p < median * 1.3 for p in nearby):
                is_spike = True
                direction = "up"
        elif ratio < 1.0 / threshold:
            # 低价尖刺
            nearby = prices[max(0, i-2):i] + prices[i+1:min(n, i+3)]
            if nearby and all(p > median * 0.5 for p in nearby):
                is_spike = True
                direction = "down"

        if is_spike:
            spikes.append((dates[i], price, f"{direction}_spike ({ratio:.1f}x median)"))

    return spikes


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    skins = conn.execute("SELECT id, market_hash_name FROM skins").fetchall()
    total_found = 0
    updated = 0

    for skin in skins:
        rows = conn.execute(
            "SELECT date, price, raw_price, is_outlier FROM price_history WHERE skin_id=? ORDER BY date",
            (skin["id"],)
        ).fetchall()

        if len(rows) < 10:
            continue

        dates = [r["date"] for r in rows]
        prices = [r["price"] for r in rows]

        avg_price = statistics.median(prices)
        if is_special(skin["market_hash_name"]) or avg_price < 1.0:
            threshold = 3.0
        else:
            threshold = 1.8

        spikes = find_spikes(prices, dates, threshold)
        for date, spike_price, reason in spikes:
            # 跳过已经标记的
            row = next((r for r in rows if r["date"] == date), None)
            if row and row["is_outlier"]:
                continue
            total_found += 1
            conn.execute(
                """UPDATE price_history SET is_outlier=1, outlier_reason=?
                   WHERE skin_id=? AND date=?""",
                (reason, skin["id"], date),
            )
            updated += 1

    conn.commit()
    # 统计
    new_count = conn.execute("SELECT COUNT(*) FROM price_history WHERE is_outlier=1").fetchone()[0]
    reasons = conn.execute(
        "SELECT outlier_reason, COUNT(*) FROM price_history WHERE is_outlier=1 GROUP BY outlier_reason"
    ).fetchall()
    print(f"新发现尖刺: {total_found}, 成功更新: {updated}")
    print(f"总异常数: {new_count}")
    for r in reasons:
        print(f"  {r[0]}: {r[1]}")

    # 展示几个典型案例
    print("\n--- 新标记案例 ---")
    samples = conn.execute("""
        SELECT s.market_hash_name, p.date, p.price, p.raw_price, p.outlier_reason
        FROM price_history p JOIN skins s ON s.id=p.skin_id
        WHERE p.is_outlier=1 AND p.outlier_reason LIKE '%spike%'
        ORDER BY p.date DESC LIMIT 15
    """).fetchall()
    for s in samples:
        print(f"  {s['market_hash_name'][:45]} | {s['date']} | ${s['price']:.2f} | {s['outlier_reason']}")

    conn.close()

if __name__ == "__main__":
    main()
