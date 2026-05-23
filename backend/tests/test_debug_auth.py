import hashlib
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant.security import verify_debug_request


class DummyRequest:
    def __init__(self, key: str = "", method: str = "GET"):
        self.headers = {"x-qt-debug-key": key} if key else {}
        self.method = method


def test_debug_request_accepts_hash_configured_key(monkeypatch):
    key = "qt_dbg_unit_case"
    monkeypatch.setenv("QT_DEBUG_API_ENABLED", "true")
    monkeypatch.setenv("QT_DEBUG_API_KEY_SHA256", hashlib.sha256(key.encode("utf-8")).hexdigest())
    monkeypatch.setenv("QT_DEBUG_API_ALLOW_WRITE", "false")

    payload = verify_debug_request(DummyRequest(key), "admin")

    assert payload
    assert payload["scope"] == "admin"
    assert payload["debug"] is True


def test_debug_request_blocks_write_when_disabled(monkeypatch):
    key = "qt_dbg_unit_case"
    monkeypatch.setenv("QT_DEBUG_API_ENABLED", "true")
    monkeypatch.setenv("QT_DEBUG_API_KEY", key)
    monkeypatch.setenv("QT_DEBUG_API_ALLOW_WRITE", "false")

    with pytest.raises(Exception) as exc_info:
        verify_debug_request(DummyRequest(key, method="POST"), "admin")

    assert getattr(exc_info.value, "status_code", None) == 403
