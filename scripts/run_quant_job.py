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


def _run_tracked_job(
    job_manager: Any,
    job: str,
    execute,
    *,
    payload: Dict[str, Any],
    prepare_message: str,
    running_message: str,
    finalizing_message: str,
) -> Dict[str, Any]:
    def tracked_execute() -> Dict[str, Any]:
        job_manager.update_progress(job, 8, prepare_message, {**payload, "stage": "prepare"})
        job_manager.update_progress(job, 35, running_message, {**payload, "stage": "running"})
        result = execute()
        job_manager.update_progress(job, 88, finalizing_message, {**payload, "stage": "finalizing"})
        return result

    return job_manager.run_job(job, tracked_execute, payload=payload)


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
    if job == "frontend_payload_precompute":
        return job_manager.run_frontend_payload_precompute(
            as_of=payload.get("as_of"),
            usernames=payload.get("usernames"),
            limit_users=payload.get("limit_users"),
            force=bool(payload.get("force")),
            background=False,
            process=False,
            lookback_days=payload.get("lookback_days") or 2,
            top_n=payload.get("top_n") or 30,
            limit_days=payload.get("limit_days") or 30,
            max_seconds=payload.get("max_seconds"),
        )
    if job == "frontend_account_precompute":
        from app import main as main_module

        account_payload = {
            "as_of": payload.get("as_of"),
            "usernames": payload.get("usernames"),
            "limit_users": payload.get("limit_users") or 50,
            "limit": payload.get("limit") or 160,
            "force": bool(payload.get("force")),
            "drain_queue": bool(payload.get("drain_queue")),
        }

        def execute() -> Dict[str, Any]:
            return main_module._precompute_frontend_accounts(**account_payload)

        return _run_tracked_job(
            job_manager,
            "frontend_account_precompute",
            execute,
            payload=account_payload,
            prepare_message="frontend account precompute parameters ready",
            running_message="frontend account precompute running",
            finalizing_message="frontend account precompute finalizing",
        )
    if job == "news_fetch":
        return job_manager.run_news_fetch(
            hours=payload.get("hours") or 12,
            pages=payload.get("pages") or 5,
            page_size=payload.get("page_size") or 20,
            refresh_events=bool(payload.get("refresh_events")),
            background=False,
            process=False,
        )
    if job == "ai_analysis":
        return job_manager.run_ai_analysis(
            as_of=payload.get("as_of"),
            max_items=payload.get("max_items") or 8,
            batch_size=payload.get("batch_size") or 4,
            background=False,
            process=False,
        )
    if job == "kline_fill":
        return job_manager.run_kline_fill(
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            max_codes=payload.get("max_codes") or 300,
            force=bool(payload.get("force")),
            background=False,
            process=False,
        )
    if job == "lhb_sync":
        return job_manager.run_lhb_sync(
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            max_stock_days=payload.get("max_stock_days") or 300,
            force=bool(payload.get("force")),
            refresh_events=bool(payload.get("refresh_events")),
            background=False,
            process=False,
        )
    if job == "market_sync":
        return job_manager.run_market_sync(
            date=payload.get("date"),
            source=str(payload.get("source") or "events"),
            max_codes=payload.get("max_codes") or 80,
            codes=payload.get("codes"),
            force=bool(payload.get("force")),
            include_latest=bool(payload.get("include_latest", True)),
            background=False,
            process=False,
        )
    if job == "trade_cycle":
        return job_manager.run_trade_cycle(
            date=payload.get("date"),
            notify=bool(payload.get("notify", True)),
            background=False,
            process=False,
        )
    if job == "strategy_daily_refresh":
        return job_manager.run_strategy_daily_refresh(
            date=payload.get("date") or payload.get("end_date"),
            mode=str(payload.get("mode") or "daily"),
            background=False,
            process=False,
        )
    if job == "system_startup":
        from app import main as main_module

        target_date = str(
            payload.get("end_date")
            or payload.get("date")
            or main_module.quant_engine.latest_event_date()
            or main_module.datetime.now(main_module.ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        ).strip()
        replay_start_date = str(payload.get("start_date") or main_module.quant_engine.first_data_date() or "2026-03-01").strip()
        startup_payload = {
            "target_date": target_date,
            "replay_start_date": replay_start_date,
            "news_hours": payload.get("news_hours") or 24,
            "news_pages": payload.get("news_pages") or 8,
            "ai_items": payload.get("ai_items") or 20,
            "market_codes": payload.get("market_codes") or 200,
            "notify": bool(payload.get("notify", True)),
            "run_strategy_replay": bool(payload.get("run_strategy_replay")),
        }

        def execute() -> Dict[str, Any]:
            return main_module._run_system_startup_flow(**startup_payload)

        return job_manager.run_job("system_startup", execute, payload=startup_payload)
    if job == "data_coverage":
        from app import main as main_module

        coverage_payload = {
            "effective_as_of": payload.get("as_of"),
            "top_n": payload.get("top_n") or 80,
        }

        def execute() -> Dict[str, Any]:
            result = main_module._compute_data_coverage_cached(**coverage_payload)
            return main_module._compact_data_coverage_result(
                result if isinstance(result, dict) else {},
                int(coverage_payload["top_n"] or 80),
            )

        return _run_tracked_job(
            job_manager,
            "data_coverage",
            execute,
            payload=payload,
            prepare_message="data coverage parameters ready",
            running_message="data coverage computation running",
            finalizing_message="data coverage result finalizing",
        )
    if job == "model_backtest":
        from app import main as main_module

        model_id = str(payload.get("model_id") or "active").strip() or "active"
        model = main_module._find_strategy_model(model_id)
        backtest_payload = {
            "model": model,
            "start_date": payload.get("start_date"),
            "end_date": payload.get("end_date"),
            "mode": payload.get("mode") or "intraday",
            "limit": payload.get("limit") or 0,
        }

        def execute() -> Dict[str, Any]:
            result = main_module._compute_model_backtest_cached(**backtest_payload)
            return main_module._compact_model_backtest_result(result if isinstance(result, dict) else {})

        return _run_tracked_job(
            job_manager,
            "model_backtest",
            execute,
            payload={**payload, "model_id": model_id},
            prepare_message="model backtest parameters ready",
            running_message="model backtest computation running",
            finalizing_message="model backtest result finalizing",
        )
    if job == "quant_timeline":
        from app import main as main_module

        timeline_payload = {
            "model_id": payload.get("model_id"),
            "start_date": payload.get("start_date"),
            "end_date": payload.get("end_date"),
            "initial_cash": payload.get("initial_cash"),
            "max_positions": payload.get("max_positions"),
            "hold_days": payload.get("hold_days"),
            "top_n": payload.get("top_n"),
            "intraday": bool(payload.get("intraday")),
            "use_daily_fallback": bool(payload.get("use_daily_fallback", True)),
            "auto_fill": bool(payload.get("auto_fill", True)),
        }

        def execute() -> Dict[str, Any]:
            result = main_module._compute_quant_timeline_cached(**timeline_payload)
            return main_module._compact_quant_timeline_result(result if isinstance(result, dict) else {})

        return _run_tracked_job(
            job_manager,
            "quant_timeline",
            execute,
            payload=timeline_payload,
            prepare_message="quant timeline parameters ready",
            running_message="quant timeline computation running",
            finalizing_message="quant timeline result finalizing",
        )
    if job == "quant_backtest":
        from app import main as main_module

        backtest_payload = {
            "as_of": payload.get("as_of"),
            "start_date": payload.get("start_date"),
            "end_date": payload.get("end_date"),
            "initial_cash": payload.get("initial_cash"),
            "max_positions": payload.get("max_positions"),
            "hold_days": payload.get("hold_days") or 3,
            "top_n": payload.get("top_n") or 5,
            "auto_fill": bool(payload.get("auto_fill")),
        }

        def execute() -> Dict[str, Any]:
            return main_module._compute_quant_backtest_cached(**backtest_payload)

        return _run_tracked_job(
            job_manager,
            "quant_backtest",
            execute,
            payload=backtest_payload,
            prepare_message="quant backtest parameters ready",
            running_message="quant backtest computation running",
            finalizing_message="quant backtest result finalizing",
        )
    if job == "fit_strategy":
        from app import main as main_module

        fit_payload = {
            "as_of": payload.get("as_of"),
            "start_date": payload.get("start_date"),
            "end_date": payload.get("end_date"),
            "apply_best": bool(payload.get("apply_best")),
        }

        def execute() -> Dict[str, Any]:
            result = main_module._compute_quant_fit_strategy(**fit_payload)
            return main_module._compact_quant_fit_strategy_result(result if isinstance(result, dict) else {})

        return _run_tracked_job(
            job_manager,
            "fit_strategy",
            execute,
            payload=fit_payload,
            prepare_message="fit strategy parameters ready",
            running_message="fit strategy optimization running",
            finalizing_message="fit strategy result finalizing",
        )
    raise SystemExit(f"unsupported job: {job}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a quant job in an isolated process.")
    parser.add_argument(
        "--job",
        required=True,
        choices=[
            "strategy_replay",
            "strategy_evolution",
            "frontend_payload_precompute",
            "frontend_account_precompute",
            "news_fetch",
            "ai_analysis",
            "kline_fill",
            "lhb_sync",
            "market_sync",
            "strategy_daily_refresh",
            "trade_cycle",
            "system_startup",
            "data_coverage",
            "model_backtest",
            "quant_timeline",
            "quant_backtest",
            "fit_strategy",
        ],
    )
    parser.add_argument("--payload-json", default="")
    args = parser.parse_args()
    _load_env_file()
    result = _run(args.job, _payload(args))
    status = str(result.get("status") if isinstance(result, dict) else "")
    return 0 if status in {"ok", "running", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
