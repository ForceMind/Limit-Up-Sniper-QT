from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppRuntimeConfig:
    app_name: str
    base_dir: Path
    frontend_dir: Path
    project_root: Path
    backup_dir: Path
    version_file: Path

    @classmethod
    def from_app_file(cls, app_file: str | Path, *, app_name: str) -> "AppRuntimeConfig":
        base_dir = Path(app_file).resolve().parent.parent
        project_root = base_dir.parent
        return cls(
            app_name=app_name,
            base_dir=base_dir,
            frontend_dir=project_root / "frontend",
            project_root=project_root,
            backup_dir=project_root / "backups",
            version_file=project_root / "VERSION",
        )

    def app_version(self) -> str:
        try:
            version = self.version_file.read_text(encoding="utf-8").strip()
            return version or "0.0.0"
        except Exception:
            return os.getenv("QT_APP_VERSION", "0.0.0")

    def env_flag(self, name: str, default: bool = False) -> bool:
        raw = str(os.getenv(name, "")).strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def env_float(self, name: str, default: float) -> float:
        try:
            return float(os.getenv(name, "") or default)
        except Exception:
            return default

    def frontend_account_replay_days(self) -> int:
        value = int(self.env_float("QT_FRONTEND_ACCOUNT_REPLAY_DAYS", 90))
        return max(20, min(value, 260))
