import sys
import threading
import time
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant import jobs as jobs_module
from app.quant.jobs import QuantJobManager


def test_run_job_background_returns_before_worker_finishes(tmp_path, monkeypatch):
    manager = QuantJobManager()
    manager.state_file = tmp_path / "job_state.json"
    monkeypatch.setattr(jobs_module, "JOB_LOG_FILE", tmp_path / "runtime_logs.jsonl")
    monkeypatch.setattr(manager, "_post_job_maintenance", lambda name: {"status": "skipped"})

    started = threading.Event()
    release = threading.Event()

    def execute():
        started.set()
        assert release.wait(2)
        return {"status": "ok", "value": 7}

    result = manager.run_job_background(
        "unit_job",
        execute,
        payload={"source": "unit"},
        message="单元测试任务已转入后台运行",
    )

    assert result["status"] == "running"
    assert result["background"] is True
    assert started.wait(1)
    assert manager.is_running("unit_job") is True

    duplicate = manager.run_job_background("unit_job", lambda: {"status": "bad"})
    assert duplicate["status"] == "running"
    assert "重复请求" in duplicate["message"]

    release.set()
    deadline = time.time() + 2
    while time.time() < deadline and manager.is_running("unit_job"):
        time.sleep(0.02)

    assert manager.is_running("unit_job") is False
    state = manager._load_state()
    current = state["jobs"]["unit_job"]
    assert current["status"] == "ok"
    assert current["last_result"]["value"] == 7


def test_strategy_replay_window_advances_cursor_by_batch(tmp_path, monkeypatch):
    manager = QuantJobManager()
    manager.state_file = tmp_path / "job_state.json"
    monkeypatch.setenv("QT_STRATEGY_REPLAY_BATCH_DAYS", "15")

    start, end, cursor = manager._strategy_replay_window(
        "2026-03-01",
        "2026-05-21",
        "intraday",
        batch_days=None,
        use_cursor=True,
    )

    assert start == "2026-03-01"
    assert end == "2026-03-15"
    assert cursor["next_start_date"] == "2026-03-16"
    assert cursor["completed_range"] is False

    manager._advance_strategy_replay_cursor({"batch": cursor})
    start, end, cursor = manager._strategy_replay_window(
        "2026-03-01",
        "2026-05-21",
        "intraday",
        batch_days=None,
        use_cursor=True,
    )

    assert start == "2026-03-16"
    assert end == "2026-03-30"
    assert cursor["next_start_date"] == "2026-03-31"
