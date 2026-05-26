from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from app.routers.admin_access import build_admin_access_router
from app.routers.admin_data_cache import build_admin_data_cache_router
from app.routers.admin_data_transfer import build_admin_data_transfer_router
from app.routers.admin_frontend_users import build_admin_frontend_users_router
from app.routers.admin_job_runs import build_admin_job_runs_router
from app.routers.admin_jobs import build_admin_jobs_router
from app.routers.admin_live import build_admin_live_router
from app.routers.admin_overview import build_admin_overview_router
from app.routers.admin_strategy_runtime import build_admin_strategy_runtime_router
from app.routers.system_control import build_system_control_router


def register_admin_routes(
    app: FastAPI,
    *,
    snapshot_service: Any,
    strategy_runtime_service: Any,
    runtime_status_service: Any,
    admin_job_run_service: Any,
    system_startup_service: Any,
    data_maintenance_service: Any,
    access_service: Any,
    frontend_user_service: Any,
    system_control_service: Any,
    job_manager: Any,
    route_defaults: Any,
    verify_admin_token: Callable[[str], Any],
    biying_status_payload: Callable[[], Any],
    live_log_key: Callable[..., Any],
    live_fingerprint: Callable[..., Any],
) -> None:
    app.include_router(
        build_admin_overview_router(
            snapshot_payload=snapshot_service.payload,
            model_signals_payload=strategy_runtime_service.model_signals_payload,
        )
    )
    app.include_router(
        build_admin_strategy_runtime_router(
            matrix_payload=strategy_runtime_service.matrix_payload,
            trading_account_payload=strategy_runtime_service.trading_account_payload,
            replay_payload=strategy_runtime_service.replay_payload,
        )
    )
    app.include_router(
        build_admin_live_router(
            verify_admin_token=verify_admin_token,
            jobs_payload=lambda: runtime_status_service.jobs_status_payload(light=True),
            status_payload=lambda jobs_payload: runtime_status_service.light_status_payload(jobs_payload=jobs_payload),
            biying_payload=biying_status_payload,
            logs_payload=lambda limit: job_manager.logs(limit=limit),
            log_key=live_log_key,
            fingerprint=live_fingerprint,
        )
    )
    app.include_router(
        build_admin_jobs_router(
            status_payload=runtime_status_service.jobs_status_payload,
            logs_payload=lambda limit, level, job: job_manager.logs(limit=limit, level=level, job=job),
            scheduler_start_payload=lambda: job_manager.start(),
            scheduler_stop_payload=lambda: job_manager.stop(),
            pause_payload=lambda job_name: job_manager.pause_job(job_name),
            resume_payload=lambda job_name: job_manager.resume_job(job_name),
            stop_payload=lambda job_name: job_manager.stop_job(job_name),
        )
    )
    app.include_router(
        build_admin_job_runs_router(
            news_fetch_payload=admin_job_run_service.news_fetch_payload,
            market_sync_payload=admin_job_run_service.market_sync_payload,
            ai_analyze_payload=admin_job_run_service.ai_analyze_payload,
            trading_run_payload=admin_job_run_service.trading_run_payload,
            strategy_daily_refresh_payload=admin_job_run_service.strategy_daily_refresh_payload,
            strategy_replay_payload=admin_job_run_service.strategy_replay_payload,
            frontend_payload_precompute_payload=admin_job_run_service.frontend_payload_precompute_payload,
            frontend_account_precompute_payload=admin_job_run_service.frontend_account_precompute_payload,
            system_startup_payload=system_startup_service.route_payload,
            **route_defaults.admin_job_runs_kwargs(),
        )
    )
    app.include_router(
        build_admin_data_cache_router(
            database_tables_payload=data_maintenance_service.database_tables_payload,
            database_table_payload=data_maintenance_service.database_table_payload,
            cache_status_payload=data_maintenance_service.cache_status_payload,
            cache_clear_payload=data_maintenance_service.cache_clear_payload,
        )
    )
    app.include_router(
        build_admin_data_transfer_router(
            backup_payload=data_maintenance_service.backup_payload,
            export_response_payload=data_maintenance_service.export_response_payload,
            import_status_payload=data_maintenance_service.import_status_payload,
            import_payload=data_maintenance_service.import_payload,
            clear_sample_state_payload=data_maintenance_service.clear_sample_state_payload,
        )
    )
    app.include_router(
        build_admin_access_router(
            access_logs_payload=access_service.logs_payload,
            access_security_payload=access_service.security_payload,
            block_payload=access_service.block_payload,
            unblock_payload=access_service.unblock_payload,
            block_all_payload=access_service.block_all_payload,
        )
    )
    app.include_router(
        build_admin_frontend_users_router(
            list_users_payload=frontend_user_service.list_users_payload,
            create_user_payload=frontend_user_service.create_user_payload,
            update_user_payload=frontend_user_service.update_user_payload,
            reset_password_payload=frontend_user_service.reset_password_payload,
            ban_user_payload=frontend_user_service.ban_user_payload,
            unban_user_payload=frontend_user_service.unban_user_payload,
            delete_user_payload=frontend_user_service.delete_user_payload,
        )
    )
    app.include_router(
        build_system_control_router(
            restart_payload=system_control_service.restart_payload,
            notification_status_payload=system_control_service.notification_status_payload,
            notification_test_payload=system_control_service.notification_test_payload,
        )
    )
