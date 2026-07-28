"""
多行业配置管理器 — 支持创建/切换/编辑多个行业配置

拆分自原 industry_config.py（同名收束）。
对外 API 路径不变：from src.crm.industry_config import IndustryProfile, ...
"""
from .profile import (
    IndustryProfile,
    CHAT_EQ_DEFAULTS,
    merge_chat_eq,
)
from .templates import SYSTEM_TEMPLATES
from .manager import IndustryConfigManager

__all__ = [
    "IndustryProfile",
    "CHAT_EQ_DEFAULTS",
    "merge_chat_eq",
    "SYSTEM_TEMPLATES",
    "IndustryConfigManager",
]
