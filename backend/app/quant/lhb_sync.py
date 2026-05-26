from __future__ import annotations

import csv
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from app.quant.engine import quant_engine
from app.quant.engine_utils import digits6, safe_float
from app.quant.quant_paths import LHB_HISTORY_FILE


EASTMONEY_DATA_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
LHB_FIELDS = ["trade_date", "stock_code", "stock_name", "buyer_seat_name", "buy_amount", "sell_amount", "hot_money"]
HTTP_SESSION = requests.Session()
HTTP_SESSION.trust_env = False


def _normalize_date(value: Optional[str]) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 8:
        return ""
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _ymd8(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _request(params: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://data.eastmoney.com/stock/tradedetail.html",
    }
    response = HTTP_SESSION.get(EASTMONEY_DATA_URL, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _result_data(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = payload.get("result") if isinstance(payload, dict) else {}
    data = result.get("data") if isinstance(result, dict) else []
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _result_pages(payload: Dict[str, Any]) -> int:
    result = payload.get("result") if isinstance(payload, dict) else {}
    return max(1, int(safe_float(result.get("pages") if isinstance(result, dict) else 1, 1)))


def _classify_seat(seat_name: str) -> str:
    name = str(seat_name or "")
    if not name:
        return ""
    if "机构专用" in name:
        return "机构"
    if "沪股通" in name or "深股通" in name:
        return "北向资金"
    if "拉萨" in name or "东方财富证券股份有限公司" in name and "拉萨" in name:
        return "拉萨席位"
    if "量化" in name:
        return "量化席位"
    if "总部" in name or "北京金融大街" in name or "上海分公司" in name:
        return "活跃营业部"
    return ""


def read_lhb_rows() -> List[Dict[str, Any]]:
    if not LHB_HISTORY_FILE.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with LHB_HISTORY_FILE.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if not isinstance(row, dict):
                        continue
                    date = _normalize_date(row.get("trade_date"))
                    code = digits6(row.get("stock_code"))
                    seat = str(row.get("buyer_seat_name") or "").strip()
                    if not date or not code or not seat:
                        continue
                    rows.append(
                        {
                            "trade_date": date,
                            "stock_code": code,
                            "stock_name": str(row.get("stock_name") or quant_engine.universe.name(code)).strip(),
                            "buyer_seat_name": seat,
                            "buy_amount": safe_float(row.get("buy_amount"), 0),
                            "sell_amount": safe_float(row.get("sell_amount"), 0),
                            "hot_money": str(row.get("hot_money") or "").strip(),
                        }
                    )
            break
        except UnicodeDecodeError:
            rows = []
            continue
        except Exception:
            return []
    rows.sort(key=lambda item: (item["trade_date"], item["stock_code"], item["buy_amount"]), reverse=True)
    return rows


def lhb_status() -> Dict[str, Any]:
    rows = read_lhb_rows()
    dates = sorted({row["trade_date"] for row in rows}, reverse=True)
    codes = {row["stock_code"] for row in rows}
    return {
        "status": "ok",
        "file": str(LHB_HISTORY_FILE),
        "exists": LHB_HISTORY_FILE.exists(),
        "rows": len(rows),
        "stock_count": len(codes),
        "latest_date": dates[0] if dates else "",
        "recent_dates": dates[:20],
    }


def _summary_rows(start_date: str, end_date: str, max_pages: int = 20) -> List[Dict[str, Any]]:
    params = {
        "sortColumns": "SECURITY_CODE,TRADE_DATE",
        "sortTypes": "1,-1",
        "pageSize": "5000",
        "pageNumber": "1",
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
        "columns": (
            "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,BILLBOARD_NET_AMT,"
            "BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,EXPLANATION"
        ),
        "source": "WEB",
        "client": "WEB",
        "filter": f"(TRADE_DATE<='{end_date}')(TRADE_DATE>='{start_date}')",
    }
    first = _request(params)
    pages = min(_result_pages(first), max(1, int(max_pages or 20)))
    rows = _result_data(first)
    for page in range(2, pages + 1):
        params["pageNumber"] = str(page)
        rows.extend(_result_data(_request(params)))
        time.sleep(0.05)
    out = []
    for row in rows:
        code = digits6(row.get("SECURITY_CODE"))
        date = _normalize_date(row.get("TRADE_DATE"))
        if not code or not date:
            continue
        out.append(
            {
                "trade_date": date,
                "stock_code": code,
                "stock_name": str(row.get("SECURITY_NAME_ABBR") or quant_engine.universe.name(code)).strip(),
                "net_amount": safe_float(row.get("BILLBOARD_NET_AMT"), 0),
                "buy_amount": safe_float(row.get("BILLBOARD_BUY_AMT"), 0),
                "sell_amount": safe_float(row.get("BILLBOARD_SELL_AMT"), 0),
                "reason": str(row.get("EXPLANATION") or "").strip(),
            }
        )
    return out


def _seat_rows_for_stock(code: str, date: str, stock_name: str = "") -> List[Dict[str, Any]]:
    code = digits6(code)
    date = _normalize_date(date)
    if not code or not date:
        return []
    by_seat: Dict[str, Dict[str, Any]] = {}
    for side, report_name, sort_col in (
        ("buy", "RPT_BILLBOARD_DAILYDETAILSBUY", "BUY"),
        ("sell", "RPT_BILLBOARD_DAILYDETAILSSELL", "SELL"),
    ):
        params = {
            "reportName": report_name,
            "columns": "ALL",
            "filter": f"(TRADE_DATE='{date}')(SECURITY_CODE=\"{code}\")",
            "pageNumber": "1",
            "pageSize": "500",
            "sortTypes": "-1",
            "sortColumns": sort_col,
            "source": "WEB",
            "client": "WEB",
        }
        for row in _result_data(_request(params)):
            seat = str(row.get("OPERATEDEPT_NAME") or "").strip()
            if not seat:
                continue
            item = by_seat.setdefault(
                seat,
                {
                    "trade_date": date,
                    "stock_code": code,
                    "stock_name": stock_name or quant_engine.universe.name(code),
                    "buyer_seat_name": seat,
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "hot_money": _classify_seat(seat),
                },
            )
            item["buy_amount"] = max(safe_float(item.get("buy_amount"), 0), safe_float(row.get("BUY"), 0))
            item["sell_amount"] = max(safe_float(item.get("sell_amount"), 0), safe_float(row.get("SELL"), 0))
        time.sleep(0.03)
    return list(by_seat.values())


def _merge_rows(existing: Iterable[Dict[str, Any]], incoming: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    merged: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in existing:
        key = (str(row.get("trade_date") or ""), digits6(row.get("stock_code")), str(row.get("buyer_seat_name") or ""))
        if key[0] and key[1] and key[2]:
            merged[key] = dict(row)
    before = len(merged)
    for row in incoming:
        key = (str(row.get("trade_date") or ""), digits6(row.get("stock_code")), str(row.get("buyer_seat_name") or ""))
        if not key[0] or not key[1] or not key[2]:
            continue
        old = merged.get(key, {})
        merged[key] = {
            "trade_date": key[0],
            "stock_code": key[1],
            "stock_name": row.get("stock_name") or old.get("stock_name") or quant_engine.universe.name(key[1]),
            "buyer_seat_name": key[2],
            "buy_amount": max(safe_float(old.get("buy_amount"), 0), safe_float(row.get("buy_amount"), 0)),
            "sell_amount": max(safe_float(old.get("sell_amount"), 0), safe_float(row.get("sell_amount"), 0)),
            "hot_money": row.get("hot_money") or old.get("hot_money") or _classify_seat(key[2]),
        }
    rows = list(merged.values())
    rows.sort(key=lambda item: (item["trade_date"], item["stock_code"], safe_float(item.get("buy_amount"), 0)), reverse=True)
    return rows, max(0, len(merged) - before)


def _write_rows(rows: List[Dict[str, Any]]) -> None:
    LHB_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LHB_HISTORY_FILE.with_suffix(LHB_HISTORY_FILE.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LHB_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in LHB_FIELDS})
    tmp.replace(LHB_HISTORY_FILE)


def sync_lhb(
    start_date: str,
    end_date: str,
    max_stock_days: int = 300,
    force: bool = False,
) -> Dict[str, Any]:
    start_date = _normalize_date(start_date)
    end_date = _normalize_date(end_date)
    if not start_date or not end_date:
        return {"status": "error", "message": "龙虎榜拉取日期无效"}
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    existing = read_lhb_rows()
    existing_keys = {(row["trade_date"], row["stock_code"]) for row in existing}
    try:
        summary = _summary_rows(start_date=start_date, end_date=end_date)
    except Exception as exc:
        return {
            "status": "error",
            "source": "eastmoney",
            "start_date": start_date,
            "end_date": end_date,
            "message": f"龙虎榜概要拉取失败：{exc}",
            "total_rows": len(existing),
        }
    selected = []
    seen = set()
    for row in sorted(summary, key=lambda item: (item["trade_date"], abs(safe_float(item.get("net_amount"), 0))), reverse=True):
        key = (row["trade_date"], row["stock_code"])
        if key in seen:
            continue
        if not force and key in existing_keys:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= max(1, min(int(max_stock_days or 300), 2000)):
            break

    incoming: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for row in selected:
        try:
            incoming.extend(_seat_rows_for_stock(row["stock_code"], row["trade_date"], row.get("stock_name", "")))
        except Exception as exc:
            errors.append({"date": row.get("trade_date"), "code": row.get("stock_code"), "error": str(exc)})
        time.sleep(0.05)

    merged, added = _merge_rows(existing, incoming)
    if incoming:
        _write_rows(merged)
        try:
            quant_engine.events(force=True)
        except Exception:
            pass

    return {
        "status": "partial" if errors else "ok",
        "source": "eastmoney",
        "start_date": start_date,
        "end_date": end_date,
        "summary_stock_days": len(summary),
        "requested_stock_days": len(selected),
        "seat_rows_fetched": len(incoming),
        "added_rows": added,
        "total_rows": len(merged),
        "errors": errors[:20],
    }
