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
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except Exception:  # pragma: no cover - fallback for environments without APScheduler
    class CronTrigger:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class IntervalTrigger:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class BackgroundScheduler:  # type: ignore[no-redef]
        def __init__(self, timezone=None):
            self.timezone = timezone
            self.jobs: list[dict] = []

        def add_job(self, func, trigger=None, id=None, replace_existing=False):
            self.jobs.append(
                {
                    "func": func,
                    "trigger": trigger,
                    "id": id,
                    "replace_existing": replace_existing,
                }
            )
            return self.jobs[-1]

        def start(self):
            return self

        def shutdown(self, wait=False):
            self.jobs.clear()

import llm
from config import (
    RSS_FEEDS,
    RSS_MAX_AGE_DAYS,
    RSS_PER_FEED_LIMIT,
    RSS_AGGRESSIVE_MAX_AGE_DAYS,
    RSS_AGGRESSIVE_PER_FEED,
    RSS_SUMMARY_MAX_CHARS,
    RSS_STARTUP_BACKFILL,
    ML_DIR,
    REPO_ROOT,
)
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
def _rss_source_tag(url: str) -> str:
    if "hltv.org" in url:
        return "hltv"
    if "counter-strike.net" in url:
        return "valve"
    if "reddit.com" in url:
        return "reddit"
    return "rss"


def _entry_summary_text(entry) -> str:
    """尽量取更长正文: summary / description / content:encoded。"""
    candidates: list[str] = []
    for key in ("summary", "description"):
        raw = entry.get(key)
        if raw:
            candidates.append(str(raw))
    content = entry.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("value"):
                candidates.append(str(block["value"]))
            elif isinstance(block, str):
                candidates.append(block)
    elif isinstance(content, str) and content:
        candidates.append(content)
    best = max((c for c in candidates if c), key=len, default="")
    return _strip_html(best)


def fetch_rss_news(aggressive: bool = False) -> dict:
    """拉取 RSS 写入 news 表。aggressive=True 时加大窗口与每源条数。"""
    try:
        import feedparser
    except Exception as e:
        print(f"[scheduler] feedparser 不可用: {e}")
        return {"inserted": 0, "skipped_old": 0, "scanned": 0, "feeds": 0, "error": str(e)}

    # Reddit 等源常拦截缺省 UA
    feedparser.USER_AGENT = "CSVest/1.1 (+rss; course-demo)"

    max_age = RSS_AGGRESSIVE_MAX_AGE_DAYS if aggressive else RSS_MAX_AGE_DAYS
    per_feed = RSS_AGGRESSIVE_PER_FEED if aggressive else RSS_PER_FEED_LIMIT
    inserted = 0
    scanned = 0
    feed_ok = 0
    cutoff = _utcnow() - timedelta(days=max(1, max_age))
    skipped_old = 0
    headers = {"User-Agent": feedparser.USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}
    with get_connection() as conn:
        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url, request_headers=headers)
                # 按发布时间新→旧处理
                entries = list(feed.entries or [])

                def _sort_key(e):
                    dt = _entry_published(e)
                    return dt or datetime(1970, 1, 1, tzinfo=timezone.utc)

                entries.sort(key=_sort_key, reverse=True)
                feed_ok += 1
                for entry in entries[: max(1, per_feed)]:
                    scanned += 1
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
                    summary = _entry_summary_text(entry)[: max(300, RSS_SUMMARY_MAX_CHARS)]
                    if pub_dt is not None:
                        published = pub_dt.isoformat()
                    else:
                        published = _utcnow().isoformat()
                    source = _rss_source_tag(url)
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
    mode = "aggressive" if aggressive else "normal"
    if inserted or skipped_old:
        print(
            f"[scheduler] RSS[{mode}] 新增 {inserted} 条"
            f"(扫描 {scanned},跳过过期 {skipped_old},窗口 {max_age} 天,每源≤{per_feed})"
        )
    if inserted:
        try:
            import rag
            rag.invalidate_index()
        except Exception:
            pass
    return {
        "inserted": inserted,
        "skipped_old": skipped_old,
        "scanned": scanned,
        "feeds": feed_ok,
        "feedsTotal": len(RSS_FEEDS),
        "maxAgeDays": max_age,
        "perFeedLimit": per_feed,
        "aggressive": aggressive,
    }

