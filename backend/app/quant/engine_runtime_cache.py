from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.quant.engine_utils import env_int


class EngineRuntimeCaches:
    def __init__(self) -> None:
        self.kline: Dict[str, List[Dict[str, Any]]] = {}
        self.kline_row_map: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.intraday: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self.future_return: Dict[Tuple[str, str, int], Optional[Dict[str, Any]]] = {}
        self.correlation: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
        self.factor: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.lhb_rows: Dict[str, List[Dict[str, Any]]] = {}
        self.lhb_by_code: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def cache_limit(self, name: str, default: int, maximum: Optional[int] = None) -> int:
        return env_int(name, default, minimum=0, maximum=maximum)

    @staticmethod
    def prune_cache(cache: Dict[Any, Any], limit: int) -> int:
        if limit <= 0:
            removed = len(cache)
            cache.clear()
            return removed
        removed = 0
        while len(cache) > limit:
            try:
                first_key = next(iter(cache))
            except StopIteration:
                break
            cache.pop(first_key, None)
            removed += 1
        return removed

    def remember_kline(self, code: str, rows: List[Dict[str, Any]]) -> None:
        self.kline[code] = rows
        limit = self.cache_limit("QT_KLINE_CACHE_MAX_CODES", 480, maximum=3000)
        while limit > 0 and len(self.kline) > limit:
            try:
                evicted = next(iter(self.kline))
            except StopIteration:
                break
            self.kline.pop(evicted, None)
            self.kline_row_map.pop(evicted, None)
        if limit <= 0:
            self.kline.clear()
            self.kline_row_map.clear()

    def remember_intraday(self, cache_key: Tuple[str, str], rows: List[Dict[str, Any]]) -> None:
        self.intraday[cache_key] = rows
        self.prune_cache(
            self.intraday,
            self.cache_limit("QT_INTRADAY_CACHE_MAX_KEYS", 120, maximum=2000),
        )

    def remember_future_return(self, cache_key: Tuple[str, str, int], value: Optional[Dict[str, Any]]) -> None:
        self.future_return[cache_key] = value
        self.prune_cache(
            self.future_return,
            self.cache_limit("QT_FUTURE_RETURN_CACHE_MAX_ITEMS", 12000, maximum=200000),
        )

    def remember_factor(self, cache_key: Tuple[str, str], value: Dict[str, Any]) -> None:
        self.factor[cache_key] = value
        self.prune_cache(
            self.factor,
            self.cache_limit("QT_FACTOR_CACHE_MAX_ITEMS", 6000, maximum=200000),
        )

    def remember_lhb(
        self,
        rows_key: str,
        rows: List[Dict[str, Any]],
        by_code_key: str,
        by_code: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        self.lhb_rows[rows_key] = rows
        self.lhb_by_code[by_code_key] = by_code
        limit = self.cache_limit("QT_LHB_CACHE_MAX_DATES", 3, maximum=30)
        self.prune_cache(self.lhb_rows, limit)
        self.prune_cache(self.lhb_by_code, limit)

    def trim(self, *, event_count: int = 0, aggressive: bool = False) -> Dict[str, Any]:
        before = self.stats(event_count=event_count)
        if aggressive:
            self.clear_market()
        else:
            self.prune_cache(
                self.intraday,
                self.cache_limit("QT_INTRADAY_CACHE_MAX_KEYS", 120, maximum=2000),
            )
            self.prune_cache(
                self.factor,
                self.cache_limit("QT_FACTOR_CACHE_MAX_ITEMS", 6000, maximum=200000),
            )
            self.prune_cache(
                self.future_return,
                self.cache_limit("QT_FUTURE_RETURN_CACHE_MAX_ITEMS", 12000, maximum=200000),
            )
            self.prune_cache(
                self.correlation,
                self.cache_limit("QT_CORRELATION_CACHE_MAX_ITEMS", 200, maximum=5000),
            )
            self.prune_cache(
                self.lhb_rows,
                self.cache_limit("QT_LHB_CACHE_MAX_DATES", 3, maximum=30),
            )
            self.prune_cache(
                self.lhb_by_code,
                self.cache_limit("QT_LHB_CACHE_MAX_DATES", 3, maximum=30),
            )
            self.remember_kline("__cache_trim_probe__", [])
            self.kline.pop("__cache_trim_probe__", None)
            self.kline_row_map.pop("__cache_trim_probe__", None)
        return {
            "before": before,
            "after": self.stats(event_count=event_count),
            "aggressive": bool(aggressive),
        }

    def stats(self, *, event_count: int = 0) -> Dict[str, Any]:
        return {
            "events": int(event_count or 0),
            "kline_codes": len(self.kline),
            "kline_row_maps": len(self.kline_row_map),
            "intraday_keys": len(self.intraday),
            "future_return_items": len(self.future_return),
            "correlation_items": len(self.correlation),
            "factor_items": len(self.factor),
            "lhb_rows_keys": len(self.lhb_rows),
            "lhb_by_code_keys": len(self.lhb_by_code),
        }

    def clear_intraday(self) -> None:
        self.intraday.clear()

    def clear_market(self) -> None:
        self.kline.clear()
        self.kline_row_map.clear()
        self.future_return.clear()
        self.correlation.clear()
        self.intraday.clear()
        self.factor.clear()
        self.lhb_rows.clear()
        self.lhb_by_code.clear()
