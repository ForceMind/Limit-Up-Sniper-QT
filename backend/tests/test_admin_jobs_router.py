from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.admin_jobs import build_admin_jobs_router


def test_admin_jobs_router_preserves_status_logs_and_control_contracts():
    calls = []

    def status_payload(light=True):
        calls.append({"endpoint": "status", "light": light})
        return {"status": "ok", "light": light}

    def logs_payload(limit=200, level=None, job=None):
        calls.append({"endpoint": "logs", "limit": limit, "level": level, "job": job})
        return {"status": "ok", "items": []}

    def scheduler_start_payload():
        calls.append({"endpoint": "scheduler_start"})
        return {"status": "ok", "scheduler": "started"}

    async def scheduler_stop_payload():
        calls.append({"endpoint": "scheduler_stop"})
        return {"status": "ok", "scheduler": "stopped"}

    def pause_payload(job_name):
        calls.append({"endpoint": "pause", "job_name": job_name})
        return {"status": "paused", "job": job_name}

    def resume_payload(job_name):
        calls.append({"endpoint": "resume", "job_name": job_name})
        return {"status": "running", "job": job_name}

    def stop_payload(job_name):
        calls.append({"endpoint": "stop", "job_name": job_name})
        return {"status": "stop_requested", "job": job_name}

    app = FastAPI()
    app.include_router(
        build_admin_jobs_router(
            status_payload=status_payload,
            logs_payload=logs_payload,
            scheduler_start_payload=scheduler_start_payload,
            scheduler_stop_payload=scheduler_stop_payload,
            pause_payload=pause_payload,
            resume_payload=resume_payload,
            stop_payload=stop_payload,
        )
    )
    client = TestClient(app)

    status_response = client.get("/api/jobs/status", params={"light": "false"})
    logs_response = client.get("/api/jobs/logs", params={"limit": 25, "level": "warning", "job": "trade_cycle"})
    runtime_logs_response = client.get(
        "/api/logs/runtime",
        params={"limit": 12, "level": "error", "job": "frontend_payload_precompute"},
    )
    scheduler_start_response = client.post("/api/jobs/scheduler/start")
    scheduler_stop_response = client.post("/api/jobs/scheduler/stop")
    pause_response = client.post("/api/jobs/trade_cycle/pause")
    resume_response = client.post("/api/jobs/trade_cycle/resume")
    stop_response = client.post("/api/jobs/trade_cycle/stop")

    assert status_response.status_code == 200
    assert logs_response.status_code == 200
    assert runtime_logs_response.status_code == 200
    assert scheduler_start_response.status_code == 200
    assert scheduler_stop_response.status_code == 200
    assert pause_response.status_code == 200
    assert resume_response.status_code == 200
    assert stop_response.status_code == 200
    assert calls == [
        {"endpoint": "status", "light": False},
        {"endpoint": "logs", "limit": 25, "level": "warning", "job": "trade_cycle"},
        {"endpoint": "logs", "limit": 12, "level": "error", "job": "frontend_payload_precompute"},
        {"endpoint": "scheduler_start"},
        {"endpoint": "scheduler_stop"},
        {"endpoint": "pause", "job_name": "trade_cycle"},
        {"endpoint": "resume", "job_name": "trade_cycle"},
        {"endpoint": "stop", "job_name": "trade_cycle"},
    ]
