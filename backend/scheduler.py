"""
SkinVision AI — 定时任务(组员 3 第 4 步)
=========================================
APScheduler 后台任务(均 try/except,不崩主进程):
  1. RSS 资讯采集     → news 表增量追加(每日 UTC 01:00 ≈ 北京 09:00)
  2. 每日 AI 市场日报 → 生成 aiSummary + 拼持仓段(每日 09:00)
  3. 增量训练触发     → 调组员 2 的 --mode incremental 脚本(每日 02:00,默认禁用)

🆕 方案 B:日报 prompt 多拼一段持仓摘要。
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import llm
from config import RSS_FEEDS, RSS_MAX_AGE_DAYS, ML_DIR, REPO_ROOT
from database import get_connection, _utcnow

_scheduler: BackgroundScheduler | None = None
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub(" ", unescape(text or "")).strip()


def _entry_published(entry) -> datetime | None:
    """解析 RSS published/updated 为 aware UTC datetime。"""
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for key in ("published", "updated"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None


# ============================================================
# 任务 1:RSS 采集
# ============================================================
def fetch_rss_news() -> int:
    try:
        import feedparser
    except Exception as e:
        print(f"[scheduler] feedparser 不可用: {e}")
        return 0
    inserted = 0
    cutoff = _utcnow() - timedelta(days=max(1, RSS_MAX_AGE_DAYS))
    skipped_old = 0
    with get_connection() as conn:
        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                # 按发布时间新→旧处理,每源最多看 20 条
                entries = list(feed.entries or [])

                def _sort_key(e):
                    dt = _entry_published(e)
                    return dt or datetime(1970, 1, 1, tzinfo=timezone.utc)

                entries.sort(key=_sort_key, reverse=True)
                for entry in entries[:20]:
                    title = (entry.get("title") or "").strip()
                    if not title:
                        continue
                    pub_dt = _entry_published(entry)
                    if pub_dt is not None and pub_dt < cutoff:
                        skipped_old += 1
                        continue
                    exists = conn.execute(
                        "SELECT 1 FROM news WHERE title=? LIMIT 1", (title,)
                    ).fetchone()
                    if exists:
                        continue
                    summary = _strip_html(entry.get("summary", "") or "")[:300]
                    if pub_dt is not None:
                        published = pub_dt.isoformat()
                    else:
                        published = _utcnow().isoformat()
                    source = "hltv" if "hltv.org" in url else (
                        "valve" if "counter-strike.net" in url else "rss"
                    )
                    conn.execute(
                        """INSERT INTO news(title, summary, source, url, published_at, sentiment, impact, related_skins)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (title, summary, source, entry.get("link", "") or "",
                         published, "neutral", "low", ""),
                    )
                    inserted += 1
            except Exception as e:
                print(f"[scheduler] RSS 采集失败 {url}: {e}")
        conn.commit()
    if inserted or skipped_old:
        print(f"[scheduler] RSS 新增 {inserted} 条(跳过过期 {skipped_old},窗口 {RSS_MAX_AGE_DAYS} 天)")
    if inserted:
        try:
            import rag
            rag.invalidate_index()
        except Exception:
            pass
    return inserted


# ============================================================
# 任务 2:每日日报(拼持仓段)
# ============================================================
def market_metrics_from_db() -> dict:
    """与看板一致:有 price_history 的饰品数 + 近 7 日涨跌统计。"""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(DISTINCT skin_id) FROM price_history").fetchone()[0]
        gainers = conn.execute(
            """SELECT COUNT(*) FROM (
               SELECT skin_id, (SELECT price FROM price_history p2 WHERE p2.skin_id=p.skin_id
                                ORDER BY date DESC LIMIT 1) AS cur,
                               (SELECT price FROM price_history p3 WHERE p3.skin_id=p.skin_id
                                ORDER BY date DESC LIMIT 1 OFFSET 7) AS old
               FROM price_history p GROUP BY skin_id)
               WHERE old IS NOT NULL AND cur > old"""
        ).fetchone()[0]
        losers = conn.execute(
            """SELECT COUNT(*) FROM (
               SELECT skin_id, (SELECT price FROM price_history p2 WHERE p2.skin_id=p.skin_id
                                ORDER BY date DESC LIMIT 1) AS cur,
                               (SELECT price FROM price_history p3 WHERE p3.skin_id=p.skin_id
                                ORDER BY date DESC LIMIT 1 OFFSET 7) AS old
               FROM price_history p GROUP BY skin_id)
               WHERE old IS NOT NULL AND cur < old"""
        ).fetchone()[0]
    return {"monitored": int(total), "gainers": int(gainers), "losers": int(losers)}


