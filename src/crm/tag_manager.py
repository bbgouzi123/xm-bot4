"""
标签三级分类管理器 — 6大类 → 28中类 → ∞小类（AI 自由生成）

设计原则：
- 大类和中类是预定义的分类框架（引导 AI 从这些维度观察）
- 小类完全由 AI 动态生成，不做任何限制
- 一个用户可以有 N 个标签 × M 个维度
"""
from typing import Dict, List, Optional
from datetime import datetime


from .tag_categories import TAG_CATEGORIES


class TagEntry:
    """单个标签条目"""

    def __init__(
        self,
        category: str,         # 大类 key (如 "demographics")
        subcategory: str,      # 中类 key (如 "gender")
        value: str,            # 小类值 (如 "男"，AI 自由生成)
        confidence: float = 0.5,  # 置信度 0-1
        source: str = "chat",  # 来源: chat / friend_add / moments / manual
        updated: str = "",
    ):
        self.category = category
        self.subcategory = subcategory
        self.value = value
        self.confidence = min(1.0, max(0.0, confidence))
        self.source = source
        self.updated = updated or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "subcategory": self.subcategory,
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TagEntry":
        return cls(
            category=d.get("category", ""),
            subcategory=d.get("subcategory", ""),
            value=d.get("value", ""),
            confidence=d.get("confidence", 0.5),
            source=d.get("source", "chat"),
            updated=d.get("updated", ""),
        )

    def __repr__(self):
        return f"Tag({self.category}/{self.subcategory}={self.value}, conf={self.confidence:.1f})"


