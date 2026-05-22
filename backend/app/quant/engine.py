from __future__ import annotations

import contextlib
import hashlib
import csv
import json
import math
import os
import re
import sqlite3
import statistics
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BACKEND_DIR = Path(__file__).resolve().parents[2]


def _configured_data_dir() -> Path:
    raw = str(os.getenv("QUANT_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return BACKEND_DIR / "data"


DATA_DIR = _configured_data_dir()
QUANT_DB_FILE = DATA_DIR / "quant_data.sqlite3"
KLINE_DAY_DIR = DATA_DIR / "kline_day_cache"
KLINE_MIN_DIR = DATA_DIR / "kline_cache"
STATE_FILE = DATA_DIR / "quant_state.json"
EVENTS_CACHE_FILE = DATA_DIR / "quant_events_cache.json"
LHB_HISTORY_FILE = DATA_DIR / "lhb_history.csv"
SAMPLE_CODES = {"600001", "600002"}
SAMPLE_MARKERS = ("样例", "Fixture", "样例算力", "样例电力")
DEFAULT_AI_MODEL = "deepseek-v4-flash"
DEFAULT_BROKER_FEE_PARAMS = {
    "commission_rate": 0.00025,
    "min_commission": 5.0,
    "stamp_duty_rate": 0.0005,
    "transfer_fee_rate": 0.00001,
}
DEFAULT_STRATEGY_PARAMS = {
    "sentiment_weight": 0.35,
    "event_weight": 0.25,
    "technical_weight": 0.25,
    "risk_weight": 0.15,
    "buy_threshold": 72.0,
    "watch_threshold": 60.0,
    "avoid_sell_threshold": 70.0,
    "avoid_buy_ceiling": 65.0,
    "sell_score_threshold": 72.0,
    "stop_loss_pct": -5.0,
    "take_profit_pct": 8.0,
    "max_hold_days": 3.0,
    "max_positions": 5.0,
    "top_n": 5.0,
    "paper_max_hold_days": 6.0,
    "account_initial_cash": 200000.0,
    "paper_position_value": 30000.0,
    "sentiment_coef": 32.0,
    "ai_score_coef": 5.0,
    "event_impact_weight": 0.62,
    "history_score_weight": 0.38,
    "history_return_coef": 420.0,
    "history_win_coef": 45.0,
    "sell_negative_sentiment_coef": 22.0,
    "sell_technical_risk_coef": 0.55,
    "negative_sentiment_risk_penalty": 15.0,
    "risk_event_penalty": 20.0,
}

_JSON_WRITE_LOCKS: Dict[str, threading.Lock] = {}
_JSON_WRITE_LOCKS_GUARD = threading.Lock()


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null", "--"}:
            return default
        return float(text)
    except Exception:
        return default


def digits6(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) > 6:
        text = text[-6:]
    return text if len(text) == 6 else ""


def is_sample_code(value: Any) -> bool:
    return digits6(value) in SAMPLE_CODES


def contains_sample_marker(value: Any) -> bool:
    if isinstance(value, dict):
        if is_sample_code(value.get("code") or value.get("stock_code")):
            return True
        return any(contains_sample_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_sample_marker(item) for item in value)
    if isinstance(value, str):
        return any(marker in value for marker in SAMPLE_MARKERS)
    return False


def parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text]
    if len(text) >= 19:
        candidates.extend([text[:19], text[:10]])
    elif len(text) >= 10:
        candidates.append(text[:10])
    for candidate in candidates:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(candidate, fmt)
            except Exception:
                pass
    return None


def item_datetime(item: Dict[str, Any]) -> Optional[datetime]:
    dt = parse_time(item.get("time_str") or item.get("analyzed_at") or item.get("date"))
    if dt:
        return dt
    ts = safe_float(item.get("timestamp"), 0)
    if ts > 0:
        try:
            return datetime.fromtimestamp(ts)
        except Exception:
            return None
    return None


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    with _JSON_WRITE_LOCKS_GUARD:
        lock = _JSON_WRITE_LOCKS.setdefault(key, threading.Lock())
    with lock:
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        last_error: Optional[Exception] = None
        for _ in range(5):
            try:
                tmp.replace(path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.08)
        try:
            tmp.unlink(missing_ok=True)
        finally:
            if last_error:
                raise last_error


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class NewsEvent:
    event_id: str
    date: str
    timestamp: int
    source: str
    text: str
    code: str
    name: str
    industry: str
    event_type: str
    sentiment: float
    impact_score: float
    ai_score: float
    reason: str

    def compact(self) -> Dict[str, Any]:
        out = asdict(self)
        out["sentiment"] = round(self.sentiment, 3)
        out["impact_score"] = round(self.impact_score, 2)
        out["ai_score"] = round(self.ai_score, 2)
        return out


class StockUniverse:
    def __init__(self) -> None:
        payload = read_json(DATA_DIR / "biying_stock_list.json", {})
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
        if not QUANT_DB_FILE.exists():
            return
        queries = [
            "SELECT code, name FROM news_events WHERE code IS NOT NULL AND name IS NOT NULL",
            "SELECT stock_code AS code, stock_name AS name FROM lhb_records WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL",
            "SELECT code, name FROM market_pool_items WHERE code IS NOT NULL AND name IS NOT NULL",
            "SELECT code, name FROM watchlist_items WHERE code IS NOT NULL AND name IS NOT NULL",
        ]
        try:
            conn = sqlite3.connect(QUANT_DB_FILE)
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


class EventClassifier:
    EVENT_KEYWORDS = {
        "政策催化": ["政策", "意见", "通知", "发布", "印发", "支持", "规划", "补贴", "国务院", "发改委", "工信部", "商务部"],
        "业绩财报": ["业绩", "财报", "净利润", "营收", "预增", "预亏", "一季报", "年报", "增长"],
        "订单合作": ["订单", "中标", "合作", "签约", "协议", "采购", "项目", "交付"],
        "产品技术": ["发布", "新品", "量产", "突破", "研发", "技术", "专利", "商业化"],
        "板块异动": ["板块", "拉升", "走强", "涨停", "异动", "领涨", "短线", "封板"],
        "宏观市场": ["指数", "沪指", "深成指", "创业板", "成交", "市场", "人民币", "利率"],
        "风险事件": ["下跌", "跌停", "处罚", "调查", "减持", "亏损", "终止", "风险", "下挫", "领跌"],
    }
    INDUSTRY_KEYWORDS = {
        "AI算力": ["AI", "人工智能", "算力", "大模型", "服务器", "数据中心", "液冷"],
        "半导体": ["半导体", "芯片", "存储", "光刻", "封测", "晶圆"],
        "电力能源": ["电力", "电网", "火电", "水电", "核电", "储能", "虚拟电厂"],
        "新能源": ["新能源", "光伏", "风电", "锂电", "电池", "固态电池", "储能"],
        "汽车": ["汽车", "整车", "智能驾驶", "无人驾驶", "车路云", "零部件"],
        "机器人": ["机器人", "人形机器人", "减速器", "伺服", "工业母机"],
        "医药": ["医药", "创新药", "医疗", "器械", "疫苗", "CRO"],
        "消费零售": ["零售", "消费", "食品", "饮料", "白酒", "免税", "旅游"],
        "金融地产": ["银行", "证券", "保险", "地产", "房地产", "物业"],
        "低空经济": ["低空", "无人机", "eVTOL", "飞行汽车", "通航"],
        "军工": ["军工", "航天", "航空", "卫星", "导弹", "船舶"],
        "有色资源": ["有色", "铜", "铝", "黄金", "稀土", "锂矿", "煤炭"],
        "传媒游戏": ["传媒", "游戏", "影视", "短剧", "出版", "广告"],
    }
    POSITIVE = ["涨停", "拉升", "走强", "大涨", "利好", "突破", "预增", "中标", "签约", "获批", "支持", "超预期", "封板"]
    NEGATIVE = ["下跌", "跌停", "领跌", "调查", "处罚", "减持", "亏损", "终止", "低于预期", "风险", "下挫", "走弱"]

    def classify_event_type(self, text: str) -> str:
        scores = {
            label: sum(1 for keyword in keywords if keyword in text)
            for label, keywords in self.EVENT_KEYWORDS.items()
        }
        best = max(scores.items(), key=lambda item: item[1])
        return best[0] if best[1] > 0 else "综合新闻"

    def classify_industry(self, text: str, concept: str = "") -> str:
        source = f"{concept} {text}"
        for label, keywords in self.INDUSTRY_KEYWORDS.items():
            if any(keyword in source for keyword in keywords):
                return label
        clean_concept = str(concept or "").strip()
        return clean_concept[:16] if clean_concept else "未分类"

    def sentiment(self, text: str, ai_score: float = 0.0) -> float:
        pos = sum(1 for keyword in self.POSITIVE if keyword in text)
        neg = sum(1 for keyword in self.NEGATIVE if keyword in text)
        keyword_score = 0.0
        if pos or neg:
            keyword_score = (pos - neg) / max(3, pos + neg)
        if ai_score > 0:
            ai_score_norm = clamp((ai_score - 5.0) / 4.0, -1.0, 1.0)
            return clamp((keyword_score * 0.55) + (ai_score_norm * 0.45), -1.0, 1.0)
        return clamp(keyword_score, -1.0, 1.0)

    def impact(self, text: str, event_type: str, sentiment: float, ai_score: float = 0.0) -> float:
        score = 48 + sentiment * 30
        if ai_score > 0:
            score += (ai_score - 5.0) * 4.5
        if any(word in text for word in ["涨停", "封板", "中标", "获批", "超预期"]):
            score += 8
        if event_type in {"政策催化", "订单合作", "业绩财报"}:
            score += 5
        if event_type == "风险事件":
            score -= 15
        return clamp(score)


class QuantEngine:
    def __init__(self) -> None:
        self.universe = StockUniverse()
        self.classifier = EventClassifier()
        self._events_cache_key = ""
        self._events_cache: List[NewsEvent] = []
        self._kline_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._correlation_cache: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
        self._future_return_cache: Dict[Tuple[str, str, int], Optional[Dict[str, Any]]] = {}
        self._kline_row_map_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._intraday_cache: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self._thread_local = threading.local()

    def _sqlite_rows(self, query: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        if not QUANT_DB_FILE.exists():
            return []
        try:
            conn = sqlite3.connect(QUANT_DB_FILE)
            conn.row_factory = sqlite3.Row
            try:
                return [dict(row) for row in conn.execute(query, params).fetchall()]
            finally:
                conn.close()
        except Exception:
            return []

    def clear_intraday_cache(self) -> None:
        self._intraday_cache.clear()

    def clear_market_cache(self) -> None:
        self._kline_cache.clear()
        self._kline_row_map_cache.clear()
        self._future_return_cache.clear()
        self._correlation_cache.clear()
        self._intraday_cache.clear()

    def _source_mtime_key(self) -> str:
        files = [
            DATA_DIR / "news_history.json",
            DATA_DIR / "news_analysis_records.json",
            DATA_DIR / "biying_stock_list.json",
            LHB_HISTORY_FILE,
            QUANT_DB_FILE,
        ]
        parts = []
        for path in files:
            try:
                parts.append(f"{path.name}:{path.stat().st_mtime_ns}")
            except Exception:
                parts.append(f"{path.name}:0")
        return "|".join(parts)

    def load_news_history(self) -> List[Dict[str, Any]]:
        payload = read_json(DATA_DIR / "news_history.json", [])
        rows: List[Dict[str, Any]] = []
        seen = set()

        def add(item: Dict[str, Any]) -> None:
            if not isinstance(item, dict):
                return
            key = str(item.get("id") or item.get("url") or item.get("timestamp") or item.get("text") or "")
            if key and key in seen:
                return
            if key:
                seen.add(key)
            rows.append(item)

        if isinstance(payload, list):
            for item in payload:
                add(item)

        db_rows = self._sqlite_rows(
            """
            SELECT id, date, timestamp, time_str, source, url, text, raw_json
            FROM news_raw
            ORDER BY COALESCE(timestamp, 0) DESC, date DESC
            LIMIT 50000
            """
        )
        for row in db_rows:
            raw_payload: Dict[str, Any] = {}
            try:
                raw = json.loads(str(row.get("raw_json") or "{}"))
                raw_payload = raw if isinstance(raw, dict) else {}
            except Exception:
                raw_payload = {}
            item = {**raw_payload}
            item.update(
                {
                    "id": str(row.get("id") or raw_payload.get("id") or short_hash(str(row.get("text") or ""))),
                    "date": str(row.get("date") or raw_payload.get("date") or "")[:10],
                    "timestamp": int(safe_float(row.get("timestamp"), safe_float(raw_payload.get("timestamp"), 0))),
                    "time_str": str(row.get("time_str") or raw_payload.get("time_str") or ""),
                    "source": str(row.get("source") or raw_payload.get("source") or ""),
                    "url": str(row.get("url") or raw_payload.get("url") or ""),
                    "text": str(row.get("text") or raw_payload.get("text") or ""),
                }
            )
            add(item)
        return rows

    def load_analysis_records(self) -> List[Dict[str, Any]]:
        payload = read_json(DATA_DIR / "news_analysis_records.json", [])
        return payload if isinstance(payload, list) else []

    def load_lhb_records(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 10000), 200000))
        rows: List[Dict[str, Any]] = []
        seen = set()

        def add(row: Dict[str, Any]) -> None:
            if not isinstance(row, dict):
                return
            date = str(row.get("trade_date") or row.get("date") or "").strip()[:10]
            code = digits6(row.get("stock_code") or row.get("code"))
            if not date or not code or is_sample_code(code):
                return
            if start_date and date < start_date:
                return
            if end_date and date > end_date:
                return
            item = {
                "trade_date": date,
                "stock_code": code,
                "stock_name": str(row.get("stock_name") or row.get("name") or self.universe.name(code)).strip(),
                "buyer_seat_name": str(row.get("buyer_seat_name") or row.get("seat_name") or "").strip(),
                "buy_amount": safe_float(row.get("buy_amount"), 0),
                "sell_amount": safe_float(row.get("sell_amount"), 0),
                "hot_money": str(row.get("hot_money") or "").strip(),
            }
            key = (
                item["trade_date"],
                item["stock_code"],
                item["buyer_seat_name"],
                round(item["buy_amount"], 2),
                round(item["sell_amount"], 2),
            )
            if key in seen:
                return
            seen.add(key)
            rows.append(item)

        db_rows = self._sqlite_rows(
            """
            SELECT trade_date, stock_code, stock_name, buyer_seat_name, buy_amount, sell_amount, hot_money
            FROM lhb_records
            WHERE (? = '' OR trade_date >= ?) AND (? = '' OR trade_date <= ?)
            ORDER BY trade_date DESC, stock_code, buy_amount DESC
            LIMIT ?
            """,
            (start_date or "", start_date or "", end_date or "", end_date or "", limit),
        )
        for row in db_rows:
            add(row)

        if LHB_HISTORY_FILE.exists():
            for encoding in ("utf-8-sig", "gb18030"):
                before_count = len(rows)
                try:
                    with LHB_HISTORY_FILE.open("r", encoding=encoding, newline="") as handle:
                        reader = csv.DictReader(handle)
                        for row in reader:
                            add(row)
                    break
                except UnicodeDecodeError:
                    rows = rows[:before_count]
                    continue
                except Exception:
                    break
        rows.sort(key=lambda item: (item["trade_date"], item["stock_code"], item["buy_amount"]), reverse=True)
        return rows[:limit]

    def load_kline(self, code: str) -> List[Dict[str, Any]]:
        code = digits6(code)
        if not code:
            return []
        cached = self._kline_cache.get(code)
        if cached is not None:
            return cached
        clean_rows = []

        def add_row(row: Dict[str, Any]) -> None:
            if not isinstance(row, dict):
                return
            date = str(row.get("date") or "").strip()[:10]
            if not date:
                return
            close = safe_float(row.get("close"), 0)
            open_price = safe_float(row.get("open"), close)
            if close <= 0:
                return
            clean_rows.append(
                {
                    "date": date,
                    "open": open_price if open_price > 0 else close,
                    "close": close,
                    "high": safe_float(row.get("high"), close),
                    "low": safe_float(row.get("low"), close),
                    "volume": safe_float(row.get("volume"), 0),
                    "amount": safe_float(row.get("amount"), 0),
                }
            )

        for row in self._sqlite_rows(
            """
            SELECT date, open, close, high, low, volume, amount
            FROM market_daily_bars
            WHERE code = ?
            ORDER BY date
            """,
            (code,),
        ):
            add_row(row)

        payload = read_json(KLINE_DAY_DIR / f"{code}.json", [])
        rows = payload if isinstance(payload, list) else []
        for row in rows:
            add_row(row)
        by_date = {row["date"]: row for row in clean_rows}
        merged_rows = [by_date[key] for key in sorted(by_date.keys())]
        self._kline_cache[code] = merged_rows
        return merged_rows

    def load_intraday_bars(self, code: str, date: str) -> List[Dict[str, Any]]:
        code = digits6(code)
        date = str(date or "").strip()[:10]
        cache_key = (code, date)
        cached = self._intraday_cache.get(cache_key)
        if cached is not None:
            return cached
        if not code or not date:
            self._intraday_cache[cache_key] = []
            return []
        path = KLINE_MIN_DIR / f"{code}_{date}.csv"
        bars: List[Dict[str, Any]] = []

        def add_bar(row: Dict[str, Any]) -> None:
            dt = parse_time(row.get("time"))
            if not dt:
                return
            open_price = safe_float(row.get("open"), 0)
            close_price = safe_float(row.get("close"), 0)
            if open_price <= 0 or close_price <= 0:
                return
            bars.append(
                {
                    "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "date": dt.strftime("%Y-%m-%d"),
                    "dt": dt,
                    "open": open_price,
                    "close": close_price,
                    "high": safe_float(row.get("high"), max(open_price, close_price)),
                    "low": safe_float(row.get("low"), min(open_price, close_price)),
                    "volume": safe_float(row.get("volume"), 0),
                    "amount": safe_float(row.get("amount"), 0),
                }
            )

        if path.exists():
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        add_bar(row)
            except Exception:
                bars = []

        for row in self._sqlite_rows(
            """
            SELECT time, date, open, close, high, low, volume, amount
            FROM market_minute_bars
            WHERE code = ? AND date = ?
            ORDER BY time
            """,
            (code, date),
        ):
            add_bar(row)

        by_time = {row["time"]: row for row in bars}
        bars = [by_time[key] for key in sorted(by_time.keys())]
        self._intraday_cache[cache_key] = bars
        return bars

    def _available_intraday_dates(self) -> Dict[str, set]:
        out: Dict[str, set] = {}
        if KLINE_MIN_DIR.exists():
            for path in KLINE_MIN_DIR.glob("*.csv"):
                match = re.match(r"^(\d{6})_(\d{4}-\d{2}-\d{2})\.csv$", path.name)
                if not match:
                    continue
                out.setdefault(match.group(2), set()).add(match.group(1))
        for row in self._sqlite_rows("SELECT DISTINCT date, code FROM market_minute_bars WHERE date IS NOT NULL AND code IS NOT NULL"):
            date = str(row.get("date") or "").strip()[:10]
            code = digits6(row.get("code"))
            if date and code:
                out.setdefault(date, set()).add(code)
        return out

    def _first_intraday_bar(self, code: str, date: str) -> Optional[Dict[str, Any]]:
        bars = self.load_intraday_bars(code, date)
        return bars[0] if bars else None

    def _next_intraday_bar_after(self, code: str, date: str, signal_dt: Optional[datetime]) -> Optional[Dict[str, Any]]:
        bars = self.load_intraday_bars(code, date)
        if not bars:
            return None
        if signal_dt is None:
            return bars[0]
        for bar in bars:
            if bar["dt"] > signal_dt:
                return bar
        return None

    def _event_signal_dt(self, event: NewsEvent) -> Optional[datetime]:
        if event.timestamp > 0:
            try:
                return datetime.fromtimestamp(event.timestamp)
            except Exception:
                pass
        return parse_time(event.date)

    def _event_from_stock_result(self, record: Dict[str, Any], stock: Dict[str, Any]) -> Optional[NewsEvent]:
        if not isinstance(stock, dict):
            return None
        code = self.universe.normalize_code(stock.get("code"), stock.get("name"))
        if not self.universe.is_tradeable_a_share(code):
            return None
        news_items = record.get("news_items") if isinstance(record.get("news_items"), list) else []
        first_news = news_items[0] if news_items and isinstance(news_items[0], dict) else {}
        dt = item_datetime(first_news) or item_datetime(record) or datetime.now()
        text_parts = []
        for item in news_items[:3]:
            if isinstance(item, dict):
                text_parts.append(str(item.get("text") or ""))
        reason = str(stock.get("reason") or "").strip()
        text = " ".join(part for part in text_parts if part).strip() or reason
        concept = str(stock.get("concept") or "").strip()
        ai_score = safe_float(stock.get("score"), 0)
        combined_text = f"{concept} {reason} {text}"
        event_type = self.classifier.classify_event_type(combined_text)
        industry = self.classifier.classify_industry(combined_text, concept)
        sentiment = self.classifier.sentiment(combined_text, ai_score=ai_score)
        impact = self.classifier.impact(combined_text, event_type, sentiment, ai_score=ai_score)
        source = str(first_news.get("source") or "AI分析记录")
        identity = f"{record.get('record_key', '')}:{code}:{dt.isoformat()}:{reason[:80]}"
        return NewsEvent(
            event_id=short_hash(identity),
            date=dt.strftime("%Y-%m-%d"),
            timestamp=int(dt.timestamp()),
            source=source,
            text=text[:700],
            code=code,
            name=self.universe.name(code, stock.get("name")),
            industry=industry,
            event_type=event_type,
            sentiment=sentiment,
            impact_score=impact,
            ai_score=ai_score,
            reason=reason or text[:180],
        )

    def _events_from_records(self) -> List[NewsEvent]:
        events: List[NewsEvent] = []
        for record in self.load_analysis_records():
            if not isinstance(record, dict):
                continue
            result = record.get("result") if isinstance(record.get("result"), dict) else {}
            stocks = result.get("stocks") if isinstance(result.get("stocks"), list) else []
            for stock in stocks:
                event = self._event_from_stock_result(record, stock)
                if event:
                    events.append(event)
        return events

    def _events_from_raw_news(self, days: int = 7) -> List[NewsEvent]:
        history = self.load_news_history()
        dated = []
        for item in history:
            if not isinstance(item, dict):
                continue
            dt = item_datetime(item)
            if dt:
                dated.append((dt.strftime("%Y-%m-%d"), dt, item))
        if not dated:
            return []
        latest_date = max(item[0] for item in dated)
        ordered_dates = sorted({item[0] for item in dated})
        allowed_dates = set(ordered_dates[-max(1, days) :])
        allowed_dates.add(latest_date)

        events: List[NewsEvent] = []
        for date, dt, item in dated:
            if date not in allowed_dates:
                continue
            text = str(item.get("text") or "").strip()
            mentions = self.universe.extract_mentions(text, limit=6)
            if not mentions:
                continue
            event_type = self.classifier.classify_event_type(text)
            industry = self.classifier.classify_industry(text)
            sentiment = self.classifier.sentiment(text)
            impact = self.classifier.impact(text, event_type, sentiment)
            source = str(item.get("source") or "新闻")
            for code, name in mentions:
                if not self.universe.is_tradeable_a_share(code):
                    continue
                identity = f"raw:{date}:{code}:{text[:120]}"
                events.append(
                    NewsEvent(
                        event_id=short_hash(identity),
                        date=date,
                        timestamp=int(dt.timestamp()),
                        source=source,
                        text=text[:700],
                        code=code,
                        name=name,
                        industry=industry,
                        event_type=event_type,
                        sentiment=sentiment,
                        impact_score=impact,
                        ai_score=0.0,
                        reason=text[:180],
                    )
                )
        return events

    def _events_from_sqlite(self, limit: int = 200000) -> List[NewsEvent]:
        rows = self._sqlite_rows(
            """
            SELECT event_id, date, timestamp, source, text, code, name, industry, event_type,
                   sentiment, impact_score, ai_score, reason
            FROM news_events
            WHERE code IS NOT NULL AND date IS NOT NULL
            ORDER BY date DESC, COALESCE(timestamp, 0) DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 200000), 500000)),),
        )
        events: List[NewsEvent] = []
        for row in rows:
            code = digits6(row.get("code"))
            date = str(row.get("date") or "").strip()[:10]
            if not code or not date or not self.universe.is_tradeable_a_share(code):
                continue
            timestamp = int(safe_float(row.get("timestamp"), 0))
            if timestamp <= 0:
                dt = parse_time(date) or datetime.now()
                timestamp = int(dt.timestamp())
            event = NewsEvent(
                event_id=str(row.get("event_id") or short_hash(f"sqlite:{date}:{code}:{row.get('text') or row.get('reason') or ''}")),
                date=date,
                timestamp=timestamp,
                source=str(row.get("source") or "sqlite"),
                text=str(row.get("text") or row.get("reason") or "")[:700],
                code=code,
                name=self.universe.name(code, row.get("name")),
                industry=str(row.get("industry") or ""),
                event_type=str(row.get("event_type") or ""),
                sentiment=safe_float(row.get("sentiment"), 0),
                impact_score=clamp(safe_float(row.get("impact_score"), 50)),
                ai_score=safe_float(row.get("ai_score"), 0),
                reason=str(row.get("reason") or row.get("text") or "")[:240],
            )
            if not self._is_sample_event(event):
                events.append(event)
        return events

    def _is_sample_event(self, event: NewsEvent) -> bool:
        if is_sample_code(event.code):
            return True
        return contains_sample_marker(
            {
                "name": event.name,
                "industry": event.industry,
                "event_type": event.event_type,
                "text": event.text,
                "reason": event.reason,
            }
        )

    def _events_from_lhb(self, days: int = 120) -> List[NewsEvent]:
        records = self.load_lhb_records(limit=200000)
        if not records:
            return []
        dates = sorted({str(row.get("trade_date") or "") for row in records if row.get("trade_date")})
        allowed_dates = set(dates[-max(1, days) :])
        grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in records:
            date = str(row.get("trade_date") or "")
            code = digits6(row.get("stock_code"))
            if not date or date not in allowed_dates or not code or not self.universe.is_tradeable_a_share(code):
                continue
            bucket = grouped.setdefault(
                (date, code),
                {
                    "date": date,
                    "code": code,
                    "name": row.get("stock_name") or self.universe.name(code),
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "seats": [],
                    "hot_money": set(),
                },
            )
            buy_amount = safe_float(row.get("buy_amount"), 0)
            sell_amount = safe_float(row.get("sell_amount"), 0)
            seat = str(row.get("buyer_seat_name") or "").strip()
            hot = str(row.get("hot_money") or "").strip()
            bucket["buy_amount"] += buy_amount
            bucket["sell_amount"] += sell_amount
            if seat:
                bucket["seats"].append({"seat": seat, "buy_amount": buy_amount, "sell_amount": sell_amount, "hot_money": hot})
            if hot:
                bucket["hot_money"].add(hot)

        events: List[NewsEvent] = []
        for (date, code), bucket in grouped.items():
            seats = sorted(bucket["seats"], key=lambda item: safe_float(item.get("buy_amount"), 0), reverse=True)
            top_seats = [str(item.get("seat") or "") for item in seats[:3] if item.get("seat")]
            buy_amount = safe_float(bucket.get("buy_amount"), 0)
            sell_amount = safe_float(bucket.get("sell_amount"), 0)
            net_amount = buy_amount - sell_amount
            gross_amount = max(1.0, buy_amount + sell_amount)
            sentiment = clamp((net_amount / gross_amount) * 1.35, -1.0, 1.0)
            hot_labels = sorted(str(item) for item in bucket.get("hot_money", set()) if item)
            hot_boost = 5.0 if hot_labels else 0.0
            impact = clamp(52 + sentiment * 18 + min(abs(net_amount) / 10_000_000, 18) + hot_boost)
            seat_text = "、".join(top_seats[:3]) if top_seats else "席位未披露"
            hot_text = f"；活跃席位标签：{'、'.join(hot_labels[:3])}" if hot_labels else ""
            reason = (
                f"龙虎榜净买入{net_amount / 10000:.1f}万，买入{buy_amount / 10000:.1f}万，"
                f"卖出{sell_amount / 10000:.1f}万；主要席位：{seat_text}{hot_text}"
            )
            dt = parse_time(date) or datetime.now()
            events.append(
                NewsEvent(
                    event_id=short_hash(f"lhb:{date}:{code}:{round(net_amount, 2)}:{seat_text}"),
                    date=date,
                    timestamp=int(dt.timestamp()),
                    source="龙虎榜",
                    text=reason,
                    code=code,
                    name=str(bucket.get("name") or self.universe.name(code)),
                    industry="龙虎榜席位",
                    event_type="龙虎榜席位",
                    sentiment=sentiment,
                    impact_score=impact,
                    ai_score=0.0,
                    reason=reason,
                )
            )
        return events

    def events(self, force: bool = False) -> List[NewsEvent]:
        key = self._source_mtime_key()
        if not force and key == self._events_cache_key and self._events_cache:
            return list(self._events_cache)
        if not force:
            cached_payload = read_json(EVENTS_CACHE_FILE, {})
            if isinstance(cached_payload, dict) and cached_payload.get("source_key") == key:
                cached_events = []
                for item in cached_payload.get("events", []):
                    if not isinstance(item, dict):
                        continue
                    try:
                        event = NewsEvent(**item)
                    except Exception:
                        continue
                    if self._is_sample_event(event):
                        continue
                    cached_events.append(event)
                if cached_events:
                    self._events_cache_key = key
                    self._events_cache = cached_events
                    return list(cached_events)
        seen = set()
        events: List[NewsEvent] = []
        for event in self._events_from_sqlite() + self._events_from_records() + self._events_from_raw_news(days=90) + self._events_from_lhb(days=120):
            if self._is_sample_event(event):
                continue
            dedupe = (event.date, event.code, short_hash(event.text[:120] + event.reason[:80]))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            events.append(event)
        events.sort(key=lambda item: (item.date, item.timestamp, item.code), reverse=True)
        self._events_cache_key = key
        self._events_cache = events
        write_json(
            EVENTS_CACHE_FILE,
            {
                "source_key": key,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "events": [event.compact() for event in events],
            },
        )
        return list(events)

    def latest_event_date(self) -> str:
        events = self.events()
        if events:
            return max(event.date for event in events)
        return datetime.now().strftime("%Y-%m-%d")

    def first_data_date(self) -> str:
        dates = set()
        for query in (
            "SELECT MIN(date) AS date FROM news_events WHERE date IS NOT NULL",
            "SELECT MIN(date) AS date FROM news_raw WHERE date IS NOT NULL",
            "SELECT MIN(date) AS date FROM market_daily_bars WHERE date IS NOT NULL",
            "SELECT MIN(trade_date) AS date FROM lhb_records WHERE trade_date IS NOT NULL",
        ):
            for row in self._sqlite_rows(query):
                date = str(row.get("date") or "").strip()[:10]
                if date:
                    dates.add(date)
        for event in self._events_cache:
            if event.date:
                dates.add(event.date)
        if not dates:
            try:
                for event in self.events():
                    if event.date:
                        dates.add(event.date)
            except Exception:
                pass
        if KLINE_DAY_DIR.exists():
            for path in KLINE_DAY_DIR.glob("*.json"):
                payload = read_json(path, [])
                if isinstance(payload, list):
                    for row in payload[:5]:
                        date = str((row or {}).get("date") or "").strip()[:10] if isinstance(row, dict) else ""
                        if date:
                            dates.add(date)
                            break
        return min(dates) if dates else "2026-03-01"

    def news_feed(
        self,
        as_of: Optional[str] = None,
        limit: int = 120,
        fallback_latest: bool = True,
        source: Optional[str] = None,
        keyword: Optional[str] = None,
        code: Optional[str] = None,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 120), 1000))
        source_filter = {part.strip().lower() for part in str(source or "").split(",") if part.strip()}
        keyword_filter = str(keyword or "").strip().lower()
        code_filter = digits6(code or "")
        rows = []
        for item in self.load_news_history():
            if not isinstance(item, dict):
                continue
            dt = item_datetime(item)
            if not dt:
                continue
            date = dt.strftime("%Y-%m-%d")
            if as_of and date > as_of:
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            source = str(item.get("source") or "未知来源").strip() or "未知来源"
            if source_filter and source.lower() not in source_filter:
                continue
            if keyword_filter:
                haystack = " ".join(
                    [
                        str(item.get("id") or ""),
                        source,
                        str(item.get("title") or ""),
                        text,
                    ]
                ).lower()
                if keyword_filter not in haystack:
                    continue
            if code_filter:
                mentions = self.universe.extract_mentions(text, limit=20)
                mentioned_codes = {digits6(raw_code) for raw_code, _ in mentions}
                if code_filter not in mentioned_codes and code_filter not in text:
                    continue
            timestamp = int(safe_float(item.get("timestamp"), dt.timestamp()))
            rows.append(
                {
                    "id": str(item.get("id") or short_hash(f"{timestamp}|{source}|{text}")),
                    "date": date,
                    "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "source": source,
                    "text": text,
                    "timestamp": timestamp,
                }
            )

        rows.sort(key=lambda item: (item["timestamp"], item["time"]), reverse=True)
        available_dates = sorted({item["date"] for item in rows}, reverse=True)
        requested_date = as_of or (available_dates[0] if available_dates else datetime.now().strftime("%Y-%m-%d"))
        exact_rows = [item for item in rows if item["date"] == requested_date]
        data_date = requested_date if exact_rows else ""
        selected = exact_rows
        has_requested_date_data = bool(exact_rows)
        if not selected and fallback_latest:
            fallback_dates = [date for date in available_dates if not requested_date or date <= requested_date]
            data_date = fallback_dates[0] if fallback_dates else (available_dates[0] if available_dates else "")
            selected = [item for item in rows if item["date"] == data_date] if data_date else rows

        event_items = []
        if data_date:
            event_items = [
                event.compact()
                for event in sorted(
                    [event for event in self.events() if event.date == data_date],
                    key=lambda event: (event.timestamp, event.impact_score),
                    reverse=True,
                )
                if not self._is_sample_event(event)
            ][:limit]

        return {
            "status": "ok",
            "requested_date": requested_date,
            "data_date": data_date,
            "latest_available_date": available_dates[0] if available_dates else "",
            "has_requested_date_data": has_requested_date_data,
            "count": len(selected),
            "items": selected[:limit],
            "events": event_items,
            "available_dates": available_dates[:60],
            "filters": {
                "source": sorted(source_filter),
                "keyword": keyword_filter,
                "code": code_filter,
            },
        }

    def latest_price(self, code: str, as_of: Optional[str] = None) -> Optional[Dict[str, Any]]:
        code = digits6(code)
        if as_of:
            bars = self.load_intraday_bars(code, as_of)
            if bars:
                latest_bar = bars[-1]
                return {
                    "date": latest_bar.get("date", as_of),
                    "time": latest_bar.get("time", ""),
                    "open": latest_bar.get("open", 0),
                    "close": latest_bar.get("close", 0),
                    "high": latest_bar.get("high", 0),
                    "low": latest_bar.get("low", 0),
                    "volume": latest_bar.get("volume", 0),
                    "source": "intraday",
                }
        rows = self.load_kline(code)
        if as_of:
            rows = [row for row in rows if row["date"] <= as_of]
        if not rows:
            return None
        return {**rows[-1], "source": "daily"}

    def future_return(self, code: str, event_date: str, hold_days: int = 3) -> Optional[Dict[str, Any]]:
        code = digits6(code)
        cache_key = (code, event_date, int(hold_days))
        if cache_key in self._future_return_cache:
            cached = self._future_return_cache[cache_key]
            return dict(cached) if isinstance(cached, dict) else cached
        rows = self.load_kline(code)
        if not rows:
            self._future_return_cache[cache_key] = None
            return None
        start_idx = None
        for idx, row in enumerate(rows):
            if row["date"] > event_date:
                start_idx = idx
                break
        if start_idx is None:
            self._future_return_cache[cache_key] = None
            return None
        exit_idx = start_idx + max(1, hold_days) - 1
        if exit_idx >= len(rows):
            self._future_return_cache[cache_key] = None
            return None
        entry = rows[start_idx]
        exit_row = rows[exit_idx]
        entry_price = safe_float(entry.get("open") or entry.get("close"), 0)
        exit_price = safe_float(exit_row.get("close"), 0)
        if entry_price <= 0 or exit_price <= 0:
            self._future_return_cache[cache_key] = None
            return None
        payload = {
            "entry_date": entry["date"],
            "exit_date": exit_row["date"],
            "entry_price": round(entry_price, 3),
            "exit_price": round(exit_price, 3),
            "return_pct": round((exit_price / entry_price - 1) * 100, 3),
        }
        if len(self._future_return_cache) > 20000:
            self._future_return_cache.clear()
        self._future_return_cache[cache_key] = payload
        return dict(payload)

    def ensure_daily_kline_for_events(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        hold_days: int = 3,
        max_codes: int = 300,
        force: bool = False,
    ) -> Dict[str, Any]:
        events = self.events()
        if start_date:
            events = [event for event in events if event.date >= start_date]
        if end_date:
            events = [event for event in events if event.date <= end_date]
        events = [event for event in events if self.universe.is_tradeable_a_share(event.code) and not self._is_sample_event(event)]
        if not events:
            return {
                "status": "ok",
                "message": "没有可用于补齐K线的新闻事件",
                "requested": 0,
                "fetched": 0,
                "added_rows": 0,
                "updated_rows": 0,
            }
        start = start_date or min(event.date for event in events)
        end = end_date or max(event.date for event in events)
        try:
            end_dt = datetime.strptime(end[:10], "%Y-%m-%d") + timedelta(days=max(3, int(hold_days or 3) * 3))
            fetch_end = end_dt.strftime("%Y-%m-%d")
        except Exception:
            fetch_end = end

        best_by_code: Dict[str, NewsEvent] = {}
        for event in events:
            old = best_by_code.get(event.code)
            if old is None or (event.date, event.impact_score, event.timestamp) > (old.date, old.impact_score, old.timestamp):
                best_by_code[event.code] = event
        ranked_codes = [
            code
            for code, _event in sorted(
                best_by_code.items(),
                key=lambda item: (item[1].impact_score, item[1].date, item[1].timestamp),
                reverse=True,
            )
        ][: max(1, min(int(max_codes or 300), 5000))]

        from app.quant.market_data import sync_daily_for_codes

        result = sync_daily_for_codes(
            ranked_codes,
            start_date=start,
            end_date=fetch_end,
            max_codes=max_codes,
            force=force,
        )
        if result.get("fetched") or result.get("added_rows") or result.get("updated_rows"):
            self.clear_market_cache()
        result["event_start_date"] = start
        result["event_end_date"] = end
        result["fetch_end_date"] = fetch_end
        return result

    def technical_profile(self, code: str, as_of: Optional[str] = None) -> Dict[str, Any]:
        rows = self.load_kline(code)
        if as_of:
            rows = [row for row in rows if row["date"] <= as_of]
        if len(rows) < 2:
            return {
                "score": 45.0,
                "risk": 55.0,
                "latest_close": 0.0,
                "latest_date": "",
                "ret_3d": 0.0,
                "ret_5d": 0.0,
                "ret_20d": 0.0,
                "volume_ratio": 1.0,
                "volatility": 0.0,
            }
        closes = [safe_float(row["close"], 0) for row in rows if safe_float(row["close"], 0) > 0]
        volumes = [safe_float(row.get("volume"), 0) for row in rows[-20:]]
        latest = rows[-1]

        def ret(days: int) -> float:
            if len(closes) <= days or closes[-days - 1] <= 0:
                return 0.0
            return closes[-1] / closes[-days - 1] - 1

        daily_returns = []
        for idx in range(max(1, len(closes) - 20), len(closes)):
            prev = closes[idx - 1]
            if prev > 0:
                daily_returns.append(closes[idx] / prev - 1)
        volatility = statistics.pstdev(daily_returns) if len(daily_returns) > 2 else 0.0
        avg_volume = statistics.mean(volumes[:-1]) if len(volumes) > 2 else (volumes[-1] if volumes else 1)
        volume_ratio = (volumes[-1] / avg_volume) if avg_volume > 0 and volumes else 1.0
        window_high = max(closes[-20:]) if len(closes) >= 20 else max(closes)
        drawdown = closes[-1] / window_high - 1 if window_high > 0 else 0.0

        ret_3d = ret(3)
        ret_5d = ret(5)
        ret_20d = ret(20)
        score = 50 + ret_3d * 450 + ret_5d * 260 + ret_20d * 80 + (volume_ratio - 1) * 8
        if drawdown < -0.08:
            score -= 10
        if volatility > 0.045:
            score -= (volatility - 0.045) * 220
        risk = 40 + volatility * 520 + max(0.0, -drawdown) * 120 + max(0.0, ret_5d - 0.12) * 120
        return {
            "score": round(clamp(score), 2),
            "risk": round(clamp(risk), 2),
            "latest_close": round(safe_float(latest["close"], 0), 3),
            "latest_date": latest["date"],
            "ret_3d": round(ret_3d * 100, 3),
            "ret_5d": round(ret_5d * 100, 3),
            "ret_20d": round(ret_20d * 100, 3),
            "volume_ratio": round(volume_ratio, 3),
            "volatility": round(volatility * 100, 3),
        }

    def _aggregate_stats(self, returns: List[float]) -> Dict[str, Any]:
        if not returns:
            return {"samples": 0, "avg_return_pct": 0.0, "win_rate": 0.0, "confidence": 0.0}
        wins = [ret for ret in returns if ret > 0]
        return {
            "samples": len(returns),
            "avg_return_pct": round(statistics.mean(returns), 3),
            "median_return_pct": round(statistics.median(returns), 3),
            "win_rate": round(len(wins) / len(returns) * 100, 2),
            "confidence": round(min(1.0, math.log(len(returns) + 1, 20)), 3),
        }

    def correlation(
        self,
        as_of: Optional[str] = None,
        hold_days: int = 3,
        realized_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        realized_by = realized_by or as_of
        cache_key = (as_of, int(hold_days), realized_by)
        cached = self._correlation_cache.get(cache_key)
        if cached is not None:
            return cached
        by_code: Dict[str, List[float]] = {}
        by_theme: Dict[str, List[float]] = {}
        by_type: Dict[str, List[float]] = {}
        all_returns: List[float] = []
        for event in self.events():
            if self._is_sample_event(event):
                continue
            if event.date >= as_of:
                continue
            realized = self.future_return(event.code, event.date, hold_days=hold_days)
            if not realized:
                continue
            if realized.get("exit_date", "") > realized_by:
                continue
            ret = safe_float(realized["return_pct"], 0)
            all_returns.append(ret)
            by_code.setdefault(event.code, []).append(ret)
            by_theme.setdefault(f"{event.industry}|{event.event_type}", []).append(ret)
            by_type.setdefault(event.event_type, []).append(ret)
        payload = {
            "as_of": as_of,
            "realized_by": realized_by,
            "hold_days": hold_days,
            "global": self._aggregate_stats(all_returns),
            "by_code": {key: self._aggregate_stats(val) for key, val in by_code.items() if len(val) >= 2},
            "by_theme": {key: self._aggregate_stats(val) for key, val in by_theme.items() if len(val) >= 3},
            "by_type": {key: self._aggregate_stats(val) for key, val in by_type.items() if len(val) >= 3},
        }
        if len(self._correlation_cache) > 200:
            self._correlation_cache.clear()
        self._correlation_cache[cache_key] = payload
        return payload

    def _load_state(self) -> Dict[str, Any]:
        payload = read_json(STATE_FILE, {})
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("positions", [])
        payload.setdefault("trades", [])
        payload.setdefault(
            "model_weights",
            {"sentiment": 0.35, "event": 0.25, "technical": 0.25, "risk": 0.15},
        )
        raw_strategy_params = payload.get("strategy_params") if isinstance(payload.get("strategy_params"), dict) else {}
        has_configured_initial_cash = "account_initial_cash" in raw_strategy_params
        if "strategy_params" not in payload:
            weights = payload.get("model_weights") if isinstance(payload.get("model_weights"), dict) else {}
            payload["strategy_params"] = {
                **DEFAULT_STRATEGY_PARAMS,
                "sentiment_weight": safe_float(weights.get("sentiment"), DEFAULT_STRATEGY_PARAMS["sentiment_weight"]),
                "event_weight": safe_float(weights.get("event"), DEFAULT_STRATEGY_PARAMS["event_weight"]),
                "technical_weight": safe_float(weights.get("technical"), DEFAULT_STRATEGY_PARAMS["technical_weight"]),
                "risk_weight": safe_float(weights.get("risk"), DEFAULT_STRATEGY_PARAMS["risk_weight"]),
            }
        payload["strategy_params"] = self._normalize_strategy_params(payload.get("strategy_params"))
        default_cash = payload["strategy_params"]["account_initial_cash"]
        positions = payload.get("positions") if isinstance(payload.get("positions"), list) else []
        trades = payload.get("trades") if isinstance(payload.get("trades"), list) else []
        filtered_positions = [pos for pos in positions if not contains_sample_marker(pos)]
        filtered_trades = [trade for trade in trades if not contains_sample_marker(trade)]
        if len(filtered_positions) != len(positions) or len(filtered_trades) != len(trades):
            payload["positions"] = filtered_positions
            payload["trades"] = filtered_trades
            positions = filtered_positions
            trades = filtered_trades
            payload["sample_state_filtered_at"] = datetime.now().isoformat(timespec="seconds")
        legacy_initial_cash = 1_000_000.0
        stored_initial_cash = safe_float(payload.get("initial_cash"), 0)
        stored_cash = safe_float(payload.get("cash"), 0)
        if (
            not has_configured_initial_cash
            and not positions
            and not trades
            and (stored_initial_cash <= 0 or abs(stored_initial_cash - legacy_initial_cash) < 0.01)
            and (stored_cash <= 0 or abs(stored_cash - legacy_initial_cash) < 0.01)
        ):
            payload["initial_cash"] = default_cash
            payload["cash"] = default_cash
            return payload
        initial_cash = safe_float(payload.get("initial_cash"), default_cash)
        if initial_cash <= 0:
            initial_cash = default_cash
        payload["initial_cash"] = initial_cash
        payload.setdefault("cash", initial_cash)
        return payload

    def _save_state(self, state: Dict[str, Any]) -> None:
        write_json(STATE_FILE, state)

    def _normalize_strategy_params(self, raw: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        raw = raw if isinstance(raw, dict) else {}
        params = {
            key: safe_float(raw.get(key), default)
            for key, default in DEFAULT_STRATEGY_PARAMS.items()
        }
        for key in ("sentiment_weight", "event_weight", "technical_weight", "risk_weight"):
            params[key] = max(0.0, params[key])
        weight_total = sum(params[key] for key in ("sentiment_weight", "event_weight", "technical_weight", "risk_weight")) or 1.0
        for key in ("sentiment_weight", "event_weight", "technical_weight", "risk_weight"):
            params[key] = round(params[key] / weight_total, 4)
        params["buy_threshold"] = clamp(params["buy_threshold"], 40, 95)
        params["watch_threshold"] = clamp(params["watch_threshold"], 30, params["buy_threshold"])
        params["avoid_sell_threshold"] = clamp(params["avoid_sell_threshold"], 40, 95)
        params["avoid_buy_ceiling"] = clamp(params["avoid_buy_ceiling"], 30, 90)
        params["sell_score_threshold"] = clamp(params["sell_score_threshold"], 40, 98)
        params["stop_loss_pct"] = max(-30.0, min(-1.0, params["stop_loss_pct"]))
        params["take_profit_pct"] = clamp(params["take_profit_pct"], 1, 40)
        params["max_hold_days"] = max(1.0, min(30.0, params["max_hold_days"]))
        params["paper_max_hold_days"] = max(1.0, min(60.0, params["paper_max_hold_days"]))
        params["max_positions"] = max(1.0, min(20.0, params["max_positions"]))
        params["top_n"] = max(1.0, min(50.0, params["top_n"]))
        params["account_initial_cash"] = max(10000.0, min(10_000_000.0, params["account_initial_cash"]))
        params["paper_position_value"] = max(5000.0, min(2_000_000.0, params["paper_position_value"]))
        params["sentiment_coef"] = clamp(params["sentiment_coef"], 0, 80)
        params["ai_score_coef"] = clamp(params["ai_score_coef"], 0, 20)
        params["event_impact_weight"] = clamp(params["event_impact_weight"], 0, 1)
        params["history_score_weight"] = clamp(params["history_score_weight"], 0, 1)
        params["history_return_coef"] = clamp(params["history_return_coef"], 0, 1000)
        params["history_win_coef"] = clamp(params["history_win_coef"], 0, 120)
        params["sell_negative_sentiment_coef"] = clamp(params["sell_negative_sentiment_coef"], 0, 80)
        params["sell_technical_risk_coef"] = clamp(params["sell_technical_risk_coef"], 0, 2)
        combo = params["event_impact_weight"] + params["history_score_weight"]
        if combo <= 0:
            params["event_impact_weight"] = DEFAULT_STRATEGY_PARAMS["event_impact_weight"]
            params["history_score_weight"] = DEFAULT_STRATEGY_PARAMS["history_score_weight"]
        else:
            params["event_impact_weight"] = round(params["event_impact_weight"] / combo, 4)
            params["history_score_weight"] = round(params["history_score_weight"] / combo, 4)
        params["negative_sentiment_risk_penalty"] = clamp(params["negative_sentiment_risk_penalty"], 0, 60)
        params["risk_event_penalty"] = clamp(params["risk_event_penalty"], 0, 80)
        return params

    def strategy_params(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        state = self._load_state()
        raw = state.get("strategy_params") if isinstance(state.get("strategy_params"), dict) else {}
        merged = {**DEFAULT_STRATEGY_PARAMS, **raw}
        thread_override = getattr(self._thread_local, "strategy_params_override", None)
        if isinstance(thread_override, dict):
            merged.update(thread_override)
        if overrides:
            merged.update(overrides)
        return self._normalize_strategy_params(merged)

    @contextlib.contextmanager
    def temporary_strategy_params(self, params: Dict[str, Any]):
        old = getattr(self._thread_local, "strategy_params_override", None)
        self._thread_local.strategy_params_override = self._normalize_strategy_params(params)
        try:
            yield
        finally:
            if old is None:
                try:
                    delattr(self._thread_local, "strategy_params_override")
                except AttributeError:
                    pass
            else:
                self._thread_local.strategy_params_override = old

    def update_strategy_params(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        state = self._load_state()
        old_initial_cash = safe_float(state.get("initial_cash"), DEFAULT_STRATEGY_PARAMS["account_initial_cash"])
        old_cash = safe_float(state.get("cash"), old_initial_cash)
        params = self.strategy_params(updates)
        state["strategy_params"] = params
        if isinstance(updates, dict) and "account_initial_cash" in updates:
            new_initial_cash = params["account_initial_cash"]
            state["initial_cash"] = new_initial_cash
            state["cash"] = round(max(0.0, old_cash + new_initial_cash - old_initial_cash), 2)
        state["model_weights"] = {
            "sentiment": params["sentiment_weight"],
            "event": params["event_weight"],
            "technical": params["technical_weight"],
            "risk": params["risk_weight"],
        }
        state["strategy_updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_state(state)
        return {"status": "ok", "strategy_params": params, "updated_at": state["strategy_updated_at"]}

    def reset_strategy_params(self) -> Dict[str, Any]:
        state = self._load_state()
        old_initial_cash = safe_float(state.get("initial_cash"), DEFAULT_STRATEGY_PARAMS["account_initial_cash"])
        old_cash = safe_float(state.get("cash"), old_initial_cash)
        params = self._normalize_strategy_params(DEFAULT_STRATEGY_PARAMS)
        state["strategy_params"] = params
        state["initial_cash"] = params["account_initial_cash"]
        state["cash"] = round(max(0.0, old_cash + params["account_initial_cash"] - old_initial_cash), 2)
        state["model_weights"] = {
            "sentiment": params["sentiment_weight"],
            "event": params["event_weight"],
            "technical": params["technical_weight"],
            "risk": params["risk_weight"],
        }
        state["strategy_updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_state(state)
        return {"status": "ok", "strategy_params": params, "updated_at": state["strategy_updated_at"]}

    def model_weights(self) -> Dict[str, float]:
        params = self.strategy_params()
        return {
            "sentiment": params["sentiment_weight"],
            "event": params["event_weight"],
            "technical": params["technical_weight"],
            "risk": params["risk_weight"],
        }

    def calibrate_model(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        backtest = self.backtest(as_of=as_of, hold_days=3, top_n=5)
        bucket = backtest.get("score_buckets", {}).get("80-100", {})
        avg_ret = safe_float(bucket.get("avg_return_pct"), 0)
        win_rate = safe_float(bucket.get("win_rate"), 0)
        weights = self.model_weights()
        if bucket.get("samples", 0) >= 5:
            if avg_ret < 0 or win_rate < 45:
                weights["risk"] += 0.04
                weights["sentiment"] -= 0.02
                weights["event"] -= 0.02
            elif avg_ret > 1.2 and win_rate > 55:
                weights["event"] += 0.03
                weights["sentiment"] += 0.01
                weights["risk"] -= 0.04
        total = sum(max(0.02, val) for val in weights.values())
        weights = {key: round(max(0.02, val) / total, 4) for key, val in weights.items()}
        state = self._load_state()
        state["model_weights"] = weights
        strategy_params = self.strategy_params()
        strategy_params.update(
            {
                "sentiment_weight": weights["sentiment"],
                "event_weight": weights["event"],
                "technical_weight": weights["technical"],
                "risk_weight": weights["risk"],
            }
        )
        state["strategy_params"] = self._normalize_strategy_params(strategy_params)
        state["last_calibration"] = {
            "as_of": as_of or self.latest_event_date(),
            "top_bucket_avg_return_pct": avg_ret,
            "top_bucket_win_rate": win_rate,
            "backtest_trades": backtest.get("trades", 0),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_state(state)
        return {**state["last_calibration"], "model_weights": weights}

    def _historical_score(self, event: NewsEvent, corr: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        code_stats = corr.get("by_code", {}).get(event.code)
        theme_stats = corr.get("by_theme", {}).get(f"{event.industry}|{event.event_type}")
        type_stats = corr.get("by_type", {}).get(event.event_type)
        stats = code_stats or theme_stats or type_stats or corr.get("global", {})
        avg_ret = safe_float(stats.get("avg_return_pct"), 0) / 100.0
        win_rate = safe_float(stats.get("win_rate"), 50) / 100.0
        confidence = safe_float(stats.get("confidence"), 0)
        params = self.strategy_params()
        score = 50 + avg_ret * safe_float(params.get("history_return_coef"), 420) + (win_rate - 0.5) * safe_float(params.get("history_win_coef"), 45)
        score = 50 * (1 - confidence) + score * confidence
        return clamp(score), stats

    def _agent_scores(self, event_bundle: Dict[str, Any], corr: Dict[str, Any], as_of: str) -> Dict[str, Any]:
        events: List[NewsEvent] = event_bundle["events"]
        main_event = max(events, key=lambda item: item.impact_score)
        avg_sentiment = statistics.mean(event.sentiment for event in events)
        max_ai_score = max((event.ai_score for event in events), default=0.0)
        avg_impact = statistics.mean(event.impact_score for event in events)
        technical = self.technical_profile(main_event.code, as_of=as_of)
        hist_score, hist_stats = self._historical_score(main_event, corr)
        params = self.strategy_params()

        sentiment_score = 50 + avg_sentiment * params["sentiment_coef"] + max(0.0, max_ai_score - 5) * params["ai_score_coef"]
        event_score = avg_impact * params["event_impact_weight"] + hist_score * params["history_score_weight"]
        technical_score = safe_float(technical.get("score"), 50)
        risk_score = 100 - safe_float(technical.get("risk"), 50)
        if avg_sentiment < -0.2:
            risk_score -= params["negative_sentiment_risk_penalty"]
        if main_event.event_type == "风险事件":
            risk_score -= params["risk_event_penalty"]
        risk_score = clamp(risk_score)
        weights = self.model_weights()
        buy_score = (
            clamp(sentiment_score) * weights["sentiment"]
            + clamp(event_score) * weights["event"]
            + clamp(technical_score) * weights["technical"]
            + risk_score * weights["risk"]
        )
        sell_score = clamp(
            100 - buy_score
            + max(0.0, -avg_sentiment) * params["sell_negative_sentiment_coef"]
            + max(0.0, safe_float(technical.get("risk"), 50) - 65) * params["sell_technical_risk_coef"]
        )
        agents = [
            {
                "agent": "新闻情绪Agent",
                "score": round(clamp(sentiment_score), 2),
                "vote": "多" if sentiment_score >= 60 else ("空" if sentiment_score <= 42 else "中性"),
                "rationale": f"情绪={avg_sentiment:.2f}, AI最高分={max_ai_score:.1f}",
            },
            {
                "agent": "事件影响Agent",
                "score": round(clamp(event_score), 2),
                "vote": "多" if event_score >= 62 else ("空" if event_score <= 42 else "中性"),
                "rationale": f"{main_event.event_type}/{main_event.industry}, 历史样本={hist_stats.get('samples', 0)}",
            },
            {
                "agent": "技术走势Agent",
                "score": round(clamp(technical_score), 2),
                "vote": "多" if technical_score >= 62 else ("空" if technical_score <= 42 else "中性"),
                "rationale": f"3日={technical.get('ret_3d')}%, 5日={technical.get('ret_5d')}%, 量比={technical.get('volume_ratio')}",
            },
            {
                "agent": "风控Agent",
                "score": round(risk_score, 2),
                "vote": "可交易" if risk_score >= 55 else "降权",
                "rationale": f"波动={technical.get('volatility')}%, 风险={technical.get('risk')}",
            },
        ]
        return {
            "buy_score": round(clamp(buy_score), 2),
            "sell_score": round(sell_score, 2),
            "agents": agents,
            "technical": technical,
            "historical": hist_stats,
            "weights": weights,
            "components": {
                "sentiment_score": round(clamp(sentiment_score), 2),
                "event_score": round(clamp(event_score), 2),
                "technical_score": round(clamp(technical_score), 2),
                "risk_score": round(risk_score, 2),
                "avg_sentiment": round(avg_sentiment, 4),
                "avg_impact": round(avg_impact, 2),
                "max_ai_score": round(max_ai_score, 2),
            },
            "strategy_params": params,
        }

    def recommendations(self, as_of: Optional[str] = None, lookback_days: int = 2, top_n: int = 30) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        params = self.strategy_params()
        all_dates = sorted({event.date for event in self.events() if event.date <= as_of})
        if not all_dates:
            return {"as_of": as_of, "items": [], "latest_events": [], "model_weights": self.model_weights(), "strategy_params": params}
        selected_dates = set(all_dates[-max(1, lookback_days) :])
        selected = [event for event in self.events() if event.date in selected_dates and event.date <= as_of]
        grouped: Dict[str, Dict[str, Any]] = {}
        for event in selected:
            if self._is_sample_event(event):
                continue
            if not self.universe.is_tradeable_a_share(event.code):
                continue
            grouped.setdefault(event.code, {"events": []})["events"].append(event)
        corr = self.correlation(as_of=as_of, hold_days=3, realized_by=as_of)
        items = []
        for code, bundle in grouped.items():
            scores = self._agent_scores(bundle, corr, as_of)
            events = sorted(bundle["events"], key=lambda item: item.impact_score, reverse=True)
            primary = events[0]
            buy_score = safe_float(scores.get("buy_score"), 0)
            sell_score = safe_float(scores.get("sell_score"), 0)
            action = "买入候选" if buy_score >= params["buy_threshold"] else ("重点观察" if buy_score >= params["watch_threshold"] else "暂不买入")
            if sell_score >= params["avoid_sell_threshold"] and buy_score < params["avoid_buy_ceiling"]:
                action = "回避/卖出"
            items.append(
                {
                    "code": code,
                    "name": self.universe.name(code, primary.name),
                    "action": action,
                    "buy_score": round(buy_score, 2),
                    "sell_score": round(sell_score, 2),
                    "short_term_direction": "up" if buy_score >= sell_score else "down",
                    "industry": primary.industry,
                    "event_type": primary.event_type,
                    "event_count": len(events),
                    "latest_event_date": max(event.date for event in events),
                    "impact_score": round(statistics.mean(event.impact_score for event in events), 2),
                    "sentiment": round(statistics.mean(event.sentiment for event in events), 3),
                    "reason": primary.reason,
                    "agents": scores["agents"],
                    "components": scores.get("components", {}),
                    "technical": scores["technical"],
                    "historical": scores["historical"],
                    "events": [event.compact() for event in events[:4]],
                }
            )
        items.sort(key=lambda item: (item["buy_score"], -item["sell_score"], item["impact_score"]), reverse=True)
        latest_events = [event.compact() for event in selected if not self._is_sample_event(event)][:60]
        return {
            "as_of": as_of,
            "lookback_days": lookback_days,
            "top_n": top_n,
            "items": items[:top_n],
            "latest_events": latest_events,
            "correlation": corr.get("global", {}),
            "model_weights": self.model_weights(),
            "strategy_params": params,
        }

    def _all_trading_dates_for_codes(self, codes: List[str]) -> List[str]:
        dates = set()
        for code in codes:
            for row in self.load_kline(code):
                date = str(row.get("date") or "")
                if date:
                    dates.add(date)
        available_intraday = self._available_intraday_dates()
        code_set = {digits6(code) for code in codes}
        for date, date_codes in available_intraday.items():
            if code_set.intersection(date_codes):
                dates.add(date)
        return sorted(dates)

    def _row_on_date(self, code: str, date: str) -> Optional[Dict[str, Any]]:
        code = digits6(code)
        if not code:
            return None
        row_map = self._kline_row_map_cache.get(code)
        if row_map is None:
            row_map = {row["date"]: row for row in self.load_kline(code)}
            self._kline_row_map_cache[code] = row_map
        return row_map.get(date)

    def _next_trading_date(self, dates: List[str], current_date: str) -> str:
        for date in dates:
            if date > current_date:
                return date
        return ""

    def _performance_metrics(
        self,
        equity_curve: List[Dict[str, Any]],
        trades: List[Dict[str, Any]],
        initial_cash: float,
        final_value: float,
    ) -> Dict[str, Any]:
        initial_cash = max(1.0, safe_float(initial_cash, 1.0))
        final_value = max(0.0, safe_float(final_value, initial_cash))
        values = [safe_float(point.get("total_value"), 0) for point in equity_curve if safe_float(point.get("total_value"), 0) > 0]
        daily_returns = []
        previous = initial_cash
        for value in values:
            daily_returns.append((value / previous - 1.0) if previous > 0 else 0.0)
            previous = value

        total_return_pct = (final_value / initial_cash - 1.0) * 100
        trading_days = len(values)
        annualized_return_pct = ((final_value / initial_cash) ** (252 / trading_days) - 1.0) * 100 if trading_days > 0 and final_value > 0 else 0.0
        volatility_pct = statistics.stdev(daily_returns) * math.sqrt(252) * 100 if len(daily_returns) >= 2 else 0.0
        sharpe_ratio = (
            statistics.mean(daily_returns) / statistics.stdev(daily_returns) * math.sqrt(252)
            if len(daily_returns) >= 2 and statistics.stdev(daily_returns) > 0
            else 0.0
        )

        peak = initial_cash
        max_drawdown = 0.0
        drawdown_start = ""
        drawdown_end = ""
        current_peak_date = ""
        for point in equity_curve:
            date = str(point.get("date") or "")
            value = safe_float(point.get("total_value"), initial_cash)
            if value >= peak:
                peak = value
                current_peak_date = date
            drawdown = value / peak - 1.0 if peak > 0 else 0.0
            if drawdown < max_drawdown:
                max_drawdown = drawdown
                drawdown_start = current_peak_date
                drawdown_end = date

        sell_trades = [trade for trade in trades if str(trade.get("side") or "").upper() == "SELL"]
        buy_trades = [trade for trade in trades if str(trade.get("side") or "").upper() == "BUY"]
        sell_returns = [safe_float(trade.get("pnl_pct"), 0) for trade in sell_trades]
        wins = [ret for ret in sell_returns if ret > 0]
        losses = [ret for ret in sell_returns if ret <= 0]
        gross_profit = sum(max(0.0, safe_float(trade.get("net_amount"), 0) - safe_float(trade.get("amount"), 0)) for trade in sell_trades)
        if gross_profit <= 0:
            gross_profit = sum(ret for ret in wins)
        gross_loss = abs(sum(min(0.0, ret) for ret in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

        max_consecutive_losses = 0
        current_losses = 0
        for ret in sell_returns:
            if ret <= 0:
                current_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, current_losses)
            else:
                current_losses = 0

        total_fees = sum(safe_float(trade.get("total_fee"), 0) for trade in trades)
        turnover_amount = sum(safe_float(trade.get("amount"), 0) for trade in trades)
        exposure_days = sum(1 for point in equity_curve if safe_float(point.get("position_count"), 0) > 0)
        avg_position_count = statistics.mean(safe_float(point.get("position_count"), 0) for point in equity_curve) if equity_curve else 0.0

        return {
            "trading_days": trading_days,
            "total_return_pct": round(total_return_pct, 3),
            "annualized_return_pct": round(annualized_return_pct, 3),
            "volatility_pct": round(volatility_pct, 3),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "max_drawdown_pct": round(max_drawdown * 100, 3),
            "max_drawdown_start": drawdown_start,
            "max_drawdown_end": drawdown_end,
            "exposure_pct": round(exposure_days / trading_days * 100, 2) if trading_days else 0.0,
            "avg_position_count": round(avg_position_count, 3),
            "buy_trades": len(buy_trades),
            "sell_trades": len(sell_trades),
            "closed_trades": len(sell_trades),
            "win_rate": round(len(wins) / len(sell_returns) * 100, 2) if sell_returns else 0.0,
            "avg_trade_return_pct": round(statistics.mean(sell_returns), 3) if sell_returns else 0.0,
            "median_trade_return_pct": round(statistics.median(sell_returns), 3) if sell_returns else 0.0,
            "avg_win_pct": round(statistics.mean(wins), 3) if wins else 0.0,
            "avg_loss_pct": round(statistics.mean(losses), 3) if losses else 0.0,
            "best_trade_pct": round(max(sell_returns), 3) if sell_returns else 0.0,
            "worst_trade_pct": round(min(sell_returns), 3) if sell_returns else 0.0,
            "profit_factor": round(profit_factor, 4),
            "expectancy_pct": round(statistics.mean(sell_returns), 3) if sell_returns else 0.0,
            "max_consecutive_losses": max_consecutive_losses,
            "total_fees": round(total_fees, 2),
            "turnover_amount": round(turnover_amount, 2),
            "turnover_ratio": round(turnover_amount / initial_cash, 4),
        }

    def walk_forward(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: Optional[float] = None,
        max_positions: Optional[int] = None,
        hold_days: Optional[int] = None,
        top_n: Optional[int] = None,
        auto_fill: bool = False,
    ) -> Dict[str, Any]:
        params = self.strategy_params()
        initial_cash = max(1.0, safe_float(initial_cash, params["account_initial_cash"]))
        max_positions = int(max_positions or params["max_positions"])
        hold_days = int(hold_days or params["max_hold_days"])
        top_n = int(top_n or params["top_n"])
        all_events = self.events()
        all_events = [event for event in all_events if not self._is_sample_event(event)]
        if end_date:
            all_events = [event for event in all_events if event.date <= end_date]
        if start_date:
            all_events = [event for event in all_events if event.date >= start_date]
        if not all_events:
            return {
                "start_date": start_date,
                "end_date": end_date,
                "initial_cash": initial_cash,
                "final_value": initial_cash,
                "return_pct": 0.0,
                "trades": [],
                "days": [],
                "equity_curve": [],
            }

        start_date = start_date or min(event.date for event in all_events)
        end_date = end_date or max(event.date for event in all_events)
        auto_fill_result: Dict[str, Any] = {}
        if auto_fill:
            auto_fill_result = self.ensure_daily_kline_for_events(
                start_date=start_date,
                end_date=end_date,
                hold_days=hold_days,
                max_codes=500,
                force=False,
            )
        codes = sorted({event.code for event in all_events})
        trading_dates = [
            date
            for date in self._all_trading_dates_for_codes(codes)
            if start_date <= date <= end_date
        ]
        if not trading_dates:
            return {
                "start_date": start_date,
                "end_date": end_date,
                "initial_cash": initial_cash,
                "final_value": initial_cash,
                "return_pct": 0.0,
                "trades": [],
                "days": [],
                "equity_curve": [],
            }

        events_by_date: Dict[str, List[NewsEvent]] = {}
        for event in all_events:
            events_by_date.setdefault(event.date, []).append(event)

        cash = float(initial_cash)
        positions: List[Dict[str, Any]] = []
        pending_buys: List[Dict[str, Any]] = []
        trades: List[Dict[str, Any]] = []
        days: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []
        prev_total = float(initial_cash)
        historical_outcomes = []
        for event in self.events():
            if self._is_sample_event(event):
                continue
            realized = self.future_return(event.code, event.date, hold_days=hold_days)
            if not realized:
                continue
            historical_outcomes.append(
                {
                    "exit_date": realized["exit_date"],
                    "event": event,
                    "return_pct": safe_float(realized.get("return_pct"), 0),
                }
            )
        historical_outcomes.sort(key=lambda item: item["exit_date"])
        outcome_idx = 0
        global_returns: List[float] = []
        code_returns: Dict[str, List[float]] = {}
        theme_returns: Dict[str, List[float]] = {}
        type_returns: Dict[str, List[float]] = {}

        def add_realized_outcomes_until(date: str) -> None:
            nonlocal outcome_idx
            while outcome_idx < len(historical_outcomes) and historical_outcomes[outcome_idx]["exit_date"] <= date:
                item = historical_outcomes[outcome_idx]
                event = item["event"]
                ret = safe_float(item["return_pct"], 0)
                global_returns.append(ret)
                code_returns.setdefault(event.code, []).append(ret)
                theme_returns.setdefault(f"{event.industry}|{event.event_type}", []).append(ret)
                type_returns.setdefault(event.event_type, []).append(ret)
                outcome_idx += 1

        def current_corr(date: str) -> Dict[str, Any]:
            return {
                "as_of": date,
                "realized_by": date,
                "hold_days": hold_days,
                "global": self._aggregate_stats(global_returns),
                "by_code": {key: self._aggregate_stats(val) for key, val in code_returns.items() if len(val) >= 2},
                "by_theme": {key: self._aggregate_stats(val) for key, val in theme_returns.items() if len(val) >= 3},
                "by_type": {key: self._aggregate_stats(val) for key, val in type_returns.items() if len(val) >= 3},
            }

        for current_date in trading_dates:
            add_realized_outcomes_until(current_date)
            day_buys = []
            day_sells = []
            day_missed = []
            corr = current_corr(current_date)
            today_events = events_by_date.get(current_date, [])
            today_candidate_scores = []
            if today_events:
                grouped_today: Dict[str, Dict[str, Any]] = {}
                for event in today_events:
                    grouped_today.setdefault(event.code, {"events": []})["events"].append(event)
                for code, bundle in grouped_today.items():
                    if not self.universe.is_tradeable_a_share(code):
                        continue
                    scores = self._agent_scores(bundle, corr, current_date)
                    events_sorted = sorted(bundle["events"], key=lambda item: item.impact_score, reverse=True)
                    primary = events_sorted[0]
                    buy_score = safe_float(scores.get("buy_score"), 0)
                    sell_score = safe_float(scores.get("sell_score"), 0)
                    action = "买入候选" if buy_score >= params["buy_threshold"] else ("重点观察" if buy_score >= params["watch_threshold"] else "暂不买入")
                    if sell_score >= params["avoid_sell_threshold"] and buy_score < params["avoid_buy_ceiling"]:
                        action = "回避/卖出"
                    today_candidate_scores.append(
                        {
                            "code": code,
                            "name": self.universe.name(code, primary.name),
                            "action": action,
                            "buy_score": round(buy_score, 2),
                            "sell_score": round(sell_score, 2),
                            "reason": primary.reason,
                            "latest_event_date": current_date,
                        }
                    )
                today_candidate_scores.sort(key=lambda item: item["buy_score"], reverse=True)
            today_score_map = {item["code"]: item for item in today_candidate_scores}

            still_pending = []
            held_codes = {pos["code"] for pos in positions}
            for order in pending_buys:
                execute_on = str(order.get("execute_on") or "")
                if execute_on and execute_on > current_date:
                    still_pending.append(order)
                    continue
                if len(positions) >= max_positions or order.get("code") in held_codes:
                    day_missed.append(
                        {
                            "date": current_date,
                            "side": "MISS",
                            "signal_date": order.get("signal_date", ""),
                            "execute_on": current_date,
                            "code": order.get("code", ""),
                            "name": order.get("name", ""),
                            "score": order.get("buy_score", 0),
                            "status": "未成交",
                            "unfilled_reason": "已持有该股" if order.get("code") in held_codes else "最大持仓数已满",
                            "reason": order.get("reason", ""),
                        }
                    )
                    continue
                row = self._row_on_date(order["code"], current_date)
                if not row:
                    retries = int(order.get("retries", 0)) + 1
                    if retries <= 5:
                        delayed = dict(order)
                        delayed["retries"] = retries
                        still_pending.append(delayed)
                    else:
                        day_missed.append(
                            {
                                "date": current_date,
                                "side": "MISS",
                                "signal_date": order.get("signal_date", ""),
                                "execute_on": current_date,
                                "code": order.get("code", ""),
                                "name": order.get("name", ""),
                                "score": order.get("buy_score", 0),
                                "status": "未成交",
                                "unfilled_reason": "执行日无可用K线或停牌",
                                "reason": order.get("reason", ""),
                            }
                        )
                    continue
                open_price = safe_float(row.get("open") or row.get("close"), 0)
                if open_price <= 0:
                    day_missed.append(
                        {
                            "date": current_date,
                            "side": "MISS",
                            "signal_date": order.get("signal_date", ""),
                            "execute_on": current_date,
                            "code": order.get("code", ""),
                            "name": order.get("name", ""),
                            "score": order.get("buy_score", 0),
                            "status": "未成交",
                            "unfilled_reason": "执行日开盘价无效",
                            "reason": order.get("reason", ""),
                        }
                    )
                    continue
                slots_left = max(1, max_positions - len(positions))
                allocation = min(cash / slots_left, float(initial_cash) / max_positions)
                qty = math.floor(allocation / open_price / 100) * 100
                while qty > 0:
                    gross_amount = qty * open_price
                    fees = self._broker_fees("BUY", gross_amount)
                    if gross_amount + fees["total_fee"] <= cash:
                        break
                    qty -= 100
                if qty <= 0:
                    day_missed.append(
                        {
                            "date": current_date,
                            "side": "MISS",
                            "signal_date": order.get("signal_date", ""),
                            "execute_on": current_date,
                            "code": order.get("code", ""),
                            "name": order.get("name", ""),
                            "score": order.get("buy_score", 0),
                            "status": "未成交",
                            "unfilled_reason": "可用资金不足以买入一手",
                            "reason": order.get("reason", ""),
                        }
                    )
                    continue
                gross_amount = qty * open_price
                fees = self._broker_fees("BUY", gross_amount)
                cash -= gross_amount + fees["total_fee"]
                position = {
                    "code": order["code"],
                    "name": order["name"],
                    "qty": qty,
                    "entry_date": current_date,
                    "signal_date": order.get("signal_date", ""),
                    "entry_price": round(open_price, 3),
                    "entry_cost": round(gross_amount + fees["total_fee"], 2),
                    "buy_score": order.get("buy_score", 0),
                    "reason": order.get("reason", ""),
                    "hold_days": 0,
                }
                positions.append(position)
                held_codes.add(order["code"])
                trade = {
                    "date": current_date,
                    "side": "BUY",
                    "code": order["code"],
                    "name": order["name"],
                    "qty": qty,
                    "price": round(open_price, 3),
                    "amount": round(gross_amount, 2),
                    "commission": fees["commission"],
                    "stamp_duty": fees["stamp_duty"],
                    "transfer_fee": fees["transfer_fee"],
                    "total_fee": fees["total_fee"],
                    "score": order.get("buy_score", 0),
                    "signal_date": order.get("signal_date", ""),
                    "reason": order.get("reason", ""),
                }
                trades.append(trade)
                day_buys.append(trade)
            pending_buys = still_pending

            remaining_positions = []
            for pos in positions:
                row = self._row_on_date(pos["code"], current_date)
                if not row:
                    remaining_positions.append(pos)
                    continue
                close_price = safe_float(row.get("close"), 0)
                if close_price <= 0:
                    remaining_positions.append(pos)
                    continue
                pos = dict(pos)
                pos["hold_days"] = int(pos.get("hold_days", 0)) + 1
                entry_price = safe_float(pos.get("entry_price"), close_price)
                pnl_pct = (close_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
                rec = today_score_map.get(pos["code"], {})
                should_sell = (
                    pnl_pct <= params["stop_loss_pct"]
                    or pnl_pct >= params["take_profit_pct"]
                    or pos["hold_days"] >= hold_days
                    or safe_float(rec.get("sell_score"), 0) >= params["sell_score_threshold"]
                )
                if should_sell:
                    qty = safe_float(pos.get("qty"), 0)
                    gross_amount = qty * close_price
                    fees = self._broker_fees("SELL", gross_amount)
                    net_amount = gross_amount - fees["total_fee"]
                    entry_cost = safe_float(pos.get("entry_cost"), qty * entry_price)
                    pnl_pct = (net_amount / entry_cost - 1) * 100 if entry_cost > 0 else pnl_pct
                    cash += net_amount
                    reason = "持仓到期"
                    if pnl_pct <= params["stop_loss_pct"]:
                        reason = "止损"
                    elif pnl_pct >= params["take_profit_pct"]:
                        reason = "止盈"
                    elif safe_float(rec.get("sell_score"), 0) >= params["sell_score_threshold"]:
                        reason = "卖出评分触发"
                    trade = {
                        "date": current_date,
                        "side": "SELL",
                        "code": pos["code"],
                        "name": pos["name"],
                        "qty": pos["qty"],
                        "price": round(close_price, 3),
                        "amount": round(gross_amount, 2),
                        "commission": fees["commission"],
                        "stamp_duty": fees["stamp_duty"],
                        "transfer_fee": fees["transfer_fee"],
                        "total_fee": fees["total_fee"],
                        "net_amount": round(net_amount, 2),
                        "pnl_pct": round(pnl_pct, 3),
                        "reason": reason,
                    }
                    trades.append(trade)
                    day_sells.append(trade)
                else:
                    pos["last_price"] = round(close_price, 3)
                    pos["pnl_pct"] = round(pnl_pct, 3)
                    remaining_positions.append(pos)
            positions = remaining_positions

            signal_items = []
            if today_events:
                recs = today_candidate_scores[:top_n]
                next_date = self._next_trading_date(trading_dates, current_date)
                held_or_pending = {pos["code"] for pos in positions} | {order["code"] for order in pending_buys}
                for item in recs:
                    if item.get("latest_event_date") != current_date:
                        continue
                    if item.get("action") != "买入候选":
                        continue
                    if item["code"] in held_or_pending:
                        continue
                    if not next_date:
                        continue
                    order = {
                        "signal_date": current_date,
                        "execute_on": next_date,
                        "code": item["code"],
                        "name": item["name"],
                        "buy_score": item["buy_score"],
                        "sell_score": item["sell_score"],
                        "reason": item["reason"][:180],
                    }
                    pending_buys.append(order)
                    held_or_pending.add(item["code"])
                    signal_items.append(order)

            position_snapshots = []
            market_value = 0.0
            for pos in positions:
                row = self._row_on_date(pos["code"], current_date)
                close_price = safe_float(row.get("close"), pos.get("last_price", 0)) if row else safe_float(pos.get("last_price"), 0)
                entry_price = safe_float(pos.get("entry_price"), close_price)
                qty = safe_float(pos.get("qty"), 0)
                pnl_pct = (close_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
                value = qty * close_price
                market_value += value
                position_snapshots.append(
                    {
                        "code": pos["code"],
                        "name": pos["name"],
                        "qty": qty,
                        "entry_date": pos.get("entry_date", ""),
                        "entry_price": round(entry_price, 3),
                        "last_price": round(close_price, 3),
                        "market_value": round(value, 2),
                        "pnl_pct": round(pnl_pct, 3),
                    }
                )

            total_value = cash + market_value
            daily_return = (total_value / prev_total - 1) * 100 if prev_total > 0 else 0.0
            prev_total = total_value
            day_record = {
                "date": current_date,
                "event_count": len(today_events),
                "signals": signal_items,
                "buys": day_buys,
                "sells": day_sells,
                "missed": day_missed,
                "pending_buys": list(pending_buys),
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "total_value": round(total_value, 2),
                "daily_return_pct": round(daily_return, 3),
                "positions": position_snapshots,
            }
            days.append(day_record)
            equity_curve.append(
                {
                    "date": current_date,
                    "total_value": round(total_value, 2),
                    "return_pct": round((total_value / float(initial_cash) - 1) * 100, 3),
                    "position_count": len(position_snapshots),
                }
            )

        final_value = equity_curve[-1]["total_value"] if equity_curve else float(initial_cash)
        closed_sells = [trade for trade in trades if trade.get("side") == "SELL"]
        win_rate = (
            sum(1 for trade in closed_sells if safe_float(trade.get("pnl_pct"), 0) > 0) / len(closed_sells) * 100
            if closed_sells
            else 0.0
        )
        peak = float(initial_cash)
        max_drawdown = 0.0
        for point in equity_curve:
            value = safe_float(point.get("total_value"), initial_cash)
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, value / peak - 1)
        performance = self._performance_metrics(equity_curve, trades, float(initial_cash), final_value)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": round(float(initial_cash), 2),
            "final_value": round(final_value, 2),
            "return_pct": round((final_value / float(initial_cash) - 1) * 100, 3),
            "max_drawdown_pct": round(max_drawdown * 100, 3),
            "annualized_return_pct": performance["annualized_return_pct"],
            "sharpe_ratio": performance["sharpe_ratio"],
            "profit_factor": performance["profit_factor"],
            "total_fees": performance["total_fees"],
            "exposure_pct": performance["exposure_pct"],
            "closed_trades": len(closed_sells),
            "win_rate": round(win_rate, 2),
            "performance": performance,
            "strategy_params": params,
            "trades": trades,
            "days": days,
            "equity_curve": equity_curve,
            "auto_fill": auto_fill_result,
        }

    def _intraday_exit(
        self,
        position: Dict[str, Any],
        date: str,
        start_dt: Optional[datetime] = None,
        take_profit_pct: float = 8.0,
        stop_loss_pct: float = -5.0,
    ) -> Optional[Dict[str, Any]]:
        bars = self.load_intraday_bars(position.get("code"), date)
        if not bars:
            return None
        entry_price = safe_float(position.get("entry_price"), 0)
        if entry_price <= 0:
            return None
        stop_price = entry_price * (1 + stop_loss_pct / 100.0)
        take_price = entry_price * (1 + take_profit_pct / 100.0)
        for bar in bars:
            if start_dt is not None and bar["dt"] <= start_dt:
                continue
            # Conservative assumption: if stop and take-profit are both touched in
            # the same 5-minute bar, the stop-loss happens first.
            if safe_float(bar.get("low"), 0) <= stop_price:
                return {
                    "price": round(stop_price, 3),
                    "time": bar["time"],
                    "reason": "分时止损",
                    "mode": "intraday_5m",
                }
            if safe_float(bar.get("high"), 0) >= take_price:
                return {
                    "price": round(take_price, 3),
                    "time": bar["time"],
                    "reason": "分时止盈",
                    "mode": "intraday_5m",
                }
        return None

    def walk_forward_intraday(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: Optional[float] = None,
        max_positions: Optional[int] = None,
        hold_days: Optional[int] = None,
        top_n: Optional[int] = None,
        use_daily_fallback: bool = True,
        auto_fill: bool = False,
    ) -> Dict[str, Any]:
        params = self.strategy_params()
        initial_cash = max(1.0, safe_float(initial_cash, params["account_initial_cash"]))
        max_positions = int(max_positions or params["max_positions"])
        hold_days = int(hold_days or params["max_hold_days"])
        top_n = int(top_n or params["top_n"])
        all_events = self.events()
        all_events = [event for event in all_events if not self._is_sample_event(event)]
        if end_date:
            all_events = [event for event in all_events if event.date <= end_date]
        if start_date:
            all_events = [event for event in all_events if event.date >= start_date]
        if not all_events:
            return {
                "mode": "intraday_5m",
                "start_date": start_date,
                "end_date": end_date,
                "initial_cash": initial_cash,
                "final_value": initial_cash,
                "return_pct": 0.0,
                "trades": [],
                "days": [],
                "equity_curve": [],
            }

        start_date = start_date or min(event.date for event in all_events)
        end_date = end_date or max(event.date for event in all_events)
        auto_fill_result: Dict[str, Any] = {}
        if auto_fill:
            auto_fill_result = self.ensure_daily_kline_for_events(
                start_date=start_date,
                end_date=end_date,
                hold_days=hold_days,
                max_codes=500,
                force=False,
            )
        codes = sorted({event.code for event in all_events})
        trading_dates = [
            date
            for date in self._all_trading_dates_for_codes(codes)
            if start_date <= date <= end_date
        ]
        if not trading_dates:
            return {
                "mode": "intraday_5m",
                "start_date": start_date,
                "end_date": end_date,
                "initial_cash": initial_cash,
                "final_value": initial_cash,
                "return_pct": 0.0,
                "trades": [],
                "days": [],
                "equity_curve": [],
            }

        intraday_dates = self._available_intraday_dates()
        events_by_date: Dict[str, List[NewsEvent]] = {}
        for event in all_events:
            events_by_date.setdefault(event.date, []).append(event)

        historical_outcomes = []
        for event in self.events():
            if self._is_sample_event(event):
                continue
            realized = self.future_return(event.code, event.date, hold_days=hold_days)
            if not realized:
                continue
            historical_outcomes.append(
                {
                    "exit_date": realized["exit_date"],
                    "event": event,
                    "return_pct": safe_float(realized.get("return_pct"), 0),
                }
            )
        historical_outcomes.sort(key=lambda item: item["exit_date"])
        outcome_idx = 0
        global_returns: List[float] = []
        code_returns: Dict[str, List[float]] = {}
        theme_returns: Dict[str, List[float]] = {}
        type_returns: Dict[str, List[float]] = {}

        def add_realized_outcomes_until(date: str) -> None:
            nonlocal outcome_idx
            while outcome_idx < len(historical_outcomes) and historical_outcomes[outcome_idx]["exit_date"] <= date:
                item = historical_outcomes[outcome_idx]
                event = item["event"]
                ret = safe_float(item["return_pct"], 0)
                global_returns.append(ret)
                code_returns.setdefault(event.code, []).append(ret)
                theme_returns.setdefault(f"{event.industry}|{event.event_type}", []).append(ret)
                type_returns.setdefault(event.event_type, []).append(ret)
                outcome_idx += 1

        def current_corr(date: str) -> Dict[str, Any]:
            return {
                "as_of": date,
                "realized_by": date,
                "hold_days": hold_days,
                "global": self._aggregate_stats(global_returns),
                "by_code": {key: self._aggregate_stats(val) for key, val in code_returns.items() if len(val) >= 2},
                "by_theme": {key: self._aggregate_stats(val) for key, val in theme_returns.items() if len(val) >= 3},
                "by_type": {key: self._aggregate_stats(val) for key, val in type_returns.items() if len(val) >= 3},
            }

        cash = float(initial_cash)
        positions: List[Dict[str, Any]] = []
        pending_buys: List[Dict[str, Any]] = []
        trades: List[Dict[str, Any]] = []
        days: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []
        prev_total = float(initial_cash)

        for current_date in trading_dates:
            add_realized_outcomes_until(current_date)
            corr = current_corr(current_date)
            today_events = events_by_date.get(current_date, [])
            day_buys: List[Dict[str, Any]] = []
            day_sells: List[Dict[str, Any]] = []
            day_intraday_codes = intraday_dates.get(current_date, set())

            today_candidate_scores = []
            if today_events:
                grouped_today: Dict[str, Dict[str, Any]] = {}
                for event in today_events:
                    grouped_today.setdefault(event.code, {"events": []})["events"].append(event)
                for code, bundle in grouped_today.items():
                    if not self.universe.is_tradeable_a_share(code):
                        continue
                    scores = self._agent_scores(bundle, corr, current_date)
                    events_sorted = sorted(bundle["events"], key=lambda item: item.impact_score, reverse=True)
                    primary = events_sorted[0]
                    buy_score = safe_float(scores.get("buy_score"), 0)
                    sell_score = safe_float(scores.get("sell_score"), 0)
                    action = "买入候选" if buy_score >= params["buy_threshold"] else ("重点观察" if buy_score >= params["watch_threshold"] else "暂不买入")
                    if sell_score >= params["avoid_sell_threshold"] and buy_score < params["avoid_buy_ceiling"]:
                        action = "回避/卖出"
                    signal_dt = self._event_signal_dt(primary)
                    today_candidate_scores.append(
                        {
                            "code": code,
                            "name": self.universe.name(code, primary.name),
                            "action": action,
                            "buy_score": round(buy_score, 2),
                            "sell_score": round(sell_score, 2),
                            "reason": primary.reason,
                            "latest_event_date": current_date,
                            "signal_time": signal_dt.strftime("%Y-%m-%d %H:%M:%S") if signal_dt else "",
                            "signal_dt": signal_dt,
                        }
                    )
                today_candidate_scores.sort(key=lambda item: item["buy_score"], reverse=True)
            today_score_map = {item["code"]: item for item in today_candidate_scores}

            still_pending = []
            held_codes = {pos["code"] for pos in positions}
            for order in pending_buys:
                execute_on = str(order.get("execute_on") or "")
                if execute_on and execute_on > current_date:
                    still_pending.append(order)
                    continue
                if len(positions) >= max_positions or order.get("code") in held_codes:
                    continue
                entry_bar = self._first_intraday_bar(order["code"], current_date)
                entry_mode = "intraday_5m"
                entry_time = entry_bar["time"] if entry_bar else f"{current_date} 09:30:00"
                entry_price = safe_float(entry_bar.get("open"), 0) if entry_bar else 0
                if entry_price <= 0 and use_daily_fallback:
                    row = self._row_on_date(order["code"], current_date)
                    entry_price = safe_float(row.get("open"), 0) if row else 0
                    entry_mode = "daily_open_fallback"
                if entry_price <= 0:
                    retries = int(order.get("retries", 0)) + 1
                    if retries <= 5:
                        delayed = dict(order)
                        delayed["retries"] = retries
                        still_pending.append(delayed)
                    continue
                slots_left = max(1, max_positions - len(positions))
                allocation = min(cash / slots_left, float(initial_cash) / max_positions)
                qty = math.floor(allocation / entry_price / 100) * 100
                if qty <= 0:
                    continue
                cash -= qty * entry_price
                position = {
                    "code": order["code"],
                    "name": order["name"],
                    "qty": qty,
                    "entry_date": current_date,
                    "entry_time": entry_time,
                    "signal_date": order.get("signal_date", ""),
                    "signal_time": order.get("signal_time", ""),
                    "entry_price": round(entry_price, 3),
                    "buy_score": order.get("buy_score", 0),
                    "reason": order.get("reason", ""),
                    "hold_days": 0,
                    "entry_mode": entry_mode,
                }
                positions.append(position)
                held_codes.add(order["code"])
                trade = {
                    "date": current_date,
                    "time": entry_time,
                    "side": "BUY",
                    "code": order["code"],
                    "name": order["name"],
                    "qty": qty,
                    "price": round(entry_price, 3),
                    "score": order.get("buy_score", 0),
                    "signal_date": order.get("signal_date", ""),
                    "signal_time": order.get("signal_time", ""),
                    "reason": order.get("reason", ""),
                    "mode": entry_mode,
                }
                trades.append(trade)
                day_buys.append(trade)
            pending_buys = still_pending

            remaining_positions = []
            for pos in positions:
                pos = dict(pos)
                row = self._row_on_date(pos["code"], current_date)
                bars = self.load_intraday_bars(pos["code"], current_date)
                entry_dt = parse_time(pos.get("entry_time")) if pos.get("entry_date") == current_date else None
                exit_info = self._intraday_exit(
                    pos,
                    current_date,
                    start_dt=entry_dt,
                    take_profit_pct=params["take_profit_pct"],
                    stop_loss_pct=params["stop_loss_pct"],
                )
                close_price = safe_float(bars[-1].get("close"), 0) if bars else 0
                close_time = bars[-1]["time"] if bars else f"{current_date} 15:00:00"
                if close_price <= 0 and row:
                    close_price = safe_float(row.get("close"), 0)
                has_price = close_price > 0
                if has_price:
                    pos["hold_days"] = int(pos.get("hold_days", 0)) + 1
                entry_price = safe_float(pos.get("entry_price"), close_price)
                pnl_pct = (close_price / entry_price - 1) * 100 if entry_price > 0 and close_price > 0 else 0.0
                rec = today_score_map.get(pos["code"], {})
                if exit_info is None and has_price:
                    if pos["hold_days"] >= hold_days:
                        exit_info = {"price": close_price, "time": close_time, "reason": "持仓到期", "mode": "intraday_5m_eod" if bars else "daily_close_fallback"}
                    elif safe_float(rec.get("sell_score"), 0) >= params["sell_score_threshold"]:
                        exit_info = {"price": close_price, "time": close_time, "reason": "卖出评分触发", "mode": "intraday_5m_eod" if bars else "daily_close_fallback"}
                if exit_info is not None:
                    exit_price = safe_float(exit_info.get("price"), close_price)
                    cash += safe_float(pos.get("qty"), 0) * exit_price
                    real_pnl_pct = (exit_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
                    trade = {
                        "date": current_date,
                        "time": exit_info.get("time", close_time),
                        "side": "SELL",
                        "code": pos["code"],
                        "name": pos["name"],
                        "qty": pos["qty"],
                        "price": round(exit_price, 3),
                        "pnl_pct": round(real_pnl_pct, 3),
                        "reason": exit_info.get("reason", ""),
                        "mode": exit_info.get("mode", ""),
                    }
                    trades.append(trade)
                    day_sells.append(trade)
                else:
                    pos["last_price"] = round(close_price, 3) if close_price > 0 else safe_float(pos.get("last_price"), 0)
                    pos["pnl_pct"] = round(pnl_pct, 3)
                    remaining_positions.append(pos)
            positions = remaining_positions

            signal_items = []
            if today_events:
                held_or_pending = {pos["code"] for pos in positions} | {order["code"] for order in pending_buys}
                next_date = self._next_trading_date(trading_dates, current_date)
                for item in today_candidate_scores[:top_n]:
                    if item.get("action") != "买入候选":
                        continue
                    if item["code"] in held_or_pending or len(positions) + len(pending_buys) >= max_positions:
                        continue
                    entry_bar = self._next_intraday_bar_after(item["code"], current_date, item.get("signal_dt"))
                    order = {
                        "signal_date": current_date,
                        "signal_time": item.get("signal_time", ""),
                        "execute_on": current_date if entry_bar else next_date,
                        "code": item["code"],
                        "name": item["name"],
                        "buy_score": item["buy_score"],
                        "sell_score": item["sell_score"],
                        "reason": item["reason"][:180],
                        "signal_dt": None,
                    }
                    if entry_bar:
                        entry_price = safe_float(entry_bar.get("open"), 0)
                        slots_left = max(1, max_positions - len(positions))
                        allocation = min(cash / slots_left, float(initial_cash) / max_positions)
                        qty = math.floor(allocation / entry_price / 100) * 100 if entry_price > 0 else 0
                        if qty <= 0:
                            continue
                        cash -= qty * entry_price
                        position = {
                            "code": item["code"],
                            "name": item["name"],
                            "qty": qty,
                            "entry_date": current_date,
                            "entry_time": entry_bar["time"],
                            "signal_date": current_date,
                            "signal_time": item.get("signal_time", ""),
                            "entry_price": round(entry_price, 3),
                            "buy_score": item["buy_score"],
                            "reason": item["reason"][:180],
                            "hold_days": 0,
                            "entry_mode": "intraday_5m",
                        }
                        trade = {
                            "date": current_date,
                            "time": entry_bar["time"],
                            "side": "BUY",
                            "code": item["code"],
                            "name": item["name"],
                            "qty": qty,
                            "price": round(entry_price, 3),
                            "score": item["buy_score"],
                            "signal_date": current_date,
                            "signal_time": item.get("signal_time", ""),
                            "reason": item["reason"][:180],
                            "mode": "intraday_5m",
                        }
                        trades.append(trade)
                        day_buys.append(trade)
                        exit_info = self._intraday_exit(
                            position,
                            current_date,
                            start_dt=entry_bar["dt"],
                            take_profit_pct=params["take_profit_pct"],
                            stop_loss_pct=params["stop_loss_pct"],
                        )
                        if exit_info is not None:
                            exit_price = safe_float(exit_info.get("price"), entry_price)
                            cash += qty * exit_price
                            real_pnl_pct = (exit_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
                            sell_trade = {
                                "date": current_date,
                                "time": exit_info.get("time", entry_bar["time"]),
                                "side": "SELL",
                                "code": item["code"],
                                "name": item["name"],
                                "qty": qty,
                                "price": round(exit_price, 3),
                                "pnl_pct": round(real_pnl_pct, 3),
                                "reason": exit_info.get("reason", ""),
                                "mode": exit_info.get("mode", ""),
                            }
                            trades.append(sell_trade)
                            day_sells.append(sell_trade)
                        else:
                            positions.append(position)
                            held_or_pending.add(item["code"])
                        signal_items.append({**order, "execute_on": current_date, "mode": "intraday_5m"})
                    elif next_date:
                        pending_buys.append(order)
                        held_or_pending.add(item["code"])
                        signal_items.append({**order, "mode": "next_session"})

            position_snapshots = []
            market_value = 0.0
            for pos in positions:
                bars = self.load_intraday_bars(pos["code"], current_date)
                row = self._row_on_date(pos["code"], current_date)
                close_price = safe_float(bars[-1].get("close"), 0) if bars else 0
                if close_price <= 0 and row:
                    close_price = safe_float(row.get("close"), 0)
                if close_price <= 0:
                    close_price = safe_float(pos.get("last_price"), 0)
                entry_price = safe_float(pos.get("entry_price"), close_price)
                qty = safe_float(pos.get("qty"), 0)
                pnl_pct = (close_price / entry_price - 1) * 100 if entry_price > 0 and close_price > 0 else 0.0
                value = qty * close_price
                market_value += value
                position_snapshots.append(
                    {
                        "code": pos["code"],
                        "name": pos["name"],
                        "qty": qty,
                        "entry_date": pos.get("entry_date", ""),
                        "entry_time": pos.get("entry_time", ""),
                        "entry_price": round(entry_price, 3),
                        "last_price": round(close_price, 3),
                        "market_value": round(value, 2),
                        "pnl_pct": round(pnl_pct, 3),
                        "entry_mode": pos.get("entry_mode", ""),
                    }
                )

            total_value = cash + market_value
            daily_return = (total_value / prev_total - 1) * 100 if prev_total > 0 else 0.0
            prev_total = total_value
            day_record = {
                "date": current_date,
                "event_count": len(today_events),
                "intraday_code_count": len(day_intraday_codes),
                "signals": signal_items,
                "buys": day_buys,
                "sells": day_sells,
                "pending_buys": list(pending_buys),
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "total_value": round(total_value, 2),
                "daily_return_pct": round(daily_return, 3),
                "positions": position_snapshots,
            }
            days.append(day_record)
            equity_curve.append(
                {
                    "date": current_date,
                    "total_value": round(total_value, 2),
                    "return_pct": round((total_value / float(initial_cash) - 1) * 100, 3),
                    "position_count": len(position_snapshots),
                }
            )

        final_value = equity_curve[-1]["total_value"] if equity_curve else float(initial_cash)
        closed_sells = [trade for trade in trades if trade.get("side") == "SELL"]
        win_rate = (
            sum(1 for trade in closed_sells if safe_float(trade.get("pnl_pct"), 0) > 0) / len(closed_sells) * 100
            if closed_sells
            else 0.0
        )
        peak = float(initial_cash)
        max_drawdown = 0.0
        for point in equity_curve:
            value = safe_float(point.get("total_value"), initial_cash)
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, value / peak - 1)
        intraday_trade_count = sum(1 for trade in trades if str(trade.get("mode", "")).startswith("intraday_5m"))
        fallback_trade_count = len(trades) - intraday_trade_count
        performance = self._performance_metrics(equity_curve, trades, float(initial_cash), final_value)
        return {
            "mode": "intraday_5m",
            "daily_fallback": bool(use_daily_fallback),
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": round(float(initial_cash), 2),
            "final_value": round(final_value, 2),
            "return_pct": round((final_value / float(initial_cash) - 1) * 100, 3),
            "max_drawdown_pct": round(max_drawdown * 100, 3),
            "annualized_return_pct": performance["annualized_return_pct"],
            "sharpe_ratio": performance["sharpe_ratio"],
            "profit_factor": performance["profit_factor"],
            "total_fees": performance["total_fees"],
            "exposure_pct": performance["exposure_pct"],
            "closed_trades": len(closed_sells),
            "win_rate": round(win_rate, 2),
            "intraday_available_dates": sorted(intraday_dates.keys()),
            "intraday_trade_count": intraday_trade_count,
            "fallback_trade_count": fallback_trade_count,
            "performance": performance,
            "strategy_params": params,
            "trades": trades,
            "days": days,
            "equity_curve": equity_curve,
            "auto_fill": auto_fill_result,
        }

    def _backtest_event_score(self, events: List[NewsEvent]) -> float:
        impact = statistics.mean(event.impact_score for event in events)
        sentiment = statistics.mean(event.sentiment for event in events)
        ai_score = max((event.ai_score for event in events), default=0.0)
        score = 45 + impact * 0.35 + sentiment * 20
        if ai_score > 0:
            score += (ai_score - 5) * 3
        return clamp(score)

    def _backtest_data_diagnostics(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        hold_days: int,
    ) -> Dict[str, Any]:
        events = [
            event
            for event in self.events()
            if not self._is_sample_event(event)
            and (not start_date or event.date >= start_date)
            and (not end_date or event.date <= end_date)
            and self.universe.is_tradeable_a_share(event.code)
        ]
        codes = sorted({event.code for event in events})
        missing_daily = []
        insufficient_forward = []
        covered = 0
        for code in codes:
            rows = self.load_kline(code)
            if not rows:
                missing_daily.append(code)
                continue
            event_dates = [event.date for event in events if event.code == code]
            has_forward = False
            for event_date in event_dates[:20]:
                if self.future_return(code, event_date, hold_days=hold_days):
                    has_forward = True
                    break
            if has_forward:
                covered += 1
            else:
                insufficient_forward.append(code)
        warnings = []
        if not events:
            warnings.append("no_events_in_range")
        if missing_daily:
            warnings.append("missing_daily_kline")
        if insufficient_forward:
            warnings.append("insufficient_forward_kline")
        return {
            "event_count": len(events),
            "event_stock_count": len(codes),
            "daily_kline_covered_stock_count": covered,
            "missing_daily_kline_count": len(missing_daily),
            "insufficient_forward_kline_count": len(insufficient_forward),
            "missing_daily_kline_codes": missing_daily[:50],
            "insufficient_forward_kline_codes": insufficient_forward[:50],
            "warnings": warnings,
            "sqlite_enabled": QUANT_DB_FILE.exists(),
            "sqlite_file": str(QUANT_DB_FILE),
        }

    def backtest(
        self,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: Optional[float] = None,
        max_positions: Optional[int] = None,
        hold_days: int = 3,
        top_n: int = 5,
        auto_fill: bool = False,
    ) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        end_date = end_date or as_of
        hold_days = max(1, min(int(hold_days or 3), 60))
        top_n = max(1, min(int(top_n or 5), 50))
        auto_fill_result: Dict[str, Any] = {}
        if auto_fill:
            auto_fill_result = self.ensure_daily_kline_for_events(
                start_date=start_date,
                end_date=end_date,
                hold_days=hold_days,
                max_codes=max(50, int(top_n or 5) * 40),
                force=False,
            )
        groups: Dict[Tuple[str, str], List[NewsEvent]] = {}
        for event in self.events():
            if event.date >= end_date:
                continue
            if self._is_sample_event(event):
                continue
            if start_date and event.date < start_date:
                continue
            groups.setdefault((event.date, event.code), []).append(event)
        by_date: Dict[str, List[Dict[str, Any]]] = {}
        for (date, code), events in groups.items():
            score = self._backtest_event_score(events)
            if score < 58:
                continue
            by_date.setdefault(date, []).append({"date": date, "code": code, "events": events, "score": score})

        trades = []
        for date, candidates in sorted(by_date.items()):
            ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)[: max(1, top_n)]
            for item in ranked:
                realized = self.future_return(item["code"], item["date"], hold_days=hold_days)
                if not realized:
                    continue
                primary = max(item["events"], key=lambda event: event.impact_score)
                trades.append(
                    {
                        "date": item["date"],
                        "code": item["code"],
                        "name": self.universe.name(item["code"], primary.name),
                        "score": round(item["score"], 2),
                        "industry": primary.industry,
                        "event_type": primary.event_type,
                        **realized,
                    }
                )

        returns = [safe_float(item["return_pct"], 0) for item in trades]
        compounded = 1.0
        equity_curve = []
        for ret in returns:
            compounded *= 1 + (ret / 100.0) / max(1, top_n)
            equity_curve.append(compounded)
        peak = 1.0
        max_drawdown = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, value / peak - 1)
        buckets: Dict[str, List[float]] = {"58-65": [], "65-72": [], "72-80": [], "80-100": []}
        for trade in trades:
            score = safe_float(trade.get("score"), 0)
            ret = safe_float(trade.get("return_pct"), 0)
            if score < 65:
                buckets["58-65"].append(ret)
            elif score < 72:
                buckets["65-72"].append(ret)
            elif score < 80:
                buckets["72-80"].append(ret)
            else:
                buckets["80-100"].append(ret)

        timeline = self.walk_forward(
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            auto_fill=False,
        )
        diagnostics = self._backtest_data_diagnostics(start_date=start_date, end_date=end_date, hold_days=hold_days)
        timeline_trades = timeline.get("trades") if isinstance(timeline.get("trades"), list) else []
        timeline_performance = timeline.get("performance") if isinstance(timeline.get("performance"), dict) else {}
        backtest_account = self.account_from_trades(
            timeline_trades,
            initial_cash=timeline.get("initial_cash", initial_cash),
            as_of=end_date,
            limit=500,
        )
        status = "ok" if (trades or timeline_trades or diagnostics.get("event_count", 0) > 0) else "no_data"
        message = "backtest completed"
        if status == "no_data":
            message = "no events or market data available for the requested range"
        elif not timeline_trades:
            message = "events were found, but no closed strategy trades were generated; check thresholds and K-line coverage"

        return {
            "status": status,
            "message": message,
            "as_of": as_of,
            "start_date": start_date,
            "end_date": end_date,
            "hold_days": hold_days,
            "top_n": top_n,
            "trades": len(trades),
            "event_outcome_trades": len(trades),
            "timeline_trade_count": len(timeline_trades),
            "closed_trades": int(timeline.get("closed_trades") or 0),
            "initial_cash": timeline.get("initial_cash"),
            "final_value": timeline.get("final_value"),
            "return_pct": timeline.get("return_pct", 0.0),
            "annualized_return_pct": timeline.get("annualized_return_pct", timeline_performance.get("annualized_return_pct", 0.0)),
            "sharpe_ratio": timeline.get("sharpe_ratio", timeline_performance.get("sharpe_ratio", 0.0)),
            "profit_factor": timeline.get("profit_factor", timeline_performance.get("profit_factor", 0.0)),
            "total_fees": timeline.get("total_fees", timeline_performance.get("total_fees", 0.0)),
            "exposure_pct": timeline.get("exposure_pct", timeline_performance.get("exposure_pct", 0.0)),
            "timeline_win_rate": timeline.get("win_rate", 0.0),
            "timeline_max_drawdown_pct": timeline.get("max_drawdown_pct", 0.0),
            "avg_return_pct": round(statistics.mean(returns), 3) if returns else 0.0,
            "median_return_pct": round(statistics.median(returns), 3) if returns else 0.0,
            "win_rate": round(sum(1 for ret in returns if ret > 0) / len(returns) * 100, 2) if returns else 0.0,
            "compounded_return_pct": round((compounded - 1) * 100, 3),
            "max_drawdown_pct": round(max_drawdown * 100, 3),
            "score_buckets": {key: self._aggregate_stats(val) for key, val in buckets.items()},
            "recent_trades": trades[-80:],
            "trade_records": timeline_trades[-500:],
            "account": backtest_account.get("account", {}),
            "positions": backtest_account.get("positions", []),
            "delivery_records": backtest_account.get("delivery_records", []),
            "daily_settlements": backtest_account.get("daily_settlements", []),
            "days": timeline.get("days", [])[-260:] if isinstance(timeline.get("days"), list) else [],
            "equity_curve": timeline.get("equity_curve", []),
            "performance": timeline_performance,
            "strategy_params": timeline.get("strategy_params", self.strategy_params()),
            "data_diagnostics": diagnostics,
            "auto_fill": auto_fill_result,
        }

    def rebuild_paper_from_replay(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        mode: str = "daily",
    ) -> Dict[str, Any]:
        start_date = str(start_date or self.first_data_date()).strip()
        end_date = str(end_date or self.latest_event_date()).strip()
        mode = str(mode or "daily").strip().lower()
        if mode == "intraday":
            timeline = self.walk_forward_intraday(start_date=start_date, end_date=end_date, use_daily_fallback=True)
        else:
            timeline = self.walk_forward(start_date=start_date, end_date=end_date)
        days = timeline.get("days") if isinstance(timeline.get("days"), list) else []
        trades = timeline.get("trades") if isinstance(timeline.get("trades"), list) else []
        last_day = days[-1] if days else {}
        positions = []
        for pos in last_day.get("positions", []) if isinstance(last_day.get("positions"), list) else []:
            if not isinstance(pos, dict):
                continue
            code = digits6(pos.get("code"))
            if not code:
                continue
            positions.append(
                {
                    "code": code,
                    "name": pos.get("name") or self.universe.name(code),
                    "qty": safe_float(pos.get("qty"), 0),
                    "entry_price": safe_float(pos.get("entry_price"), 0),
                    "entry_date": str(pos.get("entry_date") or ""),
                    "last_price": safe_float(pos.get("last_price"), 0),
                    "buy_score": safe_float(pos.get("buy_score"), 0),
                    "reason": pos.get("reason", ""),
                }
            )
        params = self.strategy_params()
        state = self._load_state()
        state["initial_cash"] = timeline.get("initial_cash", params["account_initial_cash"])
        state["cash"] = safe_float(last_day.get("cash"), timeline.get("final_value", params["account_initial_cash"]))
        state["positions"] = positions
        state["trades"] = trades[-2000:]
        state["as_of"] = end_date
        state["paper_replay"] = {
            "mode": timeline.get("mode") or mode,
            "start_date": start_date,
            "end_date": end_date,
            "return_pct": timeline.get("return_pct", 0),
            "closed_trades": timeline.get("closed_trades", 0),
            "win_rate": timeline.get("win_rate", 0),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_state(state)
        portfolio = self.paper_portfolio(as_of=end_date)
        return {
            "status": "ok",
            "mode": timeline.get("mode") or mode,
            "start_date": start_date,
            "end_date": end_date,
            "timeline": {
                "return_pct": timeline.get("return_pct", 0),
                "final_value": timeline.get("final_value", 0),
                "closed_trades": timeline.get("closed_trades", 0),
                "win_rate": timeline.get("win_rate", 0),
                "trade_count": len(trades),
                "day_count": len(days),
            },
            "portfolio": portfolio,
        }

    def paper_portfolio(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        state = self._load_state()
        positions = state.get("positions") if isinstance(state.get("positions"), list) else []
        params = self.strategy_params()
        cash = safe_float(state.get("cash"), params["account_initial_cash"])
        updated_positions = []
        total_value = cash
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            code = digits6(pos.get("code"))
            if is_sample_code(code) or contains_sample_marker(pos):
                continue
            price_row = self.latest_price(code, as_of=as_of)
            if not price_row:
                updated_positions.append(pos)
                continue
            last_price = safe_float(price_row.get("close"), 0)
            qty = safe_float(pos.get("qty"), 0)
            entry_price = safe_float(pos.get("entry_price"), last_price)
            market_value = qty * last_price
            pnl_pct = (last_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
            enriched = dict(pos)
            enriched.update(
                {
                    "last_price": round(last_price, 3),
                    "last_date": price_row["date"],
                    "market_value": round(market_value, 2),
                    "pnl_pct": round(pnl_pct, 3),
                }
            )
            total_value += market_value
            updated_positions.append(enriched)
        return {
            "as_of": as_of,
            "cash": round(cash, 2),
            "positions": updated_positions,
            "trades": state.get("trades", [])[-100:],
            "total_value": round(total_value, 2),
            "model_weights": self.model_weights(),
            "strategy_params": self.strategy_params(),
            "last_calibration": state.get("last_calibration", {}),
        }

    def _broker_fees(self, side: str, amount: float) -> Dict[str, float]:
        amount = max(0.0, safe_float(amount, 0))
        if amount <= 0:
            return {"commission": 0.0, "stamp_duty": 0.0, "transfer_fee": 0.0, "total_fee": 0.0}
        params = DEFAULT_BROKER_FEE_PARAMS
        commission = max(params["min_commission"], amount * params["commission_rate"])
        stamp_duty = amount * params["stamp_duty_rate"] if str(side).upper() == "SELL" else 0.0
        transfer_fee = amount * params["transfer_fee_rate"]
        total_fee = commission + stamp_duty + transfer_fee
        return {
            "commission": round(commission, 2),
            "stamp_duty": round(stamp_duty, 2),
            "transfer_fee": round(transfer_fee, 2),
            "total_fee": round(total_fee, 2),
        }

    def _trade_clock(self, trade: Dict[str, Any]) -> str:
        raw_time = str(trade.get("time") or "").strip()
        if raw_time:
            if len(raw_time) >= 19:
                return raw_time[:19]
            if len(raw_time) >= 8 and "-" not in raw_time[:8]:
                return f"{trade.get('date', '')} {raw_time[:8]}".strip()
            return raw_time
        return f"{trade.get('date', '')} 15:00:00".strip()

    def account_from_trades(
        self,
        trades: List[Dict[str, Any]],
        initial_cash: Optional[float] = None,
        as_of: Optional[str] = None,
        limit: int = 0,
    ) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        params = self.strategy_params()
        initial_asset = max(1.0, safe_float(initial_cash, params["account_initial_cash"]))
        raw_trades = [trade for trade in trades if isinstance(trade, dict) and not contains_sample_marker(trade)]
        visible_trades = [
            trade
            for trade in raw_trades
            if not as_of or str(trade.get("date", "")) <= as_of
        ]
        visible_trades = sorted(
            enumerate(visible_trades, start=1),
            key=lambda pair: (str(pair[1].get("date") or ""), self._trade_clock(pair[1]), pair[0]),
        )

        lots_by_code: Dict[str, List[Dict[str, Any]]] = {}
        deals: List[Dict[str, Any]] = []
        daily_settlement: Dict[str, Dict[str, float]] = {}
        total_fees = 0.0
        realized_pnl = 0.0
        adjusted_cash = initial_asset

        for index, trade in visible_trades:
            side = str(trade.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue
            code = digits6(trade.get("code"))
            if not code:
                continue
            qty = safe_float(trade.get("qty"), 0)
            price = safe_float(trade.get("price"), 0)
            amount = qty * price
            if qty <= 0 or price <= 0 or amount <= 0:
                continue

            fees = self._broker_fees(side, amount)
            if "total_fee" in trade:
                total_fee = max(0.0, safe_float(trade.get("total_fee"), fees["total_fee"]))
                fees["total_fee"] = round(total_fee, 2)
                fees["commission"] = round(max(0.0, safe_float(trade.get("commission"), fees["commission"])), 2)
                fees["stamp_duty"] = round(max(0.0, safe_float(trade.get("stamp_duty"), fees["stamp_duty"])), 2)
                fees["transfer_fee"] = round(max(0.0, safe_float(trade.get("transfer_fee"), fees["transfer_fee"])), 2)
            total_fees += fees["total_fee"]
            trade_date = str(trade.get("date") or "")
            trade_time = self._trade_clock(trade)
            name = str(trade.get("name") or self.universe.name(code))
            cash_flow = 0.0
            cost_amount = 0.0
            deal_realized = 0.0

            if side == "BUY":
                cash_flow = -(amount + fees["total_fee"])
                cost_amount = amount + fees["total_fee"]
                lots_by_code.setdefault(code, []).append(
                    {
                        "qty": qty,
                        "price": price,
                        "cost_amount": cost_amount,
                        "entry_date": trade_date,
                        "name": name,
                        "reason": trade.get("reason", ""),
                        "buy_score": safe_float(trade.get("score"), 0),
                    }
                )
            else:
                sell_qty_left = qty
                queue = lots_by_code.setdefault(code, [])
                while sell_qty_left > 0 and queue:
                    lot = queue[0]
                    lot_qty = safe_float(lot.get("qty"), 0)
                    if lot_qty <= 0:
                        queue.pop(0)
                        continue
                    matched = min(sell_qty_left, lot_qty)
                    lot_cost = safe_float(lot.get("cost_amount"), 0) * matched / lot_qty
                    cost_amount += lot_cost
                    lot["qty"] = lot_qty - matched
                    lot["cost_amount"] = safe_float(lot.get("cost_amount"), 0) - lot_cost
                    sell_qty_left -= matched
                    if lot["qty"] <= 0.000001:
                        queue.pop(0)
                if sell_qty_left > 0:
                    pnl_pct = safe_float(trade.get("pnl_pct"), 0)
                    fallback_cost_price = price / (1 + pnl_pct / 100) if pnl_pct > -99.0 else price
                    cost_amount += sell_qty_left * fallback_cost_price
                cash_flow = amount - fees["total_fee"]
                deal_realized = cash_flow - cost_amount
                realized_pnl += deal_realized
            adjusted_cash += cash_flow

            deal = {
                "deal_id": f"BT-{trade_date.replace('-', '')}-{index:05d}",
                "date": trade_date,
                "time": trade_time,
                "side": side,
                "direction": "买入" if side == "BUY" else "卖出",
                "code": code,
                "name": name,
                "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
                "price": round(price, 3),
                "amount": round(amount, 2),
                "commission": fees["commission"],
                "stamp_duty": fees["stamp_duty"],
                "transfer_fee": fees["transfer_fee"],
                "total_fee": fees["total_fee"],
                "net_amount": round(cash_flow, 2),
                "cost_amount": round(cost_amount, 2),
                "realized_pnl": round(deal_realized, 2),
                "score": round(safe_float(trade.get("score"), 0), 2) if trade.get("score") is not None else None,
                "pnl_pct": round(safe_float(trade.get("pnl_pct"), 0), 3) if trade.get("pnl_pct") is not None else None,
                "reason": trade.get("reason", ""),
            }
            deals.append(deal)

            bucket = daily_settlement.setdefault(
                trade_date,
                {
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "commission": 0.0,
                    "stamp_duty": 0.0,
                    "transfer_fee": 0.0,
                    "total_fee": 0.0,
                    "net_amount": 0.0,
                    "realized_pnl": 0.0,
                    "deal_count": 0.0,
                },
            )
            if side == "BUY":
                bucket["buy_amount"] += amount
            else:
                bucket["sell_amount"] += amount
            bucket["commission"] += fees["commission"]
            bucket["stamp_duty"] += fees["stamp_duty"]
            bucket["transfer_fee"] += fees["transfer_fee"]
            bucket["total_fee"] += fees["total_fee"]
            bucket["net_amount"] += cash_flow
            bucket["realized_pnl"] += deal_realized
            bucket["deal_count"] += 1

        positions = []
        position_cost = 0.0
        market_value = 0.0
        for code, lots in lots_by_code.items():
            active_lots = [lot for lot in lots if safe_float(lot.get("qty"), 0) > 0]
            if not active_lots:
                continue
            qty = sum(safe_float(lot.get("qty"), 0) for lot in active_lots)
            cost_amount = sum(safe_float(lot.get("cost_amount"), 0) for lot in active_lots)
            first_lot = active_lots[0]
            price_row = self.latest_price(code, as_of=as_of)
            last_price = safe_float((price_row or {}).get("close"), safe_float(first_lot.get("price"), 0))
            cost_price = cost_amount / qty if qty > 0 else last_price
            value = qty * last_price
            pnl_amount = value - cost_amount
            position_cost += cost_amount
            market_value += value
            positions.append(
                {
                    "code": code,
                    "name": first_lot.get("name") or self.universe.name(code),
                    "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
                    "available_qty": int(qty) if float(qty).is_integer() else round(qty, 2),
                    "entry_price": round(safe_float(first_lot.get("price"), cost_price), 3),
                    "cost_price": round(cost_price, 3),
                    "cost_amount": round(cost_amount, 2),
                    "last_price": round(last_price, 3),
                    "last_date": (price_row or {}).get("date", as_of),
                    "market_value": round(value, 2),
                    "pnl_amount": round(pnl_amount, 2),
                    "pnl_pct": round(pnl_amount / cost_amount * 100, 3) if cost_amount > 0 else 0.0,
                    "buy_score": safe_float(first_lot.get("buy_score"), 0),
                    "reason": first_lot.get("reason", ""),
                }
            )

        settlement_rows = [
            {
                "date": date,
                "buy_amount": round(item["buy_amount"], 2),
                "sell_amount": round(item["sell_amount"], 2),
                "commission": round(item["commission"], 2),
                "stamp_duty": round(item["stamp_duty"], 2),
                "transfer_fee": round(item["transfer_fee"], 2),
                "total_fee": round(item["total_fee"], 2),
                "net_amount": round(item["net_amount"], 2),
                "realized_pnl": round(item["realized_pnl"], 2),
                "deal_count": int(item["deal_count"]),
            }
            for date, item in daily_settlement.items()
        ]
        deals.sort(key=lambda item: (item.get("time", ""), item.get("deal_id", "")), reverse=True)
        settlement_rows.sort(key=lambda item: item["date"], reverse=True)
        today_deals = [deal for deal in deals if deal.get("date") == as_of]
        total_asset = adjusted_cash + market_value
        total_pnl = total_asset - initial_asset
        if limit and limit > 0:
            visible_deals = deals[:limit]
            visible_settlements = settlement_rows[:limit]
        else:
            visible_deals = deals
            visible_settlements = settlement_rows
        return {
            "status": "ok",
            "as_of": as_of,
            "account": {
                "initial_cash": round(initial_asset, 2),
                "total_asset": round(total_asset, 2),
                "cash": round(adjusted_cash, 2),
                "available_cash": round(max(0.0, adjusted_cash), 2),
                "market_value": round(market_value, 2),
                "position_cost": round(position_cost, 2),
                "unrealized_pnl": round(market_value - position_cost, 2),
                "realized_pnl": round(realized_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "return_pct": round(total_pnl / initial_asset * 100, 3) if initial_asset > 0 else 0.0,
                "position_count": len(positions),
                "deal_count": len(deals),
                "total_fees": round(total_fees, 2),
            },
            "positions": positions,
            "today_deals": today_deals if not limit else today_deals[:limit],
            "history_deals": visible_deals,
            "delivery_records": visible_deals,
            "daily_settlements": visible_settlements,
        }

    def trading_account(self, as_of: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        limit = max(1, min(int(limit or 500), 2000))
        state = self._load_state()
        portfolio = self.paper_portfolio(as_of=as_of)
        raw_trades = state.get("trades") if isinstance(state.get("trades"), list) else []
        raw_trades = [trade for trade in raw_trades if isinstance(trade, dict) and not contains_sample_marker(trade)]
        visible_trades = [trade for trade in raw_trades if not as_of or str(trade.get("date", "")) <= as_of]

        lots_by_code: Dict[str, List[Dict[str, Any]]] = {}
        deals: List[Dict[str, Any]] = []
        daily_settlement: Dict[str, Dict[str, float]] = {}
        total_fees = 0.0
        realized_pnl = 0.0
        params = self.strategy_params()
        initial_asset = safe_float(state.get("initial_cash"), params["account_initial_cash"])
        adjusted_cash = initial_asset

        for index, trade in enumerate(visible_trades, start=1):
            side = str(trade.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue
            code = digits6(trade.get("code"))
            if not code:
                continue
            qty = safe_float(trade.get("qty"), 0)
            price = safe_float(trade.get("price"), 0)
            amount = qty * price
            if qty <= 0 or price <= 0 or amount <= 0:
                continue

            fees = self._broker_fees(side, amount)
            total_fees += fees["total_fee"]
            trade_date = str(trade.get("date") or "")
            trade_time = self._trade_clock(trade)
            name = str(trade.get("name") or self.universe.name(code))
            cash_flow = 0.0
            cost_amount = 0.0
            deal_realized = 0.0

            if side == "BUY":
                cash_flow = -(amount + fees["total_fee"])
                cost_amount = amount + fees["total_fee"]
                lots_by_code.setdefault(code, []).append(
                    {
                        "qty": qty,
                        "price": price,
                        "cost_amount": cost_amount,
                        "entry_date": trade_date,
                        "name": name,
                        "reason": trade.get("reason", ""),
                        "buy_score": safe_float(trade.get("score"), 0),
                    }
                )
            else:
                sell_qty_left = qty
                queue = lots_by_code.setdefault(code, [])
                while sell_qty_left > 0 and queue:
                    lot = queue[0]
                    lot_qty = safe_float(lot.get("qty"), 0)
                    if lot_qty <= 0:
                        queue.pop(0)
                        continue
                    matched = min(sell_qty_left, lot_qty)
                    lot_cost = safe_float(lot.get("cost_amount"), 0) * matched / lot_qty
                    cost_amount += lot_cost
                    lot["qty"] = lot_qty - matched
                    lot["cost_amount"] = safe_float(lot.get("cost_amount"), 0) - lot_cost
                    sell_qty_left -= matched
                    if lot["qty"] <= 0.000001:
                        queue.pop(0)
                if sell_qty_left > 0:
                    pnl_pct = safe_float(trade.get("pnl_pct"), 0)
                    fallback_cost_price = price / (1 + pnl_pct / 100) if pnl_pct > -99.0 else price
                    cost_amount += sell_qty_left * fallback_cost_price
                cash_flow = amount - fees["total_fee"]
                deal_realized = cash_flow - cost_amount
                realized_pnl += deal_realized
            adjusted_cash += cash_flow

            direction = "买入" if side == "BUY" else "卖出"
            deal = {
                "deal_id": f"{trade_date.replace('-', '')}-{index:05d}",
                "date": trade_date,
                "time": trade_time,
                "side": side,
                "direction": direction,
                "code": code,
                "name": name,
                "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
                "price": round(price, 3),
                "amount": round(amount, 2),
                "commission": fees["commission"],
                "stamp_duty": fees["stamp_duty"],
                "transfer_fee": fees["transfer_fee"],
                "total_fee": fees["total_fee"],
                "net_amount": round(cash_flow, 2),
                "cost_amount": round(cost_amount, 2),
                "realized_pnl": round(deal_realized, 2),
                "score": round(safe_float(trade.get("score"), 0), 2) if trade.get("score") is not None else None,
                "pnl_pct": round(safe_float(trade.get("pnl_pct"), 0), 3) if trade.get("pnl_pct") is not None else None,
                "reason": trade.get("reason", ""),
            }
            deals.append(deal)

            bucket = daily_settlement.setdefault(
                trade_date,
                {
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "commission": 0.0,
                    "stamp_duty": 0.0,
                    "transfer_fee": 0.0,
                    "total_fee": 0.0,
                    "net_amount": 0.0,
                    "realized_pnl": 0.0,
                    "deal_count": 0.0,
                },
            )
            if side == "BUY":
                bucket["buy_amount"] += amount
            else:
                bucket["sell_amount"] += amount
            bucket["commission"] += fees["commission"]
            bucket["stamp_duty"] += fees["stamp_duty"]
            bucket["transfer_fee"] += fees["transfer_fee"]
            bucket["total_fee"] += fees["total_fee"]
            bucket["net_amount"] += cash_flow
            bucket["realized_pnl"] += deal_realized
            bucket["deal_count"] += 1

        remaining_lots = {
            code: [lot for lot in lots if safe_float(lot.get("qty"), 0) > 0]
            for code, lots in lots_by_code.items()
        }
        position_sources: List[Dict[str, Any]] = []
        for code, lots in remaining_lots.items():
            qty = sum(safe_float(lot.get("qty"), 0) for lot in lots)
            if qty <= 0:
                continue
            cost_amount = sum(safe_float(lot.get("cost_amount"), 0) for lot in lots)
            first_lot = lots[0] if lots else {}
            price_row = self.latest_price(code, as_of=as_of)
            last_price = safe_float((price_row or {}).get("close"), safe_float(first_lot.get("price"), 0))
            position_sources.append(
                {
                    "code": code,
                    "name": first_lot.get("name") or self.universe.name(code),
                    "qty": qty,
                    "entry_price": safe_float(first_lot.get("price"), 0),
                    "entry_date": str(first_lot.get("entry_date") or ""),
                    "buy_score": safe_float(first_lot.get("buy_score"), 0),
                    "reason": first_lot.get("reason", ""),
                    "last_price": last_price,
                    "last_date": (price_row or {}).get("date", as_of),
                    "last_time": (price_row or {}).get("time", ""),
                    "price_source": (price_row or {}).get("source", "daily"),
                    "_lot_cost_amount": cost_amount,
                    "_lot_qty": qty,
                }
            )

        existing_codes = {digits6(pos.get("code")) for pos in position_sources}
        for pos in portfolio.get("positions", []):
            code = digits6(pos.get("code"))
            entry_date = str(pos.get("entry_date") or "")
            if not code or code in existing_codes or (entry_date and entry_date > as_of):
                continue
            position_sources.append(pos)
            existing_codes.add(code)

        enriched_positions = []
        position_cost = 0.0
        market_value = 0.0
        for pos in position_sources:
            code = digits6(pos.get("code"))
            qty = safe_float(pos.get("qty"), 0)
            last_price = safe_float(pos.get("last_price"), pos.get("entry_price", 0))
            raw_entry_price = safe_float(pos.get("entry_price"), last_price)
            lots = remaining_lots.get(code, [])
            lot_qty = sum(safe_float(lot.get("qty"), 0) for lot in lots)
            lot_cost = safe_float(pos.get("_lot_cost_amount"), 0) or sum(safe_float(lot.get("cost_amount"), 0) for lot in lots)
            if lot_qty > 0:
                cost_price = lot_cost / lot_qty
            else:
                cost_price = raw_entry_price
                lot_cost = qty * cost_price
            cost_amount = qty * cost_price
            value = qty * last_price
            pnl_amount = value - cost_amount
            pnl_pct = pnl_amount / cost_amount * 100 if cost_amount > 0 else 0.0
            position_cost += cost_amount
            market_value += value
            entry_date = str(pos.get("entry_date") or "")
            enriched_positions.append(
                {
                    **pos,
                    "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
                    "available_qty": int(qty) if entry_date < as_of and float(qty).is_integer() else (round(qty, 2) if entry_date < as_of else 0),
                    "frozen_qty": 0 if entry_date < as_of else (int(qty) if float(qty).is_integer() else round(qty, 2)),
                    "cost_price": round(cost_price, 3),
                    "cost_amount": round(cost_amount, 2),
                    "last_price": round(last_price, 3),
                    "market_value": round(value, 2),
                    "pnl_amount": round(pnl_amount, 2),
                    "pnl_pct": round(pnl_pct, 3),
                }
            )

        state_cash = safe_float(state.get("cash"), initial_asset)
        total_asset = adjusted_cash + market_value
        total_pnl = total_asset - initial_asset
        settlement_rows = []
        for date, item in daily_settlement.items():
            settlement_rows.append(
                {
                    "date": date,
                    "buy_amount": round(item["buy_amount"], 2),
                    "sell_amount": round(item["sell_amount"], 2),
                    "commission": round(item["commission"], 2),
                    "stamp_duty": round(item["stamp_duty"], 2),
                    "transfer_fee": round(item["transfer_fee"], 2),
                    "total_fee": round(item["total_fee"], 2),
                    "net_amount": round(item["net_amount"], 2),
                    "realized_pnl": round(item["realized_pnl"], 2),
                    "deal_count": int(item["deal_count"]),
                }
            )

        deals.sort(key=lambda item: (item.get("time", ""), item.get("deal_id", "")), reverse=True)
        settlement_rows.sort(key=lambda item: item["date"], reverse=True)
        today_deals = [deal for deal in deals if deal.get("date") == as_of]
        return {
            "status": "ok",
            "as_of": as_of,
            "account": {
                "total_asset": round(total_asset, 2),
                "cash": round(adjusted_cash, 2),
                "available_cash": round(max(0.0, adjusted_cash), 2),
                "frozen_cash": 0.0,
                "state_cash_gross": round(state_cash, 2),
                "market_value": round(market_value, 2),
                "position_cost": round(position_cost, 2),
                "unrealized_pnl": round(market_value - position_cost, 2),
                "realized_pnl": round(realized_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "return_pct": round(total_pnl / initial_asset * 100, 3) if initial_asset > 0 else 0.0,
                "position_count": len(enriched_positions),
                "deal_count": len(deals),
                "total_fees": round(total_fees, 2),
            },
            "fee_rules": DEFAULT_BROKER_FEE_PARAMS,
            "positions": enriched_positions,
            "today_deals": today_deals[:limit],
            "history_deals": deals[:limit],
            "delivery_records": deals[:limit],
            "daily_settlements": settlement_rows[:limit],
            "portfolio": portfolio,
        }

    def run_paper_trading(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        params = self.strategy_params()
        state = self._load_state()
        recommendations = self.recommendations(as_of=as_of, lookback_days=2, top_n=20)
        by_code = {item["code"]: item for item in recommendations.get("items", [])}
        state.setdefault("initial_cash", params["account_initial_cash"])
        cash = safe_float(state.get("cash"), params["account_initial_cash"])
        positions = state.get("positions") if isinstance(state.get("positions"), list) else []
        trades = state.get("trades") if isinstance(state.get("trades"), list) else []
        next_positions = []

        for pos in positions:
            code = digits6(pos.get("code"))
            if is_sample_code(code) or contains_sample_marker(pos):
                continue
            price_row = self.latest_price(code, as_of=as_of)
            if not code or not price_row:
                next_positions.append(pos)
                continue
            last_price = safe_float(price_row.get("close"), 0)
            entry_price = safe_float(pos.get("entry_price"), last_price)
            qty = safe_float(pos.get("qty"), 0)
            pnl_pct = (last_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
            rec = by_code.get(code, {})
            entry_date = str(pos.get("entry_date") or as_of)
            hold_days = sum(1 for row in self.load_kline(code) if entry_date <= row["date"] <= as_of)
            should_sell = (
                pnl_pct <= params["stop_loss_pct"]
                or pnl_pct >= params["take_profit_pct"]
                or hold_days >= int(params["paper_max_hold_days"])
                or safe_float(rec.get("sell_score"), 0) >= params["sell_score_threshold"]
            )
            if should_sell and qty > 0 and last_price > 0:
                cash += qty * last_price
                trades.append(
                    {
                        "side": "SELL",
                        "date": as_of,
                        "code": code,
                        "name": self.universe.name(code),
                        "qty": qty,
                        "price": round(last_price, 3),
                        "pnl_pct": round(pnl_pct, 3),
                        "reason": "止盈/止损/持仓到期/卖出评分触发",
                    }
                )
            else:
                next_positions.append(pos)

        held = {digits6(pos.get("code")) for pos in next_positions}
        slots = max(0, int(params["max_positions"]) - len(next_positions))
        buy_candidates = [
            item
            for item in recommendations.get("items", [])
            if item["action"] == "买入候选"
            and item["code"] not in held
            and not contains_sample_marker(item)
            and safe_float(item.get("buy_score"), 0) >= params["buy_threshold"]
        ][:slots]
        for item in buy_candidates:
            price_row = self.latest_price(item["code"], as_of=as_of)
            if not price_row:
                continue
            price = safe_float(price_row.get("close"), 0)
            if price <= 0:
                continue
            allocation = min(cash / max(1, slots), params["paper_position_value"])
            qty = math.floor(allocation / price / 100) * 100
            if qty <= 0:
                continue
            cash -= qty * price
            next_positions.append(
                {
                    "code": item["code"],
                    "name": item["name"],
                    "qty": qty,
                    "entry_price": round(price, 3),
                    "entry_date": as_of,
                    "buy_score": item["buy_score"],
                    "reason": item["reason"][:180],
                }
            )
            trades.append(
                {
                    "side": "BUY",
                    "date": as_of,
                    "code": item["code"],
                    "name": item["name"],
                    "qty": qty,
                    "price": round(price, 3),
                    "score": item["buy_score"],
                    "reason": item["reason"][:180],
                }
            )
            slots = max(1, slots - 1)

        state["cash"] = round(cash, 2)
        state["positions"] = next_positions
        state["trades"] = trades[-500:]
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        state["as_of"] = as_of
        self._save_state(state)
        return self.paper_portfolio(as_of=as_of)

    def fit_strategy(
        self,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        apply_best: bool = True,
    ) -> Dict[str, Any]:
        end_date = end_date or as_of or self.latest_event_date()
        start_date = start_date or self.first_data_date()
        base = self.strategy_params()

        def normalized_candidate(name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
            return {"name": name, "params": self._normalize_strategy_params({**base, **updates})}

        candidates = [
            normalized_candidate("当前参数", {}),
            normalized_candidate(
                "进攻型",
                {
                    "buy_threshold": base["buy_threshold"] - 4,
                    "watch_threshold": base["watch_threshold"] - 3,
                    "take_profit_pct": base["take_profit_pct"] + 2,
                    "sentiment_weight": base["sentiment_weight"] + 0.04,
                    "event_weight": base["event_weight"] + 0.04,
                    "risk_weight": base["risk_weight"] - 0.05,
                },
            ),
            normalized_candidate(
                "保守型",
                {
                    "buy_threshold": base["buy_threshold"] + 4,
                    "sell_score_threshold": base["sell_score_threshold"] - 4,
                    "stop_loss_pct": base["stop_loss_pct"] + 1,
                    "risk_weight": base["risk_weight"] + 0.08,
                    "sentiment_weight": base["sentiment_weight"] - 0.03,
                    "event_weight": base["event_weight"] - 0.03,
                },
            ),
            normalized_candidate(
                "短线快进快出",
                {
                    "max_hold_days": max(1, base["max_hold_days"] - 1),
                    "paper_max_hold_days": max(2, base["paper_max_hold_days"] - 2),
                    "take_profit_pct": max(3, base["take_profit_pct"] - 2),
                    "stop_loss_pct": base["stop_loss_pct"] + 1,
                    "technical_weight": base["technical_weight"] + 0.08,
                },
            ),
            normalized_candidate(
                "事件驱动",
                {
                    "event_weight": base["event_weight"] + 0.1,
                    "history_score_weight": base["history_score_weight"] + 0.12,
                    "event_impact_weight": base["event_impact_weight"] - 0.12,
                    "technical_weight": base["technical_weight"] - 0.05,
                },
            ),
            normalized_candidate(
                "风控优先",
                {
                    "risk_weight": base["risk_weight"] + 0.12,
                    "stop_loss_pct": base["stop_loss_pct"] + 1,
                    "sell_score_threshold": base["sell_score_threshold"] - 6,
                    "take_profit_pct": base["take_profit_pct"] - 1,
                },
            ),
        ]

        results = []
        for item in candidates:
            with self.temporary_strategy_params(item["params"]):
                timeline = self.walk_forward(
                    start_date=start_date,
                    end_date=end_date,
                )
            return_pct = safe_float(timeline.get("return_pct"), 0)
            drawdown = abs(safe_float(timeline.get("max_drawdown_pct"), 0))
            win_rate = safe_float(timeline.get("win_rate"), 0)
            closed_trades = safe_float(timeline.get("closed_trades"), 0)
            performance = timeline.get("performance") if isinstance(timeline.get("performance"), dict) else {}
            sharpe_ratio = safe_float(performance.get("sharpe_ratio"), 0)
            profit_factor = safe_float(performance.get("profit_factor"), 0)
            trade_penalty = 10.0 if closed_trades < 5 else 0.0
            objective = (
                return_pct
                - drawdown * 0.75
                + sharpe_ratio * 3.0
                + min(max(profit_factor, 0), 4) * 1.2
                + win_rate * 0.025
                + min(closed_trades, 50) * 0.02
                - trade_penalty
            )
            results.append(
                {
                    "name": item["name"],
                    "objective": round(objective, 4),
                    "return_pct": round(return_pct, 3),
                    "max_drawdown_pct": round(safe_float(timeline.get("max_drawdown_pct"), 0), 3),
                    "sharpe_ratio": round(sharpe_ratio, 4),
                    "profit_factor": round(profit_factor, 4),
                    "win_rate": round(win_rate, 2),
                    "closed_trades": int(closed_trades),
                    "params": item["params"],
                }
            )

        results.sort(key=lambda item: item["objective"], reverse=True)
        best = results[0] if results else {"params": base}
        applied = False
        if apply_best and best.get("params"):
            self.update_strategy_params(best["params"])
            applied = True
            state = self._load_state()
            state["last_fit"] = {
                "as_of": end_date,
                "start_date": start_date,
                "best_name": best.get("name", ""),
                "objective": best.get("objective", 0),
                "return_pct": best.get("return_pct", 0),
                "max_drawdown_pct": best.get("max_drawdown_pct", 0),
                "win_rate": best.get("win_rate", 0),
                "closed_trades": best.get("closed_trades", 0),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._save_state(state)
        return {
            "status": "ok",
            "as_of": end_date,
            "start_date": start_date,
            "applied": applied,
            "best": best,
            "candidates": results,
            "strategy_params": self.strategy_params(),
        }

    def daily_plan(
        self,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        limit_days: int = 80,
    ) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        params = self.strategy_params()
        limit_days = max(1, min(int(limit_days or 80), 500))
        recommendations = self.recommendations(as_of=as_of, lookback_days=2, top_n=100)
        buy_list = [
            item
            for item in recommendations.get("items", [])
            if item.get("action") == "买入候选"
            and not contains_sample_marker(item)
        ]
        timeline = self.walk_forward(start_date=start_date, end_date=as_of)
        trades = timeline.get("trades", [])
        open_buys: Dict[str, List[Dict[str, Any]]] = {}
        completed = []
        all_buys = []

        for trade in trades:
            code = digits6(trade.get("code"))
            if not code:
                continue
            side = str(trade.get("side") or "").upper()
            if side == "BUY":
                buy = dict(trade)
                buy.setdefault("signal_date", trade.get("date", ""))
                buy["status"] = "持仓中"
                open_buys.setdefault(code, []).append(buy)
                all_buys.append(buy)
            elif side == "SELL":
                queue = open_buys.get(code) or []
                buy = queue.pop(0) if queue else {}
                entry_price = safe_float(buy.get("price"), 0)
                exit_price = safe_float(trade.get("price"), 0)
                pnl_pct = safe_float(trade.get("pnl_pct"), 0)
                if pnl_pct == 0 and entry_price > 0 and exit_price > 0:
                    pnl_pct = (exit_price / entry_price - 1) * 100
                completed.append(
                    {
                        "signal_date": buy.get("signal_date") or trade.get("date", ""),
                        "buy_date": buy.get("date", ""),
                        "sell_date": trade.get("date", ""),
                        "code": code,
                        "name": trade.get("name") or buy.get("name") or self.universe.name(code),
                        "qty": safe_float(buy.get("qty"), trade.get("qty", 0)),
                        "buy_price": round(entry_price, 3),
                        "sell_price": round(exit_price, 3),
                        "buy_score": safe_float(buy.get("score"), 0),
                        "pnl_pct": round(pnl_pct, 3),
                        "status": "已卖出",
                        "buy_reason": buy.get("reason", ""),
                        "sell_reason": trade.get("reason", ""),
                    }
                )

        held_records = []
        for queue in open_buys.values():
            for buy in queue:
                code = digits6(buy.get("code"))
                price_row = self.latest_price(code, as_of=as_of)
                last_price = safe_float((price_row or {}).get("close"), buy.get("price", 0))
                entry_price = safe_float(buy.get("price"), last_price)
                pnl_pct = (last_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
                held_records.append(
                    {
                        "signal_date": buy.get("signal_date") or buy.get("date", ""),
                        "buy_date": buy.get("date", ""),
                        "sell_date": "",
                        "code": code,
                        "name": buy.get("name") or self.universe.name(code),
                        "qty": safe_float(buy.get("qty"), 0),
                        "buy_price": round(entry_price, 3),
                        "sell_price": 0.0,
                        "last_price": round(last_price, 3),
                        "buy_score": safe_float(buy.get("score"), 0),
                        "pnl_pct": round(pnl_pct, 3),
                        "status": "持仓中",
                        "buy_reason": buy.get("reason", ""),
                        "sell_reason": "",
                    }
                )

        missed_records = []
        for day in timeline.get("days", []):
            for miss in day.get("missed", []):
                code = digits6(miss.get("code"))
                if not code:
                    continue
                missed_records.append(
                    {
                        "signal_date": miss.get("signal_date") or day.get("date", ""),
                        "buy_date": "",
                        "sell_date": "",
                        "code": code,
                        "name": miss.get("name") or self.universe.name(code),
                        "qty": 0,
                        "buy_price": 0.0,
                        "sell_price": 0.0,
                        "buy_score": safe_float(miss.get("score"), 0),
                        "pnl_pct": 0.0,
                        "status": "未成交",
                        "unfilled_reason": miss.get("unfilled_reason", ""),
                        "buy_reason": miss.get("reason", ""),
                        "sell_reason": "",
                    }
                )

        outcomes = completed + held_records + missed_records
        outcome_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in outcomes:
            outcome_map[(str(item.get("signal_date") or ""), digits6(item.get("code")))] = item

        day_rows = []
        for day in timeline.get("days", []):
            signal_items = []
            for signal in day.get("signals", []):
                code = digits6(signal.get("code"))
                outcome = outcome_map.get((day.get("date", ""), code), {})
                if not outcome:
                    execute_on = str(signal.get("execute_on", "") or "")
                    outcome = {
                        "status": "未成交" if execute_on and execute_on <= as_of else "待执行",
                        "pnl_pct": 0.0,
                        "unfilled_reason": "回放结束前未建仓" if execute_on and execute_on <= as_of else "等待下一交易日执行",
                    }
                signal_items.append(
                    {
                        "signal_date": day.get("date", ""),
                        "execute_on": signal.get("execute_on", ""),
                        "code": code,
                        "name": signal.get("name") or self.universe.name(code),
                        "buy_score": safe_float(signal.get("buy_score"), 0),
                        "sell_score": safe_float(signal.get("sell_score"), 0),
                        "reason": signal.get("reason", ""),
                        "outcome": outcome,
                    }
                )
            if signal_items:
                sold = [item.get("outcome", {}) for item in signal_items if item.get("outcome", {}).get("status") == "已卖出"]
                held = [item.get("outcome", {}) for item in signal_items if item.get("outcome", {}).get("status") == "持仓中"]
                avg_pnl = statistics.mean(safe_float(item.get("pnl_pct"), 0) for item in sold) if sold else 0.0
                day_rows.append(
                    {
                        "date": day.get("date", ""),
                        "signal_count": len(signal_items),
                        "sold_count": len(sold),
                        "holding_count": len(held),
                        "avg_sold_pnl_pct": round(avg_pnl, 3),
                        "signals": signal_items,
                    }
                )

        day_rows = day_rows[-limit_days:]
        current_rules = {
            "buy_threshold": params["buy_threshold"],
            "sell_score_threshold": params["sell_score_threshold"],
            "stop_loss_pct": params["stop_loss_pct"],
            "take_profit_pct": params["take_profit_pct"],
            "max_hold_days": params["max_hold_days"],
            "max_positions": params["max_positions"],
            "top_n": params["top_n"],
        }
        return {
            "status": "ok",
            "as_of": as_of,
            "current_rules": current_rules,
            "buy_list": buy_list,
            "history_days": list(reversed(day_rows)),
            "outcomes": sorted(outcomes, key=lambda item: (item.get("signal_date", ""), item.get("code", "")), reverse=True),
            "timeline_summary": {
                "start_date": timeline.get("start_date", ""),
                "end_date": timeline.get("end_date", ""),
                "return_pct": timeline.get("return_pct", 0),
                "closed_trades": timeline.get("closed_trades", 0),
                "win_rate": timeline.get("win_rate", 0),
                "trade_count": len(trades),
            },
        }

    def dashboard(self, as_of: Optional[str] = None, include_heavy: bool = True) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        params = self.strategy_params()
        recs = self.recommendations(as_of=as_of, lookback_days=2, top_n=30)
        backtest = self.backtest(as_of=as_of, hold_days=int(params["max_hold_days"]), top_n=int(params["top_n"])) if include_heavy else {}
        timeline = self.walk_forward(end_date=as_of) if include_heavy else {}
        portfolio = self.paper_portfolio(as_of=as_of)
        events = self.events()
        state = self._load_state()
        return {
            "status": "ok",
            "version": "quant-refactor-0.1",
            "as_of": as_of,
            "data": {
                "news_count": len(self.load_news_history()),
                "ai_record_count": len(self.load_analysis_records()),
                "event_count": len(events),
                "stock_count": len(self.universe.code_to_name),
                "kline_stock_count": len(list(KLINE_DAY_DIR.glob("*.json"))) if KLINE_DAY_DIR.exists() else 0,
                "lhb_record_count": len(self.load_lhb_records(limit=200000)),
            },
            "recommendations": recs,
            "backtest": backtest,
            "timeline": timeline,
            "portfolio": portfolio,
            "strategy_params": params,
            "last_fit": state.get("last_fit", {}),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }


quant_engine = QuantEngine()
