from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from app.quant.engine import quant_engine
from app.quant.engine_utils import item_datetime, read_json, safe_float, short_hash, write_json
from app.quant.event_classifier import EventClassifier
from app.quant.quant_paths import DATA_DIR
from app.quant.strategy_defaults import DEFAULT_AI_MODEL


ANALYSIS_RECORDS_FILE = DATA_DIR / "news_analysis_records.json"
AI_ANALYSIS_STATE_FILE = DATA_DIR / "ai_analysis_state.json"
CONFIG_FILE = DATA_DIR / "config.json"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"


def _news_id(item: Dict[str, Any]) -> str:
    explicit = str(item.get("id") or "").strip()
    if explicit:
        return explicit
    timestamp = str(item.get("timestamp") or "")
    source = str(item.get("source") or "")
    text = str(item.get("text") or "")
    return short_hash(f"{timestamp}|{source}|{text[:240]}")


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        pass
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if match:
        try:
            payload = json.loads(match.group(1))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(raw[start : end + 1])
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    return {}


class AINewsAnalyzer:
    def __init__(self) -> None:
        self.records_file = ANALYSIS_RECORDS_FILE
        self.state_file = AI_ANALYSIS_STATE_FILE
        self.classifier = EventClassifier()
        self._lock = threading.RLock()

    def config(self) -> Dict[str, Any]:
        payload = read_json(CONFIG_FILE, {})
        if not isinstance(payload, dict):
            payload = {}
        api_keys = payload.get("api_keys") if isinstance(payload.get("api_keys"), dict) else {}
        cost_cfg = payload.get("ai_cost_config") if isinstance(payload.get("ai_cost_config"), dict) else {}
        default_cfg = cost_cfg.get("default") if isinstance(cost_cfg.get("default"), dict) else {}
        api_key = os.getenv("DEEPSEEK_API_KEY") or str(api_keys.get("deepseek") or "").strip()
        model = os.getenv("DEEPSEEK_MODEL") or str(default_cfg.get("model") or DEFAULT_AI_MODEL).strip() or DEFAULT_AI_MODEL
        return {
            "enabled": bool(api_key),
            "provider": "deepseek",
            "model": model,
            "api_key": api_key,
            "endpoint": DEEPSEEK_API_URL,
        }

    def records(self) -> List[Dict[str, Any]]:
        payload = read_json(self.records_file, [])
        return payload if isinstance(payload, list) else []

    def analyzed_news_ids(self) -> set:
        seen = set()
        for record in self.records():
            if not isinstance(record, dict):
                continue
            news_ids = record.get("news_ids") if isinstance(record.get("news_ids"), list) else []
            for news_id in news_ids:
                if str(news_id).strip():
                    seen.add(str(news_id).strip())
        return seen

    def select_unanalyzed(self, as_of: Optional[str] = None, max_items: int = 8) -> List[Dict[str, Any]]:
        max_items = max(1, min(int(max_items or 8), 50))
        analyzed = self.analyzed_news_ids()
        history = quant_engine.load_news_history()
        rows = []
        for item in history:
            if not isinstance(item, dict):
                continue
            dt = item_datetime(item)
            if not dt:
                continue
            date = dt.strftime("%Y-%m-%d")
            if as_of and date > as_of:
                continue
            news_id = _news_id(item)
            if news_id in analyzed:
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            normalized = dict(item)
            normalized["id"] = news_id
            normalized.setdefault("time_str", dt.strftime("%Y-%m-%d %H:%M:%S"))
            normalized.setdefault("source", "新闻")
            rows.append((dt, normalized))
        rows.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in rows[:max_items]]

    def run(self, as_of: Optional[str] = None, max_items: int = 8, batch_size: int = 4) -> Dict[str, Any]:
        max_items = max(1, min(int(max_items or 8), 50))
        batch_size = max(1, min(int(batch_size or 4), 10))
        started_at = datetime.now().isoformat(timespec="seconds")
        candidates = self.select_unanalyzed(as_of=as_of, max_items=max_items)
        if not candidates:
            result = {
                "status": "ok",
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "selected": 0,
                "records_added": 0,
                "stocks": 0,
                "fallback_records": 0,
                "message": "no unanalyzed news",
            }
            self._write_state(result)
            return result

        added = 0
        stocks = 0
        fallback_records = 0
        failures = []
        for offset in range(0, len(candidates), batch_size):
            batch = candidates[offset : offset + batch_size]
            try:
                record = self._analyze_batch(batch)
            except Exception as exc:
                record = self._fallback_record(batch, error=str(exc))
            if record.get("analysis_source") == "fallback":
                fallback_records += 1
            added += self._append_record(record)
            result_stocks = (record.get("result") or {}).get("stocks") if isinstance(record.get("result"), dict) else []
            stocks += len(result_stocks or [])
            if record.get("error"):
                failures.append(str(record.get("error"))[:160])

        quant_engine.events(force=True)
        result = {
            "status": "ok",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "selected": len(candidates),
            "records_added": added,
            "stocks": stocks,
            "fallback_records": fallback_records,
            "failures": failures[:5],
        }
        self._write_state(result)
        return result

    def _analyze_batch(self, batch: List[Dict[str, Any]]) -> Dict[str, Any]:
        cfg = self.config()
        if not cfg["enabled"]:
            return self._fallback_record(batch, error="deepseek api key missing")

        prompt = self._build_prompt(batch)
        payload = {
            "model": cfg["model"],
            "messages": [
                {
                    "role": "system",
                    "content": "你是A股量化新闻结构化分析Agent。只输出合法JSON，不要输出Markdown。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(cfg["endpoint"], headers=headers, json=payload, timeout=45)
            if response.status_code >= 400:
                payload.pop("response_format", None)
                response = requests.post(cfg["endpoint"], headers=headers, json=payload, timeout=45)
            if response.status_code != 200:
                return self._fallback_record(batch, error=f"deepseek status {response.status_code}")
            raw = response.json()
            content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            parsed = _extract_json_object(content)
            if not parsed:
                return self._fallback_record(batch, error="deepseek returned invalid json")
            record = self._record_from_ai(batch, parsed, raw)
            if not (record.get("result") or {}).get("stocks"):
                fallback = self._fallback_record(batch, error="deepseek returned no stocks")
                record["result"]["stocks"] = fallback["result"]["stocks"]
                record["analysis_source"] = "ai_with_rule_fallback"
            return record
        except Exception as exc:
            return self._fallback_record(batch, error=str(exc))

    def _build_prompt(self, batch: List[Dict[str, Any]]) -> str:
        news_lines = []
        for idx, item in enumerate(batch, start=1):
            news_lines.append(
                f"{idx}. id={item.get('id')} time={item.get('time_str')} source={item.get('source')}\n{str(item.get('text') or '')[:900]}"
            )
        return (
            "请分析以下A股相关新闻，输出JSON。只允许输出一个JSON对象。\n"
            "字段格式：\n"
            "{\n"
            '  "summary": "简短总结",\n'
            '  "stocks": [\n'
            '    {"code":"6位A股代码","name":"股票名","concept":"行业/概念","event_type":"政策催化/业绩财报/订单合作/产品技术/板块异动/宏观市场/风险事件/综合新闻","sentiment":"positive/negative/neutral","impact_score":0-100,"score":0-10,"strategy":"LimitUp/Watch/Avoid","reason":"影响逻辑"}\n'
            "  ],\n"
            '  "remove_stocks": []\n'
            "}\n"
            "要求：只输出A股可交易股票；无法映射到A股股票时 stocks 返回空数组；不要编造不存在的6位代码。\n\n"
            + "\n\n".join(news_lines)
        )

    def _record_from_ai(self, batch: List[Dict[str, Any]], parsed: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
        stocks = []
        for item in parsed.get("stocks", []) if isinstance(parsed.get("stocks"), list) else []:
            if not isinstance(item, dict):
                continue
            code = quant_engine.universe.normalize_code(item.get("code"), item.get("name"))
            if not quant_engine.universe.is_tradeable_a_share(code):
                continue
            impact_score = max(0.0, min(100.0, safe_float(item.get("impact_score"), safe_float(item.get("score"), 0) * 10)))
            ai_score = safe_float(item.get("score"), impact_score / 10)
            stocks.append(
                {
                    "code": code,
                    "name": quant_engine.universe.name(code, item.get("name")),
                    "concept": str(item.get("concept") or item.get("industry") or "").strip()[:40],
                    "event_type": str(item.get("event_type") or "综合新闻").strip()[:30],
                    "sentiment": str(item.get("sentiment") or "neutral").strip()[:20],
                    "impact_score": round(impact_score, 2),
                    "score": round(max(0.0, min(10.0, ai_score)), 2),
                    "strategy": str(item.get("strategy") or "Watch").strip()[:24],
                    "reason": str(item.get("reason") or "").strip()[:240],
                }
            )
        cfg = self.config()
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        return self._base_record(
            batch=batch,
            result={"stocks": stocks, "remove_stocks": [], "summary": str(parsed.get("summary") or "").strip()[:300]},
            analysis_source="ai",
            model=raw.get("model") or cfg["model"],
            usage=usage,
        )

    def _fallback_record(self, batch: List[Dict[str, Any]], error: str = "") -> Dict[str, Any]:
        stocks = []
        for item in batch:
            text = str(item.get("text") or "")
            mentions = quant_engine.universe.extract_mentions(text, limit=5)
            for code, name in mentions:
                event_type = self.classifier.classify_event_type(text)
                industry = self.classifier.classify_industry(text)
                sentiment = self.classifier.sentiment(text)
                impact = self.classifier.impact(text, event_type, sentiment)
                stocks.append(
                    {
                        "code": code,
                        "name": name,
                        "concept": industry,
                        "event_type": event_type,
                        "sentiment": "positive" if sentiment > 0.08 else ("negative" if sentiment < -0.08 else "neutral"),
                        "impact_score": round(impact, 2),
                        "score": round(max(0.0, min(10.0, impact / 10)), 2),
                        "strategy": "Watch",
                        "reason": text[:220],
                    }
                )
        return self._base_record(
            batch=batch,
            result={"stocks": stocks, "remove_stocks": [], "summary": "规则降级分析"},
            analysis_source="fallback",
            model="rule-fallback",
            usage={},
            error=error,
        )

    def _base_record(
        self,
        batch: List[Dict[str, Any]],
        result: Dict[str, Any],
        analysis_source: str,
        model: str,
        usage: Dict[str, Any],
        error: str = "",
    ) -> Dict[str, Any]:
        now_text = datetime.now().isoformat(timespec="seconds")
        news_ids = [_news_id(item) for item in batch]
        key = short_hash("|".join(news_ids) + f"|{analysis_source}|{model}")
        stocks = result.get("stocks") if isinstance(result.get("stocks"), list) else []
        return {
            "record_key": key,
            "mode": "auto_ai",
            "analyzed_at": now_text,
            "last_seen_at": now_text,
            "from_cache": False,
            "hit_count": 1,
            "news_ids": news_ids,
            "news_items": batch,
            "market_summary": "",
            "result_summary": f"{analysis_source}关注{len(stocks)}只",
            "result": result,
            "provider": "deepseek" if analysis_source.startswith("ai") else "rule",
            "model": model,
            "usage": usage,
            "analysis_source": analysis_source,
            "error": error,
        }

    def _append_record(self, record: Dict[str, Any]) -> int:
        with self._lock:
            records = self.records()
            existing = {str(item.get("record_key") or "") for item in records if isinstance(item, dict)}
            if str(record.get("record_key") or "") in existing:
                return 0
            records.insert(0, record)
            write_json(self.records_file, records[:50000])
            return 1

    def _write_state(self, payload: Dict[str, Any]) -> None:
        write_json(self.state_file, payload)

    def status(self) -> Dict[str, Any]:
        records = self.records()
        analyzed = self.analyzed_news_ids()
        state = read_json(self.state_file, {})
        latest = ""
        for record in records:
            if isinstance(record, dict):
                latest = str(record.get("analyzed_at") or "")
                if latest:
                    break
        cfg = self.config()
        return {
            "status": "ok",
            "enabled": cfg["enabled"],
            "model": cfg["model"],
            "records": len(records),
            "analyzed_news": len(analyzed),
            "latest_analyzed_at": latest,
            "last_run": state if isinstance(state, dict) else {},
        }


ai_analyzer = AINewsAnalyzer()
