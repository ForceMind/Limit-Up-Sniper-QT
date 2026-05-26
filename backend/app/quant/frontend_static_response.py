from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi.responses import FileResponse, JSONResponse


class FrontendStaticResponseService:
    def __init__(self, *, frontend_dir: Callable[[], Path]) -> None:
        self._frontend_dir = frontend_dir

    def index_response(self):
        index_file = self._frontend_dir() / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse({"status": "ok", "message": "frontend/index.html not found"})

    def admin_index_response(self):
        admin_file = self._frontend_dir() / "admin" / "index.html"
        if admin_file.exists():
            return FileResponse(admin_file)
        return JSONResponse({"status": "ok", "message": "frontend/admin/index.html not found"})
