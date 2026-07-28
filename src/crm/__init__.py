"""
CRM 客户关系管理模块 — 360° 全景客户画像系统

核心组件：
- TagManager: 标签三级分类管理（大类→中类→小类）
- ProfileManager: 客户画像管理（CRUD + 标签合并 + 持久化）
- ProfileExtractor: 从 AI 回复中提取画像标签
- IndustryConfig: 多行业配置切换管理
- PromptBuilder: 元提示词动态生成
"""
from .tag_manager import TagManager, TAG_CATEGORIES
from .profile_manager import ProfileManager
from .profile_extractor import extract_profile_from_reply
from .industry_config import IndustryConfigManager, SYSTEM_TEMPLATES
from .prompt_builder import PromptBuilder

__all__ = [
    "TagManager",
    "TAG_CATEGORIES",
    "ProfileManager",
    "extract_profile_from_reply",
    "IndustryConfigManager",
    "SYSTEM_TEMPLATES",
    "PromptBuilder",
]

