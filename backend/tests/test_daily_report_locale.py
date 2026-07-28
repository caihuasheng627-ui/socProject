"""Locale-aware daily market summary helpers."""

from scheduler import (
    detect_summary_locale,
    rule_based_market_summary,
    summary_invents_current_events,
    summary_locale_mismatch,
    summary_metrics_mismatch,
)


def test_detect_summary_locale_zh_vs_en():
    zh = "今日市场整体偏弱，上涨少于下跌，请关注流动性风险与赛事日历。"
    en = "Market breadth looks soft; size risk carefully around tournament calendars."
    assert detect_summary_locale(zh) == "zh-CN"
    assert detect_summary_locale(en) == "en-US"


def test_summary_locale_mismatch():
    zh = "今日市场整体偏弱，上涨少于下跌，请关注流动性风险与赛事日历。"
    en = "Market breadth looks soft; size risk carefully around tournament calendars."
    assert summary_locale_mismatch(zh, "en-US") is True
    assert summary_locale_mismatch(zh, "zh-CN") is False
    assert summary_locale_mismatch(en, "zh-CN") is True
    assert summary_locale_mismatch(en, "en-US") is False
    assert summary_locale_mismatch("", "zh-CN") is True


def test_rule_based_market_summary_bilingual():
    metrics = {"monitored": 100, "gainers": 40, "losers": 60}
    zh = rule_based_market_summary(metrics, locale="zh-CN")
    en = rule_based_market_summary(metrics, locale="en-US")
    assert "饰品市场" in zh or "监控样本" in zh
    assert "不构成投资建议" in zh
    assert "CS2 skin market brief" in en
    assert "not investment advice" in en
    assert detect_summary_locale(zh) == "zh-CN"
    assert detect_summary_locale(en) == "en-US"
    assert summary_metrics_mismatch(zh, metrics) is False
    assert summary_metrics_mismatch(en, metrics) is False
    # Stale prose with old numbers must be detected
    stale = "监控样本 681 件。上涨 304 件、下跌 358 件。"
    assert summary_metrics_mismatch(stale, metrics) is True
    assert summary_invents_current_events(zh) is False
    assert summary_invents_current_events(en) is False


def test_summary_invents_current_events():
    bad_zh = (
        "市场宽度分化。Valve 近期更新影响武器贴图，Major 相关成交量通常赛后冲高。"
    )
    bad_en = (
        "Mixed breadth. Valve's recent updates affecting weapon textures; "
        "Major-related volume spikes typically occur post-event."
    )
    ok = "监控样本 680 件。上涨 235 件、下跌 260 件。不构成投资建议。"
    assert summary_invents_current_events(bad_zh) is True
    assert summary_invents_current_events(bad_en) is True
    assert summary_invents_current_events(ok) is False
