"""
AI 服务模块 — 支持 DeepSeek / Coze / Dify 多平台
"""
from .base import AIServiceBase
from .openai_compat import OpenAICompatService
from .coze_service import CozeService
from .dify_service import DifyService
from .factory import AIServiceFactory

__all__ = [
    "AIServiceBase",
    "OpenAICompatService",
    "CozeService",
    "DifyService",
    "AIServiceFactory",
]
