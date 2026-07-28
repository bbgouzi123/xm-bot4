"""
微信 UI 参数（移植自 xm-bot4 core/WxParam.py — 40行部分反编译）

原始文件: core/WxParam.py (PARTIAL(1), 40 lines)
包含不同分辨率下微信控件的高度参数，用于消息类型判断。
"""
import ctypes
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class WxParam:
    """微信 UI 参数（分辨率自适应）

    用于根据控件高度判断消息类型（系统消息、时间标签、聊天文本、图片等）
    """

    # 1080P 分辨率参数
    HEIGHT_1080P = {
        'SYS_TEXT_HEIGHT': 33,
        'TIME_TEXT_HEIGHT': 34,
        'RECALL_TEXT_HEIGHT': 45,
        'CHAT_TEXT_HEIGHT': 53,
        'CHAT_IMG_HEIGHT': 195,
    }

    # 2K 分辨率参数
    HEIGHT_2K = {
        'SYS_TEXT_HEIGHT': 50,
        'TIME_TEXT_HEIGHT': 51,
        'RECALL_TEXT_HEIGHT': 64,
        'CHAT_TEXT_HEIGHT': 80,
        'CHAT_IMG_HEIGHT': 168,
    }

    # 当前使用的参数（默认 1080P）
    SYS_TEXT_HEIGHT = HEIGHT_1080P['SYS_TEXT_HEIGHT']
    TIME_TEXT_HEIGHT = HEIGHT_1080P['TIME_TEXT_HEIGHT']
    RECALL_TEXT_HEIGHT = HEIGHT_1080P['RECALL_TEXT_HEIGHT']
    CHAT_TEXT_HEIGHT = HEIGHT_1080P['CHAT_TEXT_HEIGHT']
    CHAT_IMG_HEIGHT = HEIGHT_1080P['CHAT_IMG_HEIGHT']

    # 特殊消息类型标记
    SpecialTypes = [
        '[文件]',
        '[图片]',
        '[视频]',
        '[音乐]',
        '[链接]',
    ]

    @classmethod
    def init_resolution(cls):
        """根据屏幕分辨率初始化参数
        （从反编译的 lambda 表达式重建为正常类方法）
        """
        try:
            user32 = ctypes.windll.user32
            screen_height = user32.GetSystemMetrics(1)

            if screen_height > 1080:
                height_config = cls.HEIGHT_2K
                resolution_type = '2K'
            else:
                height_config = cls.HEIGHT_1080P
                resolution_type = '1080P'

            cls.SYS_TEXT_HEIGHT = height_config['SYS_TEXT_HEIGHT']
            cls.TIME_TEXT_HEIGHT = height_config['TIME_TEXT_HEIGHT']
            cls.RECALL_TEXT_HEIGHT = height_config['RECALL_TEXT_HEIGHT']
            cls.CHAT_TEXT_HEIGHT = height_config['CHAT_TEXT_HEIGHT']
            cls.CHAT_IMG_HEIGHT = height_config['CHAT_IMG_HEIGHT']

            logger.info(f'分辨率初始化完成: {resolution_type}, 屏幕高度={screen_height}px')
        except Exception as e:
            logger.warning(f'分辨率初始化失败，使用默认 1080P: {e}')
