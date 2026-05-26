from __future__ import annotations

from app.quant.engine_utils import clamp


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
