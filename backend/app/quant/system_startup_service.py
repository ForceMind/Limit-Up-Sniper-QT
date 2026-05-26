from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo


DateProvider = Callable[[], str]


def _default_date_provider() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


class SystemStartupService:
    def __init__(
        self,
        *,
        quant_engine: Any,
        job_manager: Any,
        date_provider: DateProvider = _default_date_provider,
    ) -> None:
        self._quant_engine = quant_engine
        self._job_manager = job_manager
        self._date_provider = date_provider

    def run_flow(
        self,
        *,
        target_date: str,
        replay_start_date: str,
        news_hours: int,
        news_pages: int,
        ai_items: int,
        market_codes: int,
        notify: bool,
        run_strategy_replay: bool = False,
    ) -> Dict[str, Any]:
        steps = []

        def stopped_before(stage: str) -> Optional[Dict[str, Any]]:
            if not self._job_manager.is_stop_requested("system_startup"):
                return None
            message = f"system startup stopped before {stage}"
            self._job_manager.update_progress("system_startup", 100, message, {"step": stage, "stopped": True})
            return {
                "status": "stopped",
                "message": message,
                "start_date": replay_start_date,
                "date": target_date,
                "steps": steps,
            }

        stopped = stopped_before("news_fetch")
        if stopped:
            return stopped
        self._job_manager.update_progress("system_startup", 8, "fetch news", {"step": "news_fetch"})
        news_result = self._job_manager.run_news_fetch(hours=news_hours, pages=news_pages, page_size=20)
        if news_result.get("status") == "ok":
            self._quant_engine.events(force=True)
        steps.append({"name": "news_fetch", "job": "news_fetch", "result": news_result})

        stopped = stopped_before("ai_analysis")
        if stopped:
            return stopped
        self._job_manager.update_progress("system_startup", 22, "AI analysis", {"step": "ai_analysis"})
        ai_result = self._job_manager.run_ai_analysis(as_of=target_date, max_items=ai_items, batch_size=4)
        steps.append({"name": "ai_analysis", "job": "ai_analysis", "result": ai_result})

        stopped = stopped_before("kline_fill")
        if stopped:
            return stopped
        self._job_manager.update_progress(
            "system_startup",
            38,
            "fill daily kline",
            {"step": "kline_fill", "start_date": replay_start_date, "end_date": target_date},
        )
        kline_result = self._job_manager.run_kline_fill(
            start_date=replay_start_date,
            end_date=target_date,
            max_codes=market_codes,
            force=False,
        )
        steps.append({"name": "kline_fill", "job": "kline_fill", "result": kline_result})

        stopped = stopped_before("lhb_sync")
        if stopped:
            return stopped
        self._job_manager.update_progress(
            "system_startup",
            54,
            "sync LHB",
            {"step": "lhb_sync", "start_date": replay_start_date, "end_date": target_date},
        )
        lhb_result = self._job_manager.run_lhb_sync(
            start_date=replay_start_date,
            end_date=target_date,
            max_stock_days=market_codes,
            force=False,
        )
        steps.append({"name": "lhb_sync", "job": "lhb_sync", "result": lhb_result})

        stopped = stopped_before("market_sync")
        if stopped:
            return stopped
        self._job_manager.update_progress("system_startup", 68, "sync intraday market", {"step": "market_sync"})
        market_result = self._job_manager.run_market_sync(
            date=target_date,
            source="auto",
            max_codes=market_codes,
            force=False,
            include_latest=True,
        )
        steps.append({"name": "market_sync", "job": "market_sync", "result": market_result})

        stopped = stopped_before("strategy_daily_refresh")
        if stopped:
            return stopped
        self._job_manager.update_progress(
            "system_startup",
            82,
            "refresh daily strategy runtime",
            {"step": "strategy_daily_refresh", "date": target_date},
        )
        refresh_result = self._job_manager.run_strategy_daily_refresh(date=target_date)
        steps.append({"name": "strategy_daily_refresh", "job": "strategy_daily_refresh", "result": refresh_result})

        stopped = stopped_before("trade_cycle")
        if stopped:
            return stopped
        self._job_manager.update_progress(
            "system_startup",
            88,
            "run lightweight trade cycle",
            {"step": "trade_cycle", "start_date": replay_start_date},
        )
        trade_result = self._job_manager.run_trade_cycle(date=target_date, notify=notify)
        steps.append({"name": "trade_cycle", "job": "trade_cycle", "result": trade_result})

        if run_strategy_replay:
            stopped = stopped_before("strategy_replay")
            if stopped:
                return stopped
            self._job_manager.update_progress(
                "system_startup",
                96,
                "run strategy replay",
                {"step": "strategy_replay", "start_date": replay_start_date},
            )
            replay_result = self._job_manager.run_strategy_replay(
                start_date=replay_start_date,
                end_date=target_date,
                mode="intraday",
                batch_days=15,
                use_cursor=True,
            )
            steps.append({"name": "strategy_replay", "job": "strategy_replay", "result": replay_result})
        else:
            self._job_manager.update_progress(
                "system_startup",
                96,
                "skip strategy replay; research tasks are manual-only",
                {"step": "strategy_replay", "manual_only": True},
            )
            steps.append(
                {
                    "name": "strategy_replay",
                    "job": "strategy_replay",
                    "result": {
                        "status": "skipped",
                        "manual_only": True,
                        "message": "strategy replay, training, and backtest are manual-only during startup",
                    },
                }
            )

        failed = [step for step in steps if (step.get("result") or {}).get("status") not in {"ok", "running", "skipped"}]
        return {
            "status": "partial" if failed else "ok",
            "message": "system startup flow completed" if not failed else "system startup flow completed with failed steps",
            "start_date": replay_start_date,
            "date": target_date,
            "steps": steps,
        }

    def payload(
        self,
        *,
        date: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        news_hours: int,
        news_pages: int,
        ai_items: int,
        market_codes: int,
        notify: bool,
        background: bool,
        process: bool,
        run_strategy_replay: bool,
    ) -> Dict[str, Any]:
        target_date = str(end_date or date or self._quant_engine.latest_event_date() or self._date_provider()).strip()
        replay_start_date = str(start_date or self._quant_engine.first_data_date() or "2026-03-01").strip()
        payload = {
            "date": target_date,
            "start_date": replay_start_date,
            "end_date": target_date,
            "news_hours": news_hours,
            "news_pages": news_pages,
            "ai_items": ai_items,
            "market_codes": market_codes,
            "notify": notify,
            "run_strategy_replay": run_strategy_replay,
        }

        def runner() -> Dict[str, Any]:
            return self.run_flow(
                target_date=target_date,
                replay_start_date=replay_start_date,
                news_hours=news_hours,
                news_pages=news_pages,
                ai_items=ai_items,
                market_codes=market_codes,
                notify=notify,
                run_strategy_replay=run_strategy_replay,
            )

        if process:
            return self._job_manager.run_job_process(
                "system_startup",
                payload=payload,
                message="system startup flow queued in an isolated process",
            )
        if background:
            return self._job_manager.run_job_background(
                "system_startup",
                runner,
                payload=payload,
                message="system startup flow queued in the background",
            )
        return self._job_manager.run_job(
            "system_startup",
            runner,
            payload=payload,
        )

    def route_payload(
        self,
        date: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        news_hours: int,
        news_pages: int,
        ai_items: int,
        market_codes: int,
        notify: bool,
        background: bool,
        process: bool,
        run_strategy_replay: bool,
    ) -> Dict[str, Any]:
        return self.payload(
            date=date,
            start_date=start_date,
            end_date=end_date,
            news_hours=news_hours,
            news_pages=news_pages,
            ai_items=ai_items,
            market_codes=market_codes,
            notify=notify,
            background=background,
            process=process,
            run_strategy_replay=run_strategy_replay,
        )