class TagManager:
    """标签三级分类管理器

    职责：
    - 验证标签是否在已知维度中（未知维度也接受，放到 "other" 大类）
    - 合并新标签和旧标签（同维度覆盖规则）
    - 提供按维度查询标签的能力
    """

    # 购买意向的优先级（只升不降）
    INTENT_PRIORITY = {
        "已成交": 6,
        "复购客户": 7,
        "意向-强烈": 5,
        "意向-中等": 4,
        "意向-观望": 3,
        "意向-拒绝": 2,  # 拒绝也允许后续提升
        "首次接触": 1,
    }

    @staticmethod
    def get_categories() -> Dict:
        """获取完整的标签分类树"""
        return TAG_CATEGORIES

    @staticmethod
    def get_category_name(category_key: str) -> str:
        """获取大类中文名"""
        cat = TAG_CATEGORIES.get(category_key, {})
        return cat.get("name", category_key)

    @staticmethod
    def get_subcategory_name(category_key: str, sub_key: str) -> str:
        """获取中类中文名"""
        cat = TAG_CATEGORIES.get(category_key, {})
        subs = cat.get("subcategories", {})
        return subs.get(sub_key, sub_key)

    @staticmethod
    def find_category_for_key(key: str) -> tuple:
        """根据中类 key 查找所属大类

        Returns:
            (category_key, subcategory_key) 或 (None, None)
        """
        for cat_key, cat_info in TAG_CATEGORIES.items():
            subs = cat_info.get("subcategories", {})
            if key in subs:
                return cat_key, key
        return None, None

    @staticmethod
    def normalize_ai_tags(raw_tags: dict) -> List[TagEntry]:
        """将 AI 输出的自由格式标签规范化为 TagEntry 列表

        AI 输出示例:
        {
            "intent": "意向-强烈",
            "industry": "陶瓷制造业",
            "province": "广东",
            "vehicle": "保时捷卡宴",
            "personality": "说话直爽"  # 未知维度也接受
        }

        Returns:
            List[TagEntry]
        """
        entries = []
        for key, value in raw_tags.items():
            if not value or not isinstance(value, str):
                continue

            value = value.strip()
            if not value:
                continue

            # 尝试匹配已知维度
            cat_key, sub_key = TagManager.find_category_for_key(key)

            if cat_key:
                # 匹配到已知维度
                entries.append(TagEntry(
                    category=cat_key,
                    subcategory=sub_key,
                    value=value,
                    confidence=0.7,
                    source="chat",
                ))
            else:
                # 未知维度 → 放到最接近的大类，或 "other"
                best_cat = TagManager._guess_category(key, value)
                entries.append(TagEntry(
                    category=best_cat,
                    subcategory=key,  # 保留原始 key 作为中类
                    value=value,
                    confidence=0.5,
                    source="chat",
                ))

        return entries

    @staticmethod
    def _guess_category(key: str, value: str) -> str:
        """猜测未知维度属于哪个大类"""
        # 关键词匹配
        key_lower = key.lower()
        hints = {
            "demographics": ["性别", "年龄", "婚姻", "家庭", "教育", "gender", "age"],
            "location": ["省", "市", "城市", "地址", "位置", "location", "city", "address"],
            "career": ["行业", "职业", "公司", "经济", "消费", "收入", "industry", "job"],
            "interest": ["爱好", "兴趣", "运动", "美食", "旅行", "宠物", "车", "hobby"],
            "social": ["关系", "来源", "社交", "朋友", "social", "relation"],
            "business": ["意向", "购买", "需求", "成交", "intent", "buy", "need"],
        }

        for cat_key, keywords in hints.items():
            for kw in keywords:
                if kw in key_lower or kw in value:
                    return cat_key

        return "interest"  # 默认归到兴趣生活

    @staticmethod
    def merge_tags(
        existing: List[TagEntry],
        new_tags: List[TagEntry],
    ) -> List[TagEntry]:
        """合并新旧标签

        规则：
        1. 同维度（category + subcategory）新标签覆盖旧标签
        2. 但购买意向只升不降（A→B 不允许，B→A 允许）
        3. 新标签置信度更高时才覆盖（除非旧标签很旧）
        4. 不同维度的标签直接追加
        """
        # 建立索引: (category, subcategory) → TagEntry
        tag_map = {}
        for t in existing:
            key = (t.category, t.subcategory)
            tag_map[key] = t

        for new_t in new_tags:
            key = (new_t.category, new_t.subcategory)
            old_t = tag_map.get(key)

            if old_t is None:
                # 新维度，直接加入
                tag_map[key] = new_t
            else:
                # 同维度，看是否需要覆盖
                should_update = False

                # 购买意向只升不降
                if new_t.subcategory == "intent":
                    old_pri = TagManager._get_intent_priority(old_t.value)
                    new_pri = TagManager._get_intent_priority(new_t.value)
                    if new_pri > old_pri:
                        should_update = True
                elif new_t.confidence >= old_t.confidence:
                    # 其他维度：置信度更高就覆盖
                    should_update = True
                elif new_t.value != old_t.value:
                    # 值不同，且新的置信度也不低（>0.5），也覆盖
                    if new_t.confidence >= 0.5:
                        should_update = True

                if should_update:
                    new_t.updated = datetime.now().isoformat()
                    tag_map[key] = new_t

        return list(tag_map.values())

    @staticmethod
    def _get_intent_priority(value: str) -> int:
        """获取购买意向的优先级"""
        for k, v in TagManager.INTENT_PRIORITY.items():
            if k in value:
                return v
        return 0

    @staticmethod
    def get_top_tags(tags: List[TagEntry], n: int = 5) -> List[TagEntry]:
        """获取最重要的 N 个标签（用于同步到微信）

        优先级：商业价值 > 职业经济 > 地理位置 > 其他
        """
        priority_map = {
            "business": 6,
            "career": 5,
            "location": 4,
            "demographics": 3,
            "social": 2,
            "interest": 1,
        }

        sorted_tags = sorted(
            tags,
            key=lambda t: (
                priority_map.get(t.category, 0),
                t.confidence,
            ),
            reverse=True,
        )
        return sorted_tags[:n]

    @staticmethod
    def tags_to_wx_labels(tags: List[TagEntry]) -> List[str]:
        """将 TagEntry 列表转为微信标签字符串列表并做精简与无效值过滤"""
        labels = []
        invalid_words = {
            "business", "casual_chat", "negative", "greeting", 
            "unknown", "未知", "无", "0-100%", "none", "null"
        }
        for t in tags:
            label = (t.value or "").strip()
            if not label:
                continue
            # 去掉重复前缀
            if "-" in label:
                parts = label.split("-", 1)
                label = parts[-1].strip()
            if label.lower() in invalid_words:
                continue
            if label.endswith("%") and label[:-1].isdigit():
                continue
            if label:
                labels.append(label[:15])  # 微信标签最长15字
        return labels
