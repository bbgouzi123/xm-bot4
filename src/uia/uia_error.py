"""
UIA 异常定义（移植自 xm-bot4 core/uia_error.py — 75行部分反编译）

原始文件: core/uia_error.py (PARTIAL(5), 75 lines)
定义了微信 UIA 操作相关的异常类和错误处理函数。
"""
import logging
from typing import Optional, Tuple, Any

logger = logging.getLogger('uia_error')


class WeChatUIAError(Exception):
    """微信 UIA 基础异常（移植自 xm-bot4）"""

    def __init__(self, message: str = '', original_error: Exception = None):
        self.message = message
        self.original_error = original_error
        super().__init__(message)

    def __str__(self):
        if self.original_error:
            return f'{self.message} (原始错误: {self.original_error})'
        return self.message


class WeChatUIAConnectionError(WeChatUIAError):
    """微信断开连接异常"""

    def __init__(self, message: str = '微信连接已断开', original_error: Exception = None):
        super().__init__(message, original_error)


class WeChatWindowError(WeChatUIAError):
    """微信窗口异常"""

    def __init__(self, message: str = '微信窗口异常', original_error: Exception = None):
        super().__init__(message, original_error)


def check_wechat_disconnected(wechat_instance) -> Tuple[bool, str]:
    """检查微信是否断开连接

    Args:
        wechat_instance: WeChat 实例

    Returns:
        tuple[bool, str]: (是否断开连接, 断开原因)
    """
    try:
        status = wechat_instance.check_connection_status()
        if not status.get('connected', False):
            return (True, status.get('reason', '未知原因'))
        return (False, '')
    except Exception as e:
        return (True, str(e))


def uia_error(e: Exception) -> Optional[WeChatUIAError]:
    """处理 UIA 异常，识别特定类型的错误并转换为对应的异常类

    Args:
        e: 原始异常

    Returns:
        Optional[WeChatUIAError]: 如果是已知的 UIA 异常则返回对应的异常对象，否则返回 None
    """
    error_str = str(e)

    # 微信进程断开
    if '事件无法调用任何订户' in error_str:
        logger.error(f'检测到微信断开连接: {error_str}')
        return WeChatUIAConnectionError(original_error=e)

    # 窗口句柄无效
    if '无效的窗口句柄' in error_str or 'Invalid window handle' in error_str:
        return WeChatWindowError(original_error=e)

    # 已是 WeChatUIAError 子类
    if isinstance(e, WeChatUIAError):
        return e

    # 通用 UIA 异常
    return WeChatUIAError(message=f'UIA异常: {str(e)}', original_error=e)
