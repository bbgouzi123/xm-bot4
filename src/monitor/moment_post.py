"""
朋友圈发帖任务 — 从 xm-bot4 MomentPostAdapter 逆向移植

功能:
    pass
- 发送纯文字朋友圈
- 发送图片朋友圈（支持多图）
- 定时发布任务
- 素材分组管理
"""
import time
import random
import re
from pathlib import Path
from typing import Optional, List, Callable

import uiautomation as uia
import pyperclip
import win32gui
import win32api

from ..uia.elements import WxClass, WxName
from ..uia.retry import (
    try_click, exists_with_timeout, random_delay, smooth_click_at,
    wait_for_element, click_at_absolute, is_shift_pressed,
)


class MomentPost:
    """朋友圈发帖操作（对标 xm-bot4 MomentPostAdapter）"""

    def __init__(self, driver):
        """
        参数:
            driver: WeChatDriver 实例
        """
        self.driver = driver

    def publish_text(self, text: str, callback: Optional[Callable] = None) -> dict:
        """发布纯文字朋友圈"""
        if callback:
            callback("publishing", {"text": text[:50]})
        
        success = self.driver.post_moment(text=text)
        
        if callback:
            callback("published", {"text": text[:50]})
            
        return {"success": success, "error": "" if success else "发送失败"}

    def publish_with_images(
        self, text: str, image_paths: List[str],
        callback: Optional[Callable] = None
    ) -> dict:
        """发布图片朋友圈（带文字+多图）"""
        if callback:
            callback("publishing", {"text": text[:50], "images": len(image_paths)})
            
        success = self.driver.post_moment(text=text, image_paths=image_paths)
        
        if callback:
            callback("published", {"text": text[:50]})
            
        return {"success": success, "error": "" if success else "发送失败"}
