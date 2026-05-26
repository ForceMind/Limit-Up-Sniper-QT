from __future__ import annotations

from typing import Any, Callable, Dict


NewsFeed = Callable[..., Dict[str, Any]]
AppendLog = Callable[[str, str, str, str, Dict[str, Any]], None]


class FrontendNewsReadService:
    def __init__(
        self,
        *,
        lightweight_news_feed: NewsFeed,
        fallback_news_feed: NewsFeed,
        append_log: AppendLog,
    ) -> None:
        self._lightweight_news_feed = lightweight_news_feed
        self._fallback_news_feed = fallback_news_feed
        self._append_log = append_log

    def safe_news_feed(self, **kwargs: Any) -> Dict[str, Any]:
        try:
            lightweight = self._lightweight_news_feed(**kwargs)
            if isinstance(lightweight, dict):
                return lightweight
        except Exception as exc:
            self._append_log(
                "warning",
                f"\u8f7b\u91cf\u65b0\u95fb\u5feb\u7167\u8bfb\u53d6\u5931\u8d25\uff0c\u56de\u9000\u5b8c\u6574\u5f15\u64ce\uff1a{exc}",
                "frontend_snapshot",
                "news_light",
                {},
            )
        try:
            return self._fallback_news_feed(**kwargs)
        except Exception as exc:
            self._append_log(
                "error",
                f"\u65b0\u95fb\u5feb\u7167\u8bfb\u53d6\u5931\u8d25\uff1a{exc}",
                "frontend_snapshot",
                "news",
                {},
            )
            return {
                "status": "error",
                "items": [],
                "events": [],
                "count": 0,
                "error": "news feed unavailable",
            }

    def frontend_light_news_feed(self, **kwargs: Any) -> Dict[str, Any]:
        try:
            lightweight = self._lightweight_news_feed(**kwargs)
            if isinstance(lightweight, dict):
                return lightweight
        except Exception as exc:
            self._append_log(
                "warning",
                f"\u524d\u53f0\u8f7b\u91cf\u65b0\u95fb\u5feb\u7167\u8bfb\u53d6\u5931\u8d25\uff0c\u8fd4\u56de\u7a7a\u5feb\u7167\uff1a{exc}",
                "frontend_snapshot",
                "news_light",
                {},
            )
        return {
            "status": "pending",
            "items": [],
            "events": [],
            "count": 0,
            "message": "lightweight news unavailable",
        }


def market_sentiment(news_payload: Dict[str, Any]) -> Dict[str, Any]:
    events = news_payload.get("events") if isinstance(news_payload.get("events"), list) else []
    scores = []
    for item in events:
        if not isinstance(item, dict):
            continue
        try:
            scores.append(float(item.get("sentiment") or 0))
        except Exception:
            continue
    avg = sum(scores) / len(scores) if scores else 0.0
    positive = sum(1 for value in scores if value > 0)
    negative = sum(1 for value in scores if value < 0)
    if avg >= 0.12:
        label = "\u504f\u6696"
    elif avg <= -0.12:
        label = "\u504f\u51b7"
    else:
        label = "\u4e2d\u6027"
    return {
        "label": label,
        "score": round(avg, 4),
        "positive_count": positive,
        "negative_count": negative,
        "sample_count": len(scores),
    }