# ============================================================
# 任务 2:每日日报(拼持仓段)
# ============================================================
def market_metrics_from_db() -> dict:
    """与看板一致:有 price_history 的饰品数 + 近 7 日涨跌统计。"""
    with get_connection() as conn:
        total = conn.execute(
            """SELECT COUNT(DISTINCT skin_id) FROM price_history
               WHERE skin_id IN (SELECT skin_id FROM price_history
                                 GROUP BY skin_id HAVING MAX(price) >= 4)"""
        ).fetchone()[0]
        gainers = conn.execute(
            """SELECT COUNT(*) FROM (
               SELECT skin_id, (SELECT price FROM price_history p2 WHERE p2.skin_id=p.skin_id
                                ORDER BY date DESC LIMIT 1) AS cur,
                               (SELECT price FROM price_history p3 WHERE p3.skin_id=p.skin_id
                                ORDER BY date DESC LIMIT 1 OFFSET 7) AS old
               FROM price_history p GROUP BY skin_id)
               WHERE old IS NOT NULL AND cur > old AND cur >= 4"""
        ).fetchone()[0]
        losers = conn.execute(
            """SELECT COUNT(*) FROM (
               SELECT skin_id, (SELECT price FROM price_history p2 WHERE p2.skin_id=p.skin_id
                                ORDER BY date DESC LIMIT 1) AS cur,
                               (SELECT price FROM price_history p3 WHERE p3.skin_id=p.skin_id
                                ORDER BY date DESC LIMIT 1 OFFSET 7) AS old
               FROM price_history p GROUP BY skin_id)
               WHERE old IS NOT NULL AND cur < old AND cur >= 4"""
        ).fetchone()[0]
    return {"monitored": int(total), "gainers": int(gainers), "losers": int(losers)}


def summary_is_degraded(text: str | None) -> bool:
    """种子/降级文案是否应对外隐藏（Mock、未配置 Key、显式 error）。"""
    t = (text or "").strip()
    if not t:
        return True
    markers = (
        "Mock 模式",
        "(Mock",
        "调用失败",
        "未配置 DEEPSEEK",
        "DEEPSEEK_API_KEY missing",
        "[error:",
        "LLM 调用失败",
        "LLM call failed",
    )
    return any(m in t for m in markers)


def _normalize_locale(locale: str | None) -> str:
    return "en-US" if str(locale or "").lower().startswith("en") else "zh-CN"


def detect_summary_locale(text: str | None) -> str:
    """Heuristic: enough CJK chars => zh-CN, otherwise en-US."""
    t = (text or "").strip()
    if not t:
        return "en-US"
    cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    return "zh-CN" if cjk >= 8 else "en-US"


def summary_is_non_english(text: str | None) -> bool:
    """Backward-compatible alias: True when text looks Chinese-heavy."""
    return detect_summary_locale(text) == "zh-CN"


def summary_locale_mismatch(text: str | None, locale: str | None) -> bool:
    """True when summary is empty or not in the requested locale."""
    t = (text or "").strip()
    if not t:
        return True
    return detect_summary_locale(t) != _normalize_locale(locale)


