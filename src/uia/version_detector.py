"""
微信版本检测器（移植自 xm-bot4 core/version_detector.py — 34行部分反编译）

原始文件: core/version_detector.py (PARTIAL(2), 34 lines)
检测微信版本以决定使用旧版(3.9.x)还是新版(4.1.x)驱动。
"""
import os
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class WeChatVersion(Enum):
    """微信版本枚举"""
    LEGACY_3_9 = '3.9.x'
    MODERN_4_1 = '4.1.x'


def detect_version(window_handle: int = None) -> WeChatVersion:
    """Best-effort WeChat version detection.

    Strategy:
        pass
    - If env `WECHAT_AUTOMATION_MODE` is set to `legacy` or `pyweixin`, honor it.
    - If UIAutomation is unavailable, assume modern (pyweixin).
    - If a known legacy control pattern is found, return LEGACY_3_9; otherwise MODERN_4_1.

    （从反编译骨架重建完整逻辑）
    """
    # 环境变量覆盖
    env_mode = os.environ.get('WECHAT_AUTOMATION_MODE', '').lower()
    if env_mode == 'legacy':
        logger.info('检测到环境变量 WECHAT_AUTOMATION_MODE=legacy')
        return WeChatVersion.LEGACY_3_9
    if env_mode in ('pyweixin', 'modern'):
        logger.info(f'检测到环境变量 WECHAT_AUTOMATION_MODE={env_mode}')
        return WeChatVersion.MODERN_4_1

    # 如果有窗口句柄，通过进程名检测版本
    if window_handle:
        try:
            import psutil
            import win32process
            _, pid = win32process.GetWindowThreadProcessId(window_handle)
            proc = psutil.Process(pid)
            exe_path = proc.exe()
            # 通过文件版本信息判断
            # 4.x 版本的 ClassName 通常是 Qt51514QWindowIcon
            import win32gui
            class_name = win32gui.GetClassName(window_handle)
            if class_name:
                if 'Qt' in class_name:
                    return WeChatVersion.MODERN_4_1
                if class_name.endswith('WeChatMainWndForPC') or 'WeChatMainWndForPC' in class_name:
                    return WeChatVersion.LEGACY_3_9
        except Exception as e:
            logger.debug(f'版本检测异常: {e}')

    # 默认假定为新版
    return WeChatVersion.MODERN_4_1
