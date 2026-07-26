import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
sys.modules.pop("config", None)

import scheduler


def test_monthly_refresh_uses_active_python_and_preserves_model_on_failure(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Completed", (), {"returncode": 1})()

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    scheduler.refresh_hybrid_v2_adapter()

    command, kwargs = calls[0]
    assert command[0] == sys.executable
    assert command[1].endswith("train_hybrid_v2.py")
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 1800


def test_scheduler_registers_monthly_hybrid_refresh(monkeypatch):
    jobs = []

    class FakeScheduler:
        def __init__(self, **kwargs):
            pass

        def add_job(self, func, trigger, **kwargs):
            jobs.append((func, trigger, kwargs))

        def start(self):
            pass

    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    scheduler.start_scheduler()

    hybrid = [job for job in jobs if job[2]["id"] == "hybrid_v2_refresh"]
    assert len(hybrid) == 1
    assert hybrid[0][0] is scheduler.refresh_hybrid_v2_adapter

    startup = [job for job in jobs if job[2]["id"] == "rss_startup"]
    if scheduler.RSS_STARTUP_BACKFILL:
        assert len(startup) == 1
        assert startup[0][0] is scheduler.fetch_rss_news
        assert startup[0][2].get("kwargs", {}).get("aggressive") is True
