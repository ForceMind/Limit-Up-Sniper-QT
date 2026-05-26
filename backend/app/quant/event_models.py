from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict


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
