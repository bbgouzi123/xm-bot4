import os
import time
import random
import re
import ctypes
import hashlib
import json
import logging
import threading
from typing import Optional, List, Dict
from pathlib import Path

import win32gui
import win32con
import uiautomation as uia
import pyperclip

from src.uia.elements import WxClass, WxName, RESOLUTION_PARAMS
from src.uia.session import parse_session_name
from src.uia.message import parse_message

logger = logging.getLogger("WeChatDriver")


class WeChatContactsMixin:
    def get_contacts(self) -> List[dict]:
        """获取联系人列表（从本地缓存读取）"""
        try:
            if self._contacts_file.exists():
                data = json.loads(
                    self._contacts_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    # ==================== 朋友圈 ====================


