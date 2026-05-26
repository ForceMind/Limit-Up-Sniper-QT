from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from app.quant.job_runtime_control import (
    build_process_command,
    build_process_env,
    build_process_popen_kwargs,
)


class JobProcessLauncher:
    def __init__(
        self,
        *,
        python_executable: str = sys.executable,
        popen=None,
        environ: Optional[Dict[str, str]] = None,
    ) -> None:
        self._python_executable = python_executable
        self._popen = popen
        self._environ = environ

    def build_payload(
        self,
        *,
        name: str,
        payload: Dict[str, Any],
        queued_at: str,
        resource_controls: Dict[str, Any],
    ) -> Dict[str, Any]:
        process_payload = {
            "job": name,
            "payload": payload,
            "request_id": uuid.uuid4().hex[:16],
            "queued_at": queued_at,
        }
        if resource_controls:
            process_payload["resource_controls"] = resource_controls
        return process_payload

    def launch(
        self,
        *,
        project_root: Path,
        worker_script: Path,
        name: str,
        process_payload: Dict[str, Any],
        resource_controls: Dict[str, Any],
    ):
        env = build_process_env(
            base_env=dict(self._environ if self._environ is not None else os.environ),
            backend_dir=project_root / "backend",
            resource_controls=resource_controls,
        )
        popen_kwargs = build_process_popen_kwargs(
            project_root=project_root,
            env=env,
            windows_no_window_flag=(
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else None
            ),
        )
        command = build_process_command(
            python_executable=self._python_executable,
            worker_script=worker_script,
            job_name=name,
            process_payload=process_payload,
        )
        popen = self._popen or subprocess.Popen
        return popen(command, **popen_kwargs)
