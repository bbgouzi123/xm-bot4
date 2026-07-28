"""
系统行业模板数据

行业模板数据完全由同步后端 PostgreSQL industry_templates 表提供。
本地仅保留兜底数据供离线使用。
新增/修改行业请操作同步后端 DB（seed_data.py UPSERT）。
"""
import os
import json

CHAT_EQ_DEFAULTS = {
    "intensity": 2,
    "min_interval": 3,
    "max_interval": 10,
    "weight": 5
}

SYSTEM_TEMPLATES: list = []

try:
    _dir = os.path.dirname(os.path.abspath(__file__))
    _json_path = os.path.join(_dir, "system_templates.json")
    if os.path.exists(_json_path):
        with open(_json_path, "r", encoding="utf-8") as _f:
            SYSTEM_TEMPLATES = json.load(_f)
except Exception as e:
    import logging
    logging.getLogger(__name__).error(f"[CRM] 加载本地 system_templates.json 行业模板失败: {e}")
