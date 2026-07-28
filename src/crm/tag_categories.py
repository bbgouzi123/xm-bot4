from typing import Dict

# ============================================================
# 标签三级分类定义（6大类 → 28中类）
# 小类由 AI 自由生成，这里只定义框架
# ============================================================

TAG_CATEGORIES: Dict[str, Dict] = {
    # -------- 1. 人口属性 --------
    "demographics": {
        "name": "人口属性",
        "icon": "👤",
        "subcategories": {
            "gender": "性别",
            "age": "年龄段",
            "marriage": "婚姻",
            "family": "家庭",
            "education": "教育",
            "origin": "民族/籍贯",
        }
    },

    # -------- 2. 地理位置 --------
    "location": {
        "name": "地理位置",
        "icon": "📍",
        "subcategories": {
            "province": "省份",
            "city": "城市",
            "city_tier": "城市等级",
            "hometown": "家乡",
            "workplace": "工作地",
            "frequent": "常去地",
        }
    },

    # -------- 3. 职业经济 --------
    "career": {
        "name": "职业经济",
        "icon": "💼",
        "subcategories": {
            "industry": "行业",
            "position": "职位",
            "company_size": "公司规模",
            "financial": "经济能力",
            "spending": "消费偏好",
            "assets": "资产线索",
        }
    },

    # -------- 4. 兴趣生活 --------
    "interest": {
        "name": "兴趣生活",
        "icon": "❤️",
        "subcategories": {
            "sports": "运动健身",
            "food": "美食",
            "travel": "旅行",
            "culture": "文艺",
            "digital": "数码",
            "gaming": "游戏",
            "pets": "宠物",
            "vehicle": "汽车",
            "parenting": "育儿",
            "health": "养生",
        }
    },

    # -------- 5. 社交关系 --------
    "social": {
        "name": "社交关系",
        "icon": "🤝",
        "subcategories": {
            "relationship": "关系类型",
            "source": "来源渠道",
            "influence": "影响力",
            "style": "社交风格",
        }
    },

    # -------- 6. 商业价值 --------
    "business": {
        "name": "商业价值",
        "icon": "💰",
        "subcategories": {
            "intent": "购买意向",
            "role": "决策角色",
            "stage": "跟进阶段",
            "need": "需求类型",
            "cycle": "成交周期",
        }
    },

    # -------- 7. 导入数据 (Excel 等外部来源的原始业务数据) --------
    "import_data": {
        "name": "导入数据",
        "icon": "📥",
        "subcategories": {}  # 子类由导入时的 Excel 表头动态生成，不预定义
    },
}
