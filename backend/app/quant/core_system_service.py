from __future__ import annotations

from typing import Any, Callable, Dict


class CoreSystemPayloadService:
    def __init__(
        self,
        *,
        app_name: Callable[[], str],
        app_version: Callable[[], str],
        git_ref: Callable[[], Dict[str, str]],
        openapi_schema: Callable[[], Dict[str, Any]],
        debug_auth_status: Callable[[], Dict[str, Any]],
        login: Callable[[Dict[str, Any], Any], Dict[str, Any]],
        register_frontend_user: Callable[[Dict[str, Any], Any], Dict[str, Any]],
        frontend_register_lifecycle: Callable[[Dict[str, Any]], Dict[str, Any]],
        update_runtime_config: Callable[[Dict[str, Any]], Dict[str, Any]],
        append_log: Callable[[str, str, str, str], None],
    ) -> None:
        self._app_name = app_name
        self._app_version = app_version
        self._git_ref = git_ref
        self._openapi_schema = openapi_schema
        self._debug_auth_status = debug_auth_status
        self._login = login
        self._register_frontend_user = register_frontend_user
        self._frontend_register_lifecycle = frontend_register_lifecycle
        self._update_runtime_config = update_runtime_config
        self._append_log = append_log

    def version_payload(self) -> Dict[str, Any]:
        version = self._app_version()
        return {
            "status": "ok",
            "app": self._app_name(),
            "version": version,
            "backend_version": version,
            "frontend_version": version,
            "git": self._git_ref(),
        }

    def debug_status_payload(self, request: Any) -> Dict[str, Any]:
        payload = getattr(getattr(request, "state", None), "auth_payload", None)
        return {
            "status": "ok",
            "debug_auth": self._debug_auth_status(),
            "auth": {
                "scope": str((payload or {}).get("scope") or ""),
                "sub": str((payload or {}).get("sub") or ""),
                "debug": bool((payload or {}).get("debug")),
                "write_allowed": bool((payload or {}).get("write_allowed")),
            },
            "version": self.version_payload(),
        }

    def debug_routes_payload(self) -> Dict[str, Any]:
        paths = self._openapi_schema().get("paths", {})
        modules: Dict[str, Dict[str, int]] = {}
        for path, operations in paths.items():
            if not isinstance(operations, dict):
                continue
            parts = [part for part in str(path).split("/") if part]
            module = parts[1] if len(parts) > 1 and parts[0] == "api" else "other"
            bucket = modules.setdefault(module, {"paths": 0, "operations": 0})
            bucket["paths"] += 1
            bucket["operations"] += len(operations)
        return {
            "status": "ok",
            "path_count": len(paths),
            "operation_count": sum(len(value) for value in paths.values() if isinstance(value, dict)),
            "modules": modules,
        }

    def auth_login_payload(self, request: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._login(payload, request)

    def auth_register_payload(self, request: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = self._register_frontend_user(payload, request)
        return self._frontend_register_lifecycle(result)

    def config_update_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = self._update_runtime_config(payload)
        self._append_log("warning", "runtime config saved", "admin_config", "saved")
        return result
