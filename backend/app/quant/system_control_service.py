from __future__ import annotations

import subprocess
import time
from pathlib import Path
from shutil import which as default_which
from typing import Any, Callable, Dict, Optional


class SystemControlService:
    def __init__(
        self,
        *,
        project_root: Callable[[], Path],
        env_flag: Callable[[str, bool], bool],
        notifier: Any,
        append_log: Callable[[str, str, str, str, Dict[str, Any]], None],
        which: Callable[[str], Optional[str]] = default_which,
        popen: Callable[..., Any] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._project_root = project_root
        self._env_flag = env_flag
        self._notifier = notifier
        self._append_log = append_log
        self._which = which
        self._popen = popen
        self._sleep = sleep

    def restart_service_after_response(self) -> None:
        self._sleep(0.5)
        root = self._project_root()
        script = root / "scripts" / "restart_server.sh"
        if not script.exists():
            return
        try:
            self._popen(
                ["bash", str(script)],
                cwd=str(root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception:
            return

    def restart_payload(self, background_tasks: Any) -> Dict[str, Any]:
        if not self._env_flag("QUANT_ALLOW_API_RESTART", False):
            result = {
                "status": "disabled",
                "message": "Set QUANT_ALLOW_API_RESTART=1 on the server to enable API-triggered restart.",
            }
            self._append_log(
                "warning",
                "API restart blocked because QUANT_ALLOW_API_RESTART is disabled.",
                "admin_restart",
                "blocked",
                result,
            )
            return result

        script = self._project_root() / "scripts" / "restart_server.sh"
        if not script.exists() or not self._which("bash"):
            result = {
                "status": "unavailable",
                "message": "restart script or bash runtime is not available on this host.",
            }
            self._append_log(
                "error",
                "API restart unavailable because restart script or bash runtime is missing.",
                "admin_restart",
                "unavailable",
                result,
            )
            return result

        background_tasks.add_task(self.restart_service_after_response)
        result = {"status": "ok", "message": "restart scheduled"}
        self._append_log(
            "warning",
            "API restart scheduled.",
            "admin_restart",
            "scheduled",
            result,
        )
        return result

    def notification_status_payload(self) -> Dict[str, Any]:
        return self._notifier.status()

    def notification_test_payload(self) -> Dict[str, Any]:
        return self._notifier.send_test()
