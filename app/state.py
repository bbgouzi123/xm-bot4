"""Process-wide singletons (formerly main.py module globals)."""

from __future__ import annotations

import os
from typing import Any

from src.ai.openai_compat import OpenAICompatService
from src.monitor.chat_monitor import ChatMonitor
from src.monitor.multi_account_manager import MultiAccountManager
from src.uia.driver import WeChatDriver

driver = WeChatDriver()
ai_service = OpenAICompatService()
monitor = ChatMonitor(driver, ai_service)
account_manager = MultiAccountManager(ai_service)

# 朋友圈排期调度器（在 lifespan 中初始化）
moment_scheduler: Any = None

# 朋友圈智能互动管理器（在 lifespan 中初始化）
moment_interaction_manager: Any = None

# 用于重名消歧的全局活跃名字与 wxid 映射字典
name_to_active_wxid: dict[str, str] = {}

# 自动聊天运行状态
_bot_automation_running = False

# 【安全规范第 17 条】禁止硬编码密钥，从环境变量读取
API_KEY = os.getenv("XM_BOT4_API_KEY", "xm_bot_key")
