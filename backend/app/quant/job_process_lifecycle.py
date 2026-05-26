from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional


class JobProcessLifecycle:
    def pid_alive(self, pid: Any) -> bool:
        try:
            value = int(pid)
        except Exception:
            return False
        if value <= 0:
            return False
        if os.name == "nt":
            try:
                process_query_limited_information = 0x1000
                still_active = 259
                handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, value)
                if not handle:
                    return False
                exit_code = ctypes.c_ulong()
                try:
                    if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return False
                    return int(exit_code.value) == still_active
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                return True
        try:
            os.kill(value, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False

    def terminate_process_tree(self, pid: Any, *, pid_alive: Optional[Callable[[Any], bool]] = None) -> Dict[str, Any]:
        pid_alive = pid_alive or self.pid_alive
        try:
            value = int(pid)
        except Exception:
            return {"status": "error", "message": "进程号无效", "pid": pid}
        if value <= 0:
            return {"status": "error", "message": "进程号无效", "pid": value}
        if not pid_alive(value):
            return {"status": "ok", "message": "进程已经退出", "pid": value, "alive": False}
        if os.name == "nt":
            try:
                completed = subprocess.run(
                    ["taskkill", "/PID", str(value), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                return {
                    "status": "ok" if completed.returncode == 0 else "error",
                    "pid": value,
                    "return_code": completed.returncode,
                    "stdout": (completed.stdout or "").strip()[:500],
                    "stderr": (completed.stderr or "").strip()[:500],
                    "alive": pid_alive(value),
                }
            except Exception as exc:
                return {"status": "error", "message": str(exc), "pid": value, "alive": pid_alive(value)}

        errors = []
        try:
            os.killpg(value, signal.SIGTERM)
        except Exception as exc:
            errors.append(f"killpg SIGTERM: {exc}")
            try:
                os.kill(value, signal.SIGTERM)
            except Exception as inner_exc:
                errors.append(f"kill SIGTERM: {inner_exc}")
        time.sleep(0.5)
        if pid_alive(value):
            try:
                os.killpg(value, signal.SIGKILL)
            except Exception as exc:
                errors.append(f"killpg SIGKILL: {exc}")
                try:
                    os.kill(value, signal.SIGKILL)
                except Exception as inner_exc:
                    errors.append(f"kill SIGKILL: {inner_exc}")
        alive = pid_alive(value)
        return {
            "status": "ok" if not alive else "error",
            "pid": value,
            "alive": alive,
            "errors": errors,
        }

    def reconcile_state(
        self,
        state: Dict[str, Any],
        *,
        now: datetime,
        grace_seconds: float,
        parse_iso_ts: Callable[[Any], Optional[datetime]],
        pid_alive: Callable[[Any], bool],
        iso_now: Callable[[], str],
    ) -> list[Dict[str, Any]]:
        jobs = state.get("jobs") if isinstance(state.get("jobs"), dict) else {}
        stale_jobs = []
        for name, current in jobs.items():
            if not isinstance(current, dict):
                continue
            status_text = str(current.get("status") or "")
            if status_text not in {"running", "stop_requested"} or not current.get("process"):
                continue
            pid = current.get("process_pid")
            started_at = parse_iso_ts(current.get("last_started_at") or current.get("started_at"))
            if started_at and (now - started_at).total_seconds() < grace_seconds:
                continue
            if pid_alive(pid):
                continue
            stopped = bool(current.get("stop_requested")) or status_text == "stop_requested"
            message = "停止请求后的独立进程已退出" if stopped else "独立进程已退出但没有写入完成状态，任务已标记失败"
            current.update(
                {
                    "status": "stopped" if stopped else "failed",
                    "progress_message": message,
                    "progress_pct": 100 if stopped else current.get("progress_pct") or 1,
                    "last_error": "" if stopped else message,
                    "last_finished_at": iso_now(),
                    "updated_at": iso_now(),
                    "process": False,
                    "process_pid": "",
                    "stop_requested": False,
                    "stop_requested_at": "",
                    "stop_message": "",
                }
            )
            stale_jobs.append({"job": name, "pid": pid, "message": message, "stopped": stopped})
        return stale_jobs
