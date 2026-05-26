from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Callable, Dict
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


TokenValidator = Callable[[str], Any]
Payload = Callable[[], Dict[str, Any]]
LogsPayload = Callable[[int], Dict[str, Any]]
LogKey = Callable[[Dict[str, Any]], str]
Fingerprint = Callable[[Any], str]


def log_key(item: Dict[str, Any]) -> str:
    return "|".join(
        str(item.get(key) or "")
        for key in ("ts", "job", "stage", "level", "message")
    )


def json_fingerprint(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(payload)


def build_admin_live_router(
    *,
    verify_admin_token: TokenValidator,
    jobs_payload: Payload,
    status_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    biying_payload: Payload,
    logs_payload: LogsPayload,
    log_key: LogKey,
    fingerprint: Fingerprint,
    interval_seconds: float = 3.0,
) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/admin/live")
    async def admin_live(websocket: WebSocket):
        await websocket.accept()
        try:
            auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=5)
            verify_admin_token(str(auth_message.get("token") or ""))
        except Exception:
            await websocket.close(code=1008)
            return

        sent_logs: set[str] = set()
        status_fp = ""
        jobs_fp = ""
        biying_fp = ""
        try:
            while True:
                jobs = jobs_payload()
                status = status_payload(jobs)
                biying = biying_payload()
                logs = logs_payload(120)
                logs_delta = []
                for item in reversed(logs.get("items", [])):
                    if not isinstance(item, dict):
                        continue
                    key = log_key(item)
                    if key in sent_logs:
                        continue
                    sent_logs.add(key)
                    logs_delta.append(item)
                if len(sent_logs) > 1000:
                    sent_logs = set(list(sent_logs)[-500:])

                message: Dict[str, Any] = {
                    "type": "live_delta",
                    "server_time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                }
                next_status_fp = fingerprint(status)
                next_jobs_fp = fingerprint(jobs)
                next_biying_fp = fingerprint(biying)
                if next_status_fp != status_fp:
                    message["status_payload"] = status
                    status_fp = next_status_fp
                if next_jobs_fp != jobs_fp:
                    message["jobs"] = jobs
                    jobs_fp = next_jobs_fp
                if next_biying_fp != biying_fp:
                    message["biying"] = biying
                    biying_fp = next_biying_fp
                if logs_delta:
                    message["logs_delta"] = logs_delta
                if len(message) > 2:
                    await websocket.send_json(message)
                await asyncio.sleep(interval_seconds)
        except WebSocketDisconnect:
            return

    return router