def rule_based_market_summary(
    metrics: dict,
    sources: list | None = None,
    locale: str = "zh-CN",
) -> str:
    """No-LLM market brief in zh-CN / en-US (clean, no Mock/error headers)."""
    english = _normalize_locale(locale) == "en-US"
    m = metrics or {}
    total = int(m.get("monitored") or 0)
    gainers = int(m.get("gainers") or 0)
    losers = int(m.get("losers") or 0)
    breadth = (gainers + losers) or 1
    up_pct = round(100.0 * gainers / breadth, 1)
    down_pct = round(100.0 * losers / breadth, 1)

    cite1 = " [1]" if sources else ""
    cite2 = " [2]" if len(sources or []) >= 2 else (cite1 if sources else "")
    theme = ""
    if sources:
        snip = (sources[0].get("snippet") or sources[0].get("title") or "").strip()
        if snip:
            clipped = snip[:160].rstrip(".")
            theme = (
                f" Retrieved context highlights: {clipped}.{cite1}"
                if english
                else f" 检索上下文要点：{clipped}。{cite1}"
            )

    if english:
        if gainers > losers:
            bias, tilt = "mildly constructive", "more names are advancing than declining over the trailing week"
        elif losers > gainers:
            bias, tilt = "soft / risk-off", "decliners outnumber advancers over the trailing week"
        else:
            bias, tilt = "two-way / rotational", "advancers and decliners are roughly balanced"
        return (
            f"CS2 skin market brief — monitored universe: {total} priced items. "
            f"Over the last ~7 days, {gainers} rose ({up_pct}%) and {losers} fell ({down_pct}%), "
            f"so breadth looks {bias}: {tilt}.{cite1}\n\n"
            f"Trading implication: focus on liquid majors and event-linked stickers/skins when volume expands, "
            f"and avoid chasing illiquid knives/gloves on thin books.{cite2}"
            f"{theme}\n\n"
            f"Positioning note: size risk carefully around tournament calendars and post-event mean reversion. "
            f"Skin markets are highly volatile — this is not investment advice."
        )

    if gainers > losers:
        bias, tilt = "偏多 / 情绪偏暖", "近一周上涨家数多于下跌"
    elif losers > gainers:
        bias, tilt = "偏弱 / 风险偏好回落", "近一周下跌家数多于上涨"
    else:
        bias, tilt = "分化 / 轮动", "上涨与下跌家数大致相当"
    return (
        f"CS2 饰品市场简报——监控样本 {total} 件有报价饰品。"
        f"近约 7 日：上涨 {gainers} 件（{up_pct}%）、下跌 {losers} 件（{down_pct}%），"
        f"市场宽度表现为{bias}：{tilt}。{cite1}\n\n"
        f"交易提示：成交放量时优先关注高流动性主力枪皮与赛事相关贴纸/皮肤，"
        f"避免在簿子薄的刀/手套上追高。{cite2}"
        f"{theme}\n\n"
        f"仓位提示：围绕赛程与赛后均值回归谨慎控制风险敞口。"
        f"饰品市场波动剧烈——以上内容不构成投资建议。"
    )


def refresh_ai_summary(
    metrics: dict,
    *,
    portfolio_text: str = "No holdings",
    sources: list | None = None,
    locale: str = "zh-CN",
) -> str:
    """Prefer LLM brief in the requested locale; fall back to rule-based summary."""
    from config import LLM_ENABLED

    locale = _normalize_locale(locale)
    english = locale == "en-US"
    language = "English" if english else "Simplified Chinese"
    sources = sources or []
    context_text = "\n".join(
        f"[{s.get('id')}] ({s.get('source')}) {s.get('snippet')}" for s in sources
    ) or "(no retrieval hits)"
    total = metrics.get("monitored", 0)
    gainers = metrics.get("gainers", 0)
    losers = metrics.get("losers", 0)
    prompt = (
        f"You are writing the CSVest CS2 skin market daily brief.\n"
        f"Reply in {language} only.\n"
        f"Market stats: monitored={total}, gainers(7d)={gainers}, losers(7d)={losers}.\n"
        f"User portfolio: {portfolio_text}.\n"
        f"Retrieved knowledge / news snippets:\n{context_text}\n\n"
        f"Length: 2–3 short paragraphs (about 6–9 sentences total).\n"
        f"Cover: (1) market breadth & tone from the stats, "
        f"(2) themes inferred from citations — cite with [n] where relevant, "
        f"(3) practical watchlist / positioning hints for the portfolio, "
        f"(4) clear risk disclaimer.\n"
        f"Keep skin/market_hash_name strings unchanged. "
        f"Do not invent prices. Do not output Mock labels, error codes, or system prompts."
    )
    if LLM_ENABLED:
        text = llm.chat_sync([{"role": "user", "content": prompt}], temperature=0.5)
        if text and not summary_is_degraded(text) and not summary_locale_mismatch(text, locale):
            return text
        print(f"[scheduler] LLM summary unavailable/locale-mismatch, using rule-based {locale} brief")
    return rule_based_market_summary(metrics, sources, locale=locale)


