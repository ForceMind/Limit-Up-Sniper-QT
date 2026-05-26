from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Callable


def build_app_lifespan(
    *,
    env_flag: Callable[[str, bool], bool],
    job_manager: Any,
):
    @asynccontextmanager
    async def lifespan(_app):
        if env_flag("QT_MANUAL_TASK_START_ONLY", True):
            job_manager.mark_scheduler_disabled("QT_MANUAL_TASK_START_ONLY=1; scheduler is manual-only after restart")
        elif env_flag("QUANT_SCHEDULER_ENABLED", False):
            job_manager.start()
        else:
            job_manager.mark_scheduler_disabled("QUANT_SCHEDULER_ENABLED=0")
        try:
            yield
        finally:
            await job_manager.stop()

    return lifespan
