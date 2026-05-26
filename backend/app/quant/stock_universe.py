from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.quant.engine_utils import digits6, read_json
from app.quant.quant_paths import DATA_DIR, QUANT_DB_FILE


class StockUniverse:
    def __init__(self, data_dir: Optional[Path] = None, sqlite_file: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve() if data_dir is not None else DATA_DIR
        self.sqlite_file = (
            Path(sqlite_file).expanduser().resolve() if sqlite_file is not None else QUANT_DB_FILE
        )
        payload = read_json(self.data_dir / "biying_stock_list.json", {})
        stocks = payload.get("stocks") if isinstance(payload, dict) else {}
        if not isinstance(stocks, dict):
            stocks = {}
        self.code_to_name: Dict[str, str] = {}
        for raw_code, raw_name in stocks.items():
            code = digits6(raw_code)
            name = str(raw_name or "").strip()
            if code and name:
                self.code_to_name[code] = name
        self._load_names_from_sqlite()
        self.name_to_code = {
            name: code
            for code, name in self.code_to_name.items()
            if len(name) >= 2 and "退" not in name
        }
        self._name_patterns: List[Tuple[str, str]] = sorted(
            self.name_to_code.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        escaped_names = [re.escape(name) for name, _ in self._name_patterns if len(name) >= 2]
        self._name_regex = re.compile("|".join(escaped_names)) if escaped_names else None

    def _load_names_from_sqlite(self) -> None:
        if not self.sqlite_file.exists():
            return
        queries = [
            "SELECT code, name FROM news_events WHERE code IS NOT NULL AND name IS NOT NULL",
            "SELECT stock_code AS code, stock_name AS name FROM lhb_records WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL",
            "SELECT code, name FROM market_pool_items WHERE code IS NOT NULL AND name IS NOT NULL",
            "SELECT code, name FROM watchlist_items WHERE code IS NOT NULL AND name IS NOT NULL",
        ]
        try:
            conn = sqlite3.connect(self.sqlite_file)
            try:
                for query in queries:
                    try:
                        for raw_code, raw_name in conn.execute(query):
                            code = digits6(raw_code)
                            name = str(raw_name or "").strip()
                            if code and name and code not in self.code_to_name:
                                self.code_to_name[code] = name
                    except sqlite3.Error:
                        continue
            finally:
                conn.close()
        except Exception:
            return

    def normalize_code(self, code: Any = "", name: Any = "") -> str:
        normalized = digits6(code)
        if normalized:
            return normalized
        name_text = str(name or "").strip()
        return self.name_to_code.get(name_text, "")

    def name(self, code: str, fallback: str = "") -> str:
        return self.code_to_name.get(digits6(code), str(fallback or "").strip() or digits6(code))

    def is_tradeable_a_share(self, code: str) -> bool:
        code = digits6(code)
        if not code:
            return False
        name = self.code_to_name.get(code, "")
        if "ST" in name.upper() or "退" in name:
            return False
        return code.startswith(("0", "3", "6"))

    def extract_mentions(self, text: str, limit: int = 8) -> List[Tuple[str, str]]:
        if not text:
            return []
        found: List[Tuple[str, str]] = []
        seen = set()
        if self._name_regex is not None:
            for match in self._name_regex.finditer(text):
                name = match.group(0)
                code = self.name_to_code.get(name, "")
                if code and code not in seen:
                    found.append((code, name))
                    seen.add(code)
                    if len(found) >= limit:
                        break
        return found
