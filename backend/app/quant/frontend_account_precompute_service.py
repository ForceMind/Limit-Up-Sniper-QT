from __future__ import annotations

import json
import os
import queue
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from app.quant.runtime_cache import env_int


class FrontendAccountPrecomputeService:
    ALLOWED_REASONS = {
        "profile_strategy_changed",
        "profile_cash_changed",
        "profile_cash_and_strategy_changed",
        "register",
        "account_runtime_missing",
    }

    def __init__(
        self,
        *,
        data_dir: Callable[[], Path],
        env_flag: Callable[[str, bool], bool],
        env_float: Callable[[str, float], float],
        job_manager: Any,
    ) -> None:
        self._data_dir = data_dir
        self._env_flag = env_flag
        self._env_float = env_float
        self._job_manager = job_manager
        self.queue_lock = threading.Lock()
        self.async_lock = threading.Lock()
        self.async_pending: Dict[str, float] = {}
        self.async_tasks: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.async_worker_lock = threading.Lock()
        self._async_workers_started = 0
        self._resolve_as_of: Optional[Callable[[Optional[str]], Optional[str]]] = None
        self._frontend_user_summary: Optional[Callable[[], Dict[str, Any]]] = None
        self._profile_context: Optional[Callable[..., Dict[str, Any]]] = None
        self._strategy_account: Optional[Callable[..., Dict[str, Any]]] = None

    @property
    def async_workers_started(self) -> int:
        return self._async_workers_started

    def configure_runtime(
        self,
        *,
        resolve_as_of: Callable[[Optional[str]], Optional[str]],
        frontend_user_summary: Callable[[], Dict[str, Any]],
        profile_context: Callable[..., Dict[str, Any]],
        strategy_account: Callable[..., Dict[str, Any]],
    ) -> None:
        self._resolve_as_of = resolve_as_of
        self._frontend_user_summary = frontend_user_summary
        self._profile_context = profile_context
        self._strategy_account = strategy_account

    def precompute_runtime(
        self,
        as_of: Optional[str] = None,
        usernames: Optional[Any] = None,
        limit_users: int = 50,
        limit: int = 160,
        force: bool = False,
        drain_queue: bool = False,
    ) -> Dict[str, Any]:
        resolve_as_of, frontend_user_summary, profile_context, strategy_account = self._runtime_callbacks()
        return self.precompute(
            as_of=as_of,
            usernames=usernames,
            limit_users=limit_users,
            limit=limit,
            force=force,
            drain_queue=drain_queue,
            resolve_as_of=resolve_as_of,
            frontend_user_summary=frontend_user_summary,
            profile_context=profile_context,
            strategy_account=strategy_account,
        )

    def start_runtime_worker_for_queue(
        self,
        as_of: Optional[str] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        self._runtime_callbacks()
        return self.start_worker_for_queue(
            as_of=as_of,
            reason=reason,
            precompute_accounts=self.precompute_runtime,
        )

    def queue_runtime_user(
        self,
        username: str,
        reason: str = "",
        as_of: Optional[str] = None,
        start_worker: Optional[bool] = None,
        async_enqueue: bool = False,
    ) -> Dict[str, Any]:
        self._runtime_callbacks()
        return self.queue_for_user(
            username=username,
            reason=reason,
            as_of=as_of,
            start_worker=start_worker,
            async_enqueue=async_enqueue,
            enqueue=self.enqueue,
            start_worker_for_queue=self.start_runtime_worker_for_queue,
        )

    def attach_runtime_precompute(
        self,
        payload: Dict[str, Any],
        context: Dict[str, Any],
        as_of: Optional[str],
        reason: str = "account_runtime_missing",
    ) -> Dict[str, Any]:
        self._runtime_callbacks()
        return self.attach_precompute(
            payload=payload,
            context=context,
            as_of=as_of,
            reason=reason,
            queue_for_user=self.queue_runtime_user,
        )

    def run_runtime_job_payload(
        self,
        *,
        as_of: Optional[str],
        usernames: Optional[str],
        limit_users: int,
        limit: int,
        force: bool,
        background: bool,
        process: bool,
        drain_queue: Optional[bool],
    ) -> Dict[str, Any]:
        self._runtime_callbacks()
        return self.run_job_payload(
            as_of=as_of,
            usernames=usernames,
            limit_users=limit_users,
            limit=limit,
            force=force,
            background=background,
            process=process,
            drain_queue=drain_queue,
            precompute_accounts=self.precompute_runtime,
        )

    def queue_file(self) -> Path:
        return self._data_dir() / "frontend_account_precompute_queue.json"

    def queue_lock_file(self) -> Path:
        return self._data_dir() / "frontend_account_precompute_queue.lock"

    @contextmanager
    def queue_file_lock(self):
        path = self.queue_lock_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        timeout_ms = env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_TIMEOUT_MS", 5000, minimum=100, maximum=60000)
        stale_ms = env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_STALE_MS", 30000, minimum=1000, maximum=600000)
        deadline = time.time() + timeout_ms / 1000
        fd: Optional[int] = None
        while True:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = {
                    "pid": os.getpid(),
                    "created_at": self._now_shanghai_iso(),
                }
                os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                break
            except FileExistsError:
                try:
                    if time.time() - path.stat().st_mtime > stale_ms / 1000:
                        path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() >= deadline:
                    raise TimeoutError(f"frontend account precompute queue lock timeout: {path}")
                time.sleep(0.05)
        try:
            yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def load_queue(self) -> list[Dict[str, Any]]:
        path = self.queue_file()
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        clean: list[Dict[str, Any]] = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "").strip()
            if not username or username in seen:
                continue
            seen.add(username)
            clean.append(
                {
                    "username": username,
                    "reason": str(item.get("reason") or ""),
                    "as_of": str(item.get("as_of") or ""),
                    "queued_at": str(item.get("queued_at") or ""),
                }
            )
        return clean

    def save_queue(self, items: list[Dict[str, Any]]) -> None:
        path = self.queue_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": self._now_shanghai_iso(),
            "items": items,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def enqueue(self, username: str, reason: str, as_of: Optional[str] = None) -> Dict[str, Any]:
        username = str(username or "").strip()
        if not username:
            return {"status": "skipped", "queued": False, "reason": "missing_username"}
        with self.queue_lock:
            with self.queue_file_lock():
                items = self.load_queue()
                items = [item for item in items if str(item.get("username") or "") != username]
                items.append(
                    {
                        "username": username,
                        "reason": str(reason or ""),
                        "as_of": str(as_of or ""),
                        "queued_at": self._now_shanghai_iso(),
                    }
                )
                max_items = env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_MAX_USERS", 500, minimum=1, maximum=5000)
                if len(items) > max_items:
                    items = items[-max_items:]
                self.save_queue(items)
                return {"status": "queued", "queued": True, "queue_size": len(items), "username": username}

    def dequeue(self, limit_users: int) -> list[Dict[str, Any]]:
        clean_limit = max(1, min(int(limit_users or 50), 500))
        with self.queue_lock:
            with self.queue_file_lock():
                items = self.load_queue()
                batch = items[:clean_limit]
                remaining = items[clean_limit:]
                self.save_queue(remaining)
                return batch

    def queue_size(self) -> int:
        with self.queue_lock:
            with self.queue_file_lock():
                return len(self.load_queue())

    def queue_status(self) -> Dict[str, Any]:
        path = self.queue_file()
        lock_path = self.queue_lock_file()
        now = time.time()
        stale_ms = env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_STALE_MS", 30000, minimum=1000, maximum=600000)
        payload: Dict[str, Any] = {
            "status": "ok",
            "queued": 0,
            "empty": True,
            "updated_at": "",
            "oldest_queued_at": "",
            "newest_queued_at": "",
            "reason_counts": {},
            "queue_file_exists": path.exists(),
            "lock": {
                "exists": lock_path.exists(),
                "age_ms": 0,
                "stale": False,
                "stale_after_ms": stale_ms,
            },
        }
        try:
            if path.exists():
                payload["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and raw.get("updated_at"):
                        payload["updated_at"] = str(raw.get("updated_at") or "")
                except Exception:
                    pass
            items = self.load_queue()
            queued_at_values = [str(item.get("queued_at") or "") for item in items if str(item.get("queued_at") or "")]
            reason_counts: Dict[str, int] = {}
            for item in items:
                reason = str(item.get("reason") or "unknown")
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            payload.update(
                {
                    "queued": len(items),
                    "empty": len(items) <= 0,
                    "oldest_queued_at": min(queued_at_values) if queued_at_values else "",
                    "newest_queued_at": max(queued_at_values) if queued_at_values else "",
                    "reason_counts": reason_counts,
                }
            )
        except Exception as exc:
            payload.update({"status": "error", "message": str(exc)})

        if lock_path.exists():
            try:
                age_ms = max(0, int((now - lock_path.stat().st_mtime) * 1000))
                payload["lock"] = {
                    "exists": True,
                    "age_ms": age_ms,
                    "stale": age_ms > stale_ms,
                    "stale_after_ms": stale_ms,
                }
            except Exception as exc:
                payload["lock"] = {"exists": True, "age_ms": 0, "stale": False, "stale_after_ms": stale_ms, "error": str(exc)}
        return payload

    def split_usernames(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item or "").strip() for item in value if str(item or "").strip()]
        return [str(value or "").strip()] if str(value or "").strip() else []

    def merge_precompute_result(self, target: Dict[str, Any], result: Dict[str, Any]) -> None:
        target["user_count"] += self._safe_int(result.get("user_count"), 0)
        target["saved"] += self._safe_int(result.get("saved"), 0)
        target["cached"] += self._safe_int(result.get("cached"), 0)
        target["pending"] += self._safe_int(result.get("pending"), 0)
        target["error_count"] += self._safe_int(result.get("error_count"), 0)
        target["items"].extend(result.get("items") if isinstance(result.get("items"), list) else [])
        target["errors"].extend(result.get("errors") if isinstance(result.get("errors"), list) else [])

    def precompute_once(
        self,
        *,
        as_of: Optional[str],
        usernames: Optional[Any],
        limit_users: int,
        limit: int,
        force: bool,
        resolve_as_of: Callable[[Optional[str]], Optional[str]],
        frontend_user_summary: Callable[[], Dict[str, Any]],
        profile_context: Callable[..., Dict[str, Any]],
        strategy_account: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        effective_as_of = resolve_as_of(as_of)
        requested = set(self.split_usernames(usernames))
        clean_limit_users = max(1, min(int(limit_users or 50), 500))
        clean_limit = max(1, min(int(limit or 160), 2000))
        users_payload = frontend_user_summary()
        users_items = users_payload.get("items") if isinstance(users_payload, dict) else []
        candidates = []
        for item in users_items if isinstance(users_items, list) else []:
            if not isinstance(item, dict) or item.get("disabled"):
                continue
            username = str(item.get("username") or "").strip()
            if not username:
                continue
            if requested and username not in requested:
                continue
            candidates.append(username)
            if len(candidates) >= clean_limit_users:
                break

        results = []
        saved = 0
        cached = 0
        pending = 0
        errors = []
        for username in candidates:
            row: Dict[str, Any] = {"username": username}
            try:
                context = profile_context(username, include_catalog=False)
                account = strategy_account(
                    context,
                    effective_as_of,
                    limit=clean_limit,
                    force=force,
                    record_period=True,
                    defer_miss=True,
                )
                source = str(account.get("strategy_account_source") or "")
                cache_state = str(account.get("strategy_account_cache") or "")
                row.update(
                    {
                        "status": "pending" if account.get("frontend_account_deferred") else "ok",
                        "strategy_model_id": str((context.get("profile") or {}).get("strategy_model_id") or ""),
                        "follow_start_date": str(account.get("follow_start_date") or ""),
                        "source": source,
                        "cache": cache_state,
                        "message": account.get("message") or "",
                    }
                )
                if account.get("frontend_account_deferred"):
                    pending += 1
                elif cache_state == "user_follow":
                    cached += 1
                else:
                    saved += 1
            except Exception as exc:
                row.update({"status": "error", "error": str(exc)})
                errors.append({"username": username, "error": str(exc)})
            results.append(row)

        status = "ok" if not errors else ("partial" if saved or cached or pending else "error")
        return {
            "status": status,
            "job": "frontend_account_precompute",
            "as_of": effective_as_of,
            "user_count": len(candidates),
            "saved": saved,
            "cached": cached,
            "pending": pending,
            "error_count": len(errors),
            "errors": errors[:20],
            "items": results,
            "force": bool(force),
            "limit": clean_limit,
            "generated_at": self._now_shanghai_iso(),
        }

    def precompute(
        self,
        *,
        as_of: Optional[str],
        usernames: Optional[Any],
        limit_users: int,
        limit: int,
        force: bool,
        drain_queue: bool,
        resolve_as_of: Callable[[Optional[str]], Optional[str]],
        frontend_user_summary: Callable[[], Dict[str, Any]],
        profile_context: Callable[..., Dict[str, Any]],
        strategy_account: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not drain_queue:
            return self.precompute_once(
                as_of=as_of,
                usernames=usernames,
                limit_users=limit_users,
                limit=limit,
                force=force,
                resolve_as_of=resolve_as_of,
                frontend_user_summary=frontend_user_summary,
                profile_context=profile_context,
                strategy_account=strategy_account,
            )

        clean_limit_users = max(1, min(int(limit_users or 50), 500))
        summary: Dict[str, Any] = {
            "status": "ok",
            "job": "frontend_account_precompute",
            "as_of": resolve_as_of(as_of),
            "drain_queue": True,
            "batches": 0,
            "user_count": 0,
            "saved": 0,
            "cached": 0,
            "pending": 0,
            "error_count": 0,
            "errors": [],
            "items": [],
            "force": bool(force),
            "limit": max(1, min(int(limit or 160), 2000)),
            "generated_at": self._now_shanghai_iso(),
        }
        max_batches = env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_MAX_BATCHES", 20, minimum=1, maximum=200)
        idle_grace_ms = env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_IDLE_GRACE_MS", 500, minimum=0, maximum=5000)
        idle_checked = False
        for _index in range(max_batches):
            batch = self.dequeue(clean_limit_users)
            if not batch:
                if idle_grace_ms > 0 and not idle_checked:
                    idle_checked = True
                    time.sleep(idle_grace_ms / 1000)
                    continue
                break
            idle_checked = False
            usernames_batch = [str(item.get("username") or "").strip() for item in batch if str(item.get("username") or "").strip()]
            if not usernames_batch:
                continue
            batch_as_of = as_of or next((str(item.get("as_of") or "").strip() for item in batch if str(item.get("as_of") or "").strip()), None)
            result = self.precompute_once(
                as_of=batch_as_of,
                usernames=usernames_batch,
                limit_users=len(usernames_batch),
                limit=limit,
                force=force,
                resolve_as_of=resolve_as_of,
                frontend_user_summary=frontend_user_summary,
                profile_context=profile_context,
                strategy_account=strategy_account,
            )
            summary["batches"] += 1
            self.merge_precompute_result(summary, result)

        if summary["error_count"]:
            summary["status"] = "partial" if summary["saved"] or summary["cached"] or summary["pending"] else "error"
        summary["errors"] = summary["errors"][:20]
        return summary

    def async_status(self) -> Dict[str, Any]:
        now = time.time()
        debounce_seconds = max(0.0, min(self._env_float("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DEBOUNCE_SECONDS", 5.0), 300.0))
        stale_after = max(debounce_seconds, 60.0)
        reason_counts: Dict[str, int] = {}
        mode_counts: Dict[str, int] = {}
        ages_ms: list[int] = []
        with self.async_lock:
            for key, ts in list(self.async_pending.items()):
                if now - ts > stale_after:
                    self.async_pending.pop(key, None)
                    continue
                parts = str(key).split("|")
                reason = parts[1] if len(parts) > 1 and parts[1] else "unknown"
                mode = parts[3] if len(parts) > 3 and parts[3] else "queue_only"
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
                mode_counts[mode] = mode_counts.get(mode, 0) + 1
                ages_ms.append(max(0, int((now - ts) * 1000)))
            pending_count = len(self.async_pending)
        return {
            "status": "ok",
            "pending_count": pending_count,
            "empty": pending_count <= 0,
            "debounce_seconds": round(debounce_seconds, 3),
            "stale_after_seconds": round(stale_after, 3),
            "oldest_age_ms": max(ages_ms) if ages_ms else 0,
            "newest_age_ms": min(ages_ms) if ages_ms else 0,
            "queued_tasks": self.async_tasks.qsize(),
            "worker_started": self._async_workers_started > 0,
            "worker_count": self._async_workers_started,
            "worker_target": env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS", 4, minimum=1, maximum=16),
            "reason_counts": reason_counts,
            "mode_counts": mode_counts,
        }

    def submit_async(self, task: Callable[[], None]) -> None:
        self._ensure_async_worker()
        self.async_tasks.put(task)

    def ensure_async_worker(self) -> None:
        self._ensure_async_worker()

    def start_worker_for_queue(
        self,
        *,
        as_of: Optional[str],
        reason: str,
        precompute_accounts: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            queue_size = self.queue_size()
        except Exception as exc:
            self._append_log("warning", f"frontend account precompute queue status read failed: {exc}", stage="queue")
            return {"status": "error", "queued": False, "worker_started": False, "reason": reason, "message": str(exc)}
        if queue_size <= 0:
            return {"status": "skipped", "queued": False, "worker_started": False, "reason": reason or "queue_empty", "queue_size": 0}

        payload = {
            "as_of": as_of,
            "usernames": None,
            "limit_users": env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_BATCH_USERS", 50, minimum=1, maximum=500),
            "limit": env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_LIMIT", 160, minimum=1, maximum=2000),
            "force": False,
            "drain_queue": True,
        }

        def execute() -> Dict[str, Any]:
            return precompute_accounts(**payload)

        try:
            if self._env_flag("QT_FRONT_ACCOUNT_PRECOMPUTE_PROCESS_ENABLED", True):
                result = self._job_manager.run_job_process(
                    "frontend_account_precompute",
                    payload=payload,
                    message="Frontend account precompute queue moved to an isolated process.",
                )
            else:
                result = self._job_manager.run_job_background(
                    "frontend_account_precompute",
                    execute,
                    payload=payload,
                    message="Frontend account precompute queue moved to a background worker.",
                )
        except Exception as exc:
            self._append_log("warning", f"frontend account precompute queue worker start failed: {exc}", stage="queue")
            return {"status": "queued", "queued": True, "worker_started": False, "reason": reason, "queue_size": queue_size, "message": str(exc)}

        if isinstance(result, dict):
            worker_started = bool(result.get("process_pid") or result.get("progress_pct") is not None)
            return {**result, "queued": True, "worker_started": worker_started, "reason": reason, "queue_size": queue_size}
        return {"status": "ok", "queued": True, "worker_started": True, "reason": reason, "queue_size": queue_size}

    def run_job_payload(
        self,
        *,
        as_of: Optional[str],
        usernames: Optional[str],
        limit_users: int,
        limit: int,
        force: bool,
        background: bool,
        process: bool,
        drain_queue: Optional[bool],
        precompute_accounts: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        queue_status = self.queue_status()
        effective_drain_queue = bool(drain_queue) if drain_queue is not None else (
            not str(usernames or "").strip() and self._safe_int(queue_status.get("queued"), 0) > 0
        )
        payload = {
            "as_of": as_of,
            "usernames": usernames,
            "limit_users": limit_users,
            "limit": limit,
            "force": bool(force),
            "drain_queue": effective_drain_queue,
        }

        def execute() -> Dict[str, Any]:
            return precompute_accounts(**payload)

        if process:
            return self._job_manager.run_job_process(
                "frontend_account_precompute",
                payload=payload,
                message="前台账户快照预热已转入独立进程运行",
            )
        if background:
            return self._job_manager.run_job_background(
                "frontend_account_precompute",
                execute,
                payload=payload,
                message="前台账户快照预热已转入后台运行",
            )
        return self._job_manager.run_job("frontend_account_precompute", execute, payload=payload)

    def needs_precompute(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        return bool(
            payload.get("frontend_account_deferred")
            or payload.get("user_follow_persist_deferred")
            or str(payload.get("status") or "") == "pending"
            or str(account.get("status") or "") == "pending"
        )

    def attach_precompute(
        self,
        *,
        payload: Dict[str, Any],
        context: Dict[str, Any],
        as_of: Optional[str],
        reason: str,
        queue_for_user: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not self._env_flag("QT_FRONT_ACCOUNT_AUTO_PRECOMPUTE_ENABLED", True):
            return payload
        if not self.needs_precompute(payload):
            return payload
        effective_reason = str(payload.get("frontend_account_precompute_reason") or reason or "account_runtime_missing")
        rescue = queue_for_user(
            str(context.get("username") or ""),
            reason=effective_reason,
            as_of=as_of,
            start_worker=True,
            async_enqueue=True,
        )
        if rescue.get("queued") or rescue.get("status") == "error":
            payload["account_precompute"] = rescue
            payload["account_precompute_queued"] = bool(rescue.get("queued"))
        return payload

    def queue_for_user(
        self,
        *,
        username: str,
        reason: str = "",
        as_of: Optional[str] = None,
        start_worker: Optional[bool] = None,
        async_enqueue: bool = False,
        enqueue: Callable[[str, str, Optional[str]], Dict[str, Any]],
        start_worker_for_queue: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        username = str(username or "").strip()
        reason = str(reason or "").strip()
        if not username:
            return {"status": "skipped", "reason": "missing_username"}
        if reason not in self.ALLOWED_REASONS:
            return {"status": "skipped", "reason": reason or "profile_unchanged"}
        if not self._env_flag("QT_FRONT_ACCOUNT_AUTO_PRECOMPUTE_ENABLED", True):
            return {"status": "disabled", "reason": reason}

        should_start_worker = (
            self._env_flag("QT_FRONT_ACCOUNT_START_WORKER_ON_PROFILE", False)
            if start_worker is None
            else bool(start_worker)
        )
        if async_enqueue and self._env_flag("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_ASYNC_ON_PROFILE", True):
            clean_username = username
            reason_text = reason
            as_of_text = as_of
            start_worker_value = should_start_worker
            async_key = "|".join(
                [clean_username, reason_text, str(as_of_text or ""), "start_worker" if start_worker_value else "queue_only"]
            )
            debounce_seconds = max(0.0, min(self._env_float("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DEBOUNCE_SECONDS", 5.0), 300.0))
            now_ts = time.time()
            with self.async_lock:
                stale_after = max(debounce_seconds, 60.0)
                for key, ts in list(self.async_pending.items()):
                    if now_ts - ts > stale_after:
                        self.async_pending.pop(key, None)
                previous_ts = self.async_pending.get(async_key)
                if debounce_seconds > 0 and previous_ts and now_ts - previous_ts < debounce_seconds:
                    return {
                        "status": "queued_async",
                        "queued": True,
                        "async": True,
                        "deduped": True,
                        "queue_pending": True,
                        "reason": reason,
                        "username": username,
                        "worker_started": False,
                        "worker_start_deferred": not should_start_worker,
                        "worker_start_pending": bool(should_start_worker),
                        "debounce_seconds": round(debounce_seconds, 3),
                    }
                self.async_pending[async_key] = now_ts

            def worker() -> None:
                result = self.queue_for_user(
                    username=clean_username,
                    reason=reason_text,
                    as_of=as_of_text,
                    start_worker=start_worker_value,
                    async_enqueue=False,
                    enqueue=enqueue,
                    start_worker_for_queue=start_worker_for_queue,
                )
                if str(result.get("status") or "") == "error":
                    self._append_log(
                        "warning",
                        f"frontend account async enqueue failed: {result.get('message') or 'unknown error'}",
                        stage="queue_async",
                        payload={"username": clean_username, "reason": reason_text},
                    )

            self.submit_async(worker)
            return {
                "status": "queued_async",
                "queued": True,
                "async": True,
                "deduped": False,
                "queue_pending": True,
                "reason": reason,
                "username": username,
                "worker_started": False,
                "worker_start_deferred": not should_start_worker,
                "worker_start_pending": bool(should_start_worker),
                "debounce_seconds": round(debounce_seconds, 3),
            }

        try:
            queue_result = enqueue(username, reason, as_of)
        except Exception as exc:
            self._append_log("warning", f"frontend account enqueue failed: {exc}", stage="queue")
            return {"status": "error", "queued": False, "reason": reason, "username": username, "message": str(exc)}
        if not queue_result.get("queued"):
            return {**queue_result, "reason": reason, "username": username}

        if not should_start_worker:
            return {
                **queue_result,
                "reason": reason,
                "username": username,
                "queued": True,
                "worker_started": False,
                "worker_start_deferred": True,
            }

        worker_result = start_worker_for_queue(as_of=as_of, reason=reason)
        return {**worker_result, **queue_result, "reason": reason, "username": username, "queued": True}

    def _ensure_async_worker(self) -> None:
        worker_target = env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS", 4, minimum=1, maximum=16)
        if self._async_workers_started >= worker_target:
            return
        with self.async_worker_lock:
            if self._async_workers_started >= worker_target:
                return

            def run() -> None:
                while True:
                    task = self.async_tasks.get()
                    try:
                        delay_ms = env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DISPATCH_DELAY_MS", 25, minimum=0, maximum=1000)
                        if delay_ms > 0:
                            time.sleep(delay_ms / 1000)
                        task()
                    except Exception as exc:
                        self._append_log("warning", f"frontend account async task failed: {exc}", stage="queue_async")
                    finally:
                        self.async_tasks.task_done()

            while self._async_workers_started < worker_target:
                next_index = self._async_workers_started + 1
                threading.Thread(target=run, name=f"qt-account-precompute-async-{next_index}", daemon=True).start()
                self._async_workers_started = next_index

    def _append_log(self, level: str, message: str, *, stage: str, payload: Optional[Dict[str, Any]] = None) -> None:
        try:
            self._job_manager._append_log(
                level,
                message,
                job="frontend_account_precompute",
                stage=stage,
                payload=payload or {},
            )
        except Exception:
            pass

    def _runtime_callbacks(
        self,
    ) -> tuple[
        Callable[[Optional[str]], Optional[str]],
        Callable[[], Dict[str, Any]],
        Callable[..., Dict[str, Any]],
        Callable[..., Dict[str, Any]],
    ]:
        if (
            self._resolve_as_of is None
            or self._frontend_user_summary is None
            or self._profile_context is None
            or self._strategy_account is None
        ):
            raise RuntimeError("frontend account precompute runtime callbacks are not configured")
        return (
            self._resolve_as_of,
            self._frontend_user_summary,
            self._profile_context,
            self._strategy_account,
        )

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(float(value if value is not None else default))
        except Exception:
            return default

    @staticmethod
    def _now_shanghai_iso() -> str:
        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