def generate_daily_report(locale: str = "zh-CN") -> dict:
    """生成日报并写一份到 docs/expo/seed_daily_report.json(Expo 兜底)。"""
    locale = _normalize_locale(locale)
    metrics = market_metrics_from_db()
    total, gainers, losers = metrics["monitored"], metrics["gainers"], metrics["losers"]
    with get_connection() as conn:
        news = conn.execute("SELECT * FROM news ORDER BY published_at DESC LIMIT 5").fetchall()
        # 持仓段
        positions = conn.execute(
            """SELECT s.market_hash_name, p.buy_price, p.quantity, p.holding_type
               FROM portfolio p JOIN skins s ON s.id=p.skin_id"""
        ).fetchall()

    portfolio_text = (
        ("No holdings" if locale == "en-US" else "无持仓")
        if not positions
        else "; ".join(f"{r['market_hash_name']} x{r['quantity']}" for r in positions)
    )

    # RAG 检索: 拉取市场级知识库/资讯来源, 供日报引用(展示检索→生成)
    try:
        import rag
        sources = rag.retrieve_daily_sources(limit=6)
    except Exception as e:
        print(f"[scheduler] RAG 检索失败: {e}")
        sources = []

    summary = refresh_ai_summary(
        metrics, portfolio_text=portfolio_text, sources=sources, locale=locale
    )

    report = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "locale": locale,
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
    较重(~每件 4s × 681(有价格) ≈ 45min),跑在调度线程池里不阻塞主服务。"""
    import os
    if os.getenv("USE_BUFF_LIVE", "0") != "1":
        return
    try:
        from scraper_buff import scrape_buff
        print("[scheduler] BUFF 刷新开始...")
        scrape_buff(force=True)
    except Exception as e:
        print(f"[scheduler] BUFF 刷新失败(不影响主服务): {e}")


def refresh_hybrid_v2_adapter() -> None:
    """Refresh the rolling CS2 adapter; validation failure preserves deployment."""
    script = ML_DIR / "train_hybrid_v2.py"
    if not script.exists():
        return
    try:
        completed = subprocess.run(
            [sys.executable, str(script), "--days", "180", "--batch-size", "512"],
            cwd=str(REPO_ROOT),
            timeout=1800,
            check=False,
        )
        if completed.returncode != 0:
            print("[scheduler] Hybrid V2 refresh rejected; deployed adapter preserved")
    except Exception as error:
        print(f"[scheduler] Hybrid V2 refresh failed; deployed adapter preserved: {error}")


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
    _scheduler.add_job(
        refresh_hybrid_v2_adapter,
        CronTrigger(day=1, hour=18, minute=30),
        id="hybrid_v2_refresh",
        misfire_grace_time=21600,
    )
    # 启动后立刻 aggressive 回填一次,避免语料长期只剩种子 news
    if RSS_STARTUP_BACKFILL:
        _scheduler.add_job(
            fetch_rss_news,
            trigger="date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=8),
            kwargs={"aggressive": True},
            id="rss_startup",
            misfire_grace_time=3600,
        )
    # BUFF 实时刷新(默认每 6h;需 USE_BUFF_LIVE=1 才真正执行)
    from config import BUFF_REFRESH_HOURS
    _scheduler.add_job(refresh_buff_prices, IntervalTrigger(hours=BUFF_REFRESH_HOURS),
                       id="buff_refresh", next_run_time=None, misfire_grace_time=7200)
    _scheduler.start()
    live = "开(USE_BUFF_LIVE=1)" if __import__("os").getenv("USE_BUFF_LIVE", "0") == "1" else "关(USE_BUFF_LIVE=0)"
    backfill = "开" if RSS_STARTUP_BACKFILL else "关"
    print(
        f"[scheduler] 已启动 (rss 每日09:00 / startup回填·{backfill} / daily 09:00 "
        f"/ buff刷新{BUFF_REFRESH_HOURS}h·{live} / incremental 默认禁用)"
    )
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