def generate_daily_report() -> dict:
    """生成日报并写一份到 docs/expo/seed_daily_report.json(Expo 兜底)。"""
    metrics = market_metrics_from_db()
    total, gainers, losers = metrics["monitored"], metrics["gainers"], metrics["losers"]
    with get_connection() as conn:
        news = conn.execute("SELECT * FROM news ORDER BY published_at DESC LIMIT 5").fetchall()
        # 持仓段
        positions = conn.execute(
            """SELECT s.market_hash_name, p.buy_price, p.quantity, p.holding_type
               FROM portfolio p JOIN skins s ON s.id=p.skin_id"""
        ).fetchall()

    portfolio_text = "无持仓" if not positions else "; ".join(
        f"{r['market_hash_name']} {r['quantity']}件" for r in positions
    )

    # RAG 检索: 拉取市场级知识库/资讯来源, 供日报引用(展示检索→生成)
    try:
        import rag
        sources = rag.retrieve_daily_sources(limit=6)
    except Exception as e:
        print(f"[scheduler] RAG 检索失败: {e}")
        sources = []
    context_text = "\n".join(
        f"[{s['id']}] ({s['source']}) {s['snippet']}" for s in sources
    ) or "(无检索结果)"

    prompt = (
        f"今日 CS2 饰品市场:监控 {total} 件,上涨 {gainers} 件,下跌 {losers} 件。"
        f"你的持仓:{portfolio_text}。\n"
        f"以下是检索到的市场知识库/资讯:\n{context_text}\n\n"
        f"请据此生成一段中文市场日报(3-4 句),在相关处用 [编号] 标注引用来源,"
        f"含持仓提示与风险。"
    )
    summary = llm.chat_sync([{"role": "user", "content": prompt}], temperature=0.5)

    report = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": metrics,
        "portfolio": [{"name": r["market_hash_name"], "quantity": r["quantity"],
                       "holdingType": r["holding_type"]} for r in positions],
        "aiSummary": summary,
        "sources": sources,
        "news": [{"title": n["title"], "summary": n["summary"], "source": n["source"],
                  "sentiment": n["sentiment"]} for n in news],
    }

    # 写 Expo 兜底
    try:
        from config import SEED_DIR
        SEED_DIR.mkdir(parents=True, exist_ok=True)
        (SEED_DIR / "seed_daily_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[scheduler] 日报写入失败: {e}")
    print(f"[scheduler] 日报已生成 (监控 {total}, 持仓 {len(positions)})")
    return report


# ============================================================
# 任务 3:增量训练触发(默认禁用)
# ============================================================
def trigger_incremental_training() -> None:
    """调组员 2 的树模型增量脚本(策划书 §方案 B 增量更新)。默认禁用,需设 env。"""
    import os
    if os.getenv("ENABLE_INCREMENTAL_TRAIN", "0") != "1":
        return
    scripts = ["make_predictions_trees.py", "02_xgboost_reg_cls.py"]
    for script in scripts:
        p = ML_DIR / script
        if not p.exists():
            continue
        try:
            print(f"[scheduler] 增量训练: {script}")
            subprocess.run(["python", str(p), "--mode", "incremental"],
                           cwd=str(REPO_ROOT), timeout=600, check=False)
        except Exception as e:
            print(f"[scheduler] 增量训练失败 {script}: {e}")


# ============================================================
# 任务 4:BUFF 实时刷新(滚动 180 天)
# ============================================================
def refresh_buff_prices() -> None:
    """定时重采 BUFF 价格(force=True,upsert 最新 + 删 >180d 旧数据)。
    较重(~每件 4s × 769 ≈ 50min),跑在调度线程池里不阻塞主服务。"""
    import os
    if os.getenv("USE_BUFF_LIVE", "0") != "1":
        return
    try:
        from scraper_buff import scrape_buff
        print("[scheduler] BUFF 刷新开始...")
        scrape_buff(force=True)
    except Exception as e:
        print(f"[scheduler] BUFF 刷新失败(不影响主服务): {e}")


# ============================================================
# 启动
# ============================================================
def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")

    _scheduler.add_job(fetch_rss_news, CronTrigger(hour=1, minute=0), id="rss",
                       misfire_grace_time=3600)  # UTC 01:00 ≈ 北京 09:00,每日一次
    _scheduler.add_job(generate_daily_report, CronTrigger(hour=1, minute=0), id="daily",
                       misfire_grace_time=3600)   # UTC 01:00 ≈ 北京 09:00
    _scheduler.add_job(trigger_incremental_training, CronTrigger(hour=18, minute=0), id="train",
                       misfire_grace_time=3600)
    # BUFF 实时刷新(默认每 6h;需 USE_BUFF_LIVE=1 才真正执行)
    from config import BUFF_REFRESH_HOURS
    _scheduler.add_job(refresh_buff_prices, IntervalTrigger(hours=BUFF_REFRESH_HOURS),
                       id="buff_refresh", next_run_time=None, misfire_grace_time=7200)
    _scheduler.start()
    live = "开(USE_BUFF_LIVE=1)" if __import__("os").getenv("USE_BUFF_LIVE", "0") == "1" else "关(USE_BUFF_LIVE=0)"
    print(f"[scheduler] 已启动 (rss 每日09:00 / daily 09:00 / buff刷新{BUFF_REFRESH_HOURS}h·{live} / incremental 默认禁用)")
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
