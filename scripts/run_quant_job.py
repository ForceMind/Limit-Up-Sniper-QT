#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load_env_file() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def _payload(args: argparse.Namespace) -> Dict[str, Any]:
    if not args.payload_json:
        return {}
    try:
        loaded = json.loads(args.payload_json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid payload json: {exc}") from exc
    if not isinstance(loaded, dict):
        return {}
    payload = loaded.get("payload")
    return payload if isinstance(payload, dict) else loaded


def _run(job: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from app.quant.jobs import job_manager

    if job == "strategy_replay":
        return job_manager.run_strategy_replay(
            start_date=payload.get("requested_start_date") or payload.get("start_date"),
            end_date=payload.get("requested_end_date") or payload.get("end_date"),
            mode=str(payload.get("mode") or "intraday"),
            background=False,
            batch_days=payload.get("batch_days"),
            use_cursor=bool(payload.get("cursor_enabled")),
            process=False,
        )
    if job == "strategy_evolution":
        return job_manager.run_strategy_evolution(
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            mode=str(payload.get("mode") or "intraday"),
            generations=payload.get("generations"),
            population_size=payload.get("population_size"),
            apply_best=payload.get("apply_best"),
            process=False,
        )
    raise SystemExit(f"unsupported job: {job}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a quant job in an isolated process.")
    parser.add_argument("--job", required=True, choices=["strategy_replay", "strategy_evolution"])
    parser.add_argument("--payload-json", default="")
    args = parser.parse_args()
    _load_env_file()
    result = _run(args.job, _payload(args))
    status = str(result.get("status") if isinstance(result, dict) else "")
    return 0 if status in {"ok", "running", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
