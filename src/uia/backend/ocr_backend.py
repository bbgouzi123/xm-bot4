"""
OCR Backend — 截图 + OCR + Win32 驱动
=====================================
为微信 ≥4.1.8 提供不依赖辅助功能 API 的自动化能力。
核心原理：PrintWindow 截图 → PaddleOCR 文字识别 → Win32 坐标点击

状态：待实现（阶段 1~4）
"""
from .base import DriverBackend
from typing import Dict, List, Tuple


class OCRBackend(DriverBackend):
    """新版微信 OCR 后端"""

    def __init__(self):
        self._ocr_engine = None  # 懒加载

    def _get_ocr(self):
        """懒加载 PaddleOCR 引擎"""
        if self._ocr_engine is None:
            try:
                from paddleocr import PaddleOCR
                self._ocr_engine = PaddleOCR(
                    use_angle_cls=True,
                    lang='ch',
                    show_log=False,
                )
            except ImportError:
                raise RuntimeError(
                    "OCR Backend 需要安装 paddleocr: "
                    "pip install paddlepaddle paddleocr opencv-python"
                )
        return self._ocr_engine

    def _capture_window(self, hwnd: int):
        """PrintWindow 截图，返回 numpy 数组 (BGR)

        TODO 阶段 0: 实现 capture.py 后调用
        """
        # from .capture import capture_window
        # return capture_window(hwnd)
        raise NotImplementedError("阶段 0 待实现 capture.py")

    def _capture_region(self, hwnd: int, x: int, y: int, w: int, h: int):
        """截取窗口指定区域

        TODO 阶段 0: 实现
        """
        raise NotImplementedError("阶段 0 待实现")

    # ==================== 接口实现 ====================

    def find_nav_toolbar(self, hwnd: int) -> Tuple[object, object]:
        """OCR 模式不需要真正"找到"导航栏控件，
        而是通过截图确认导航栏的像素区域。

        TODO 阶段 1: 截图左侧 60px → OCR 识别「微信」「通讯录」等文字，
        确认导航栏位置和尺寸。
        """
        raise NotImplementedError("阶段 1 待实现")

    def extract_user_info(self, hwnd: int, skip_avatar: bool = False) -> Dict:
        """截图 → 点击头像 → 截图资料卡 → OCR 提取昵称和微信号

        TODO 阶段 1:
        1. 截图导航栏顶部区域（头像位置）
        2. 物理点击头像坐标
        3. 等待资料卡弹出
        4. 截图资料卡 → OCR 提取昵称、微信号
        5. （可选）截取资料卡头像区域保存
        """
        raise NotImplementedError("阶段 1 待实现")

    def scan_sessions(self, hwnd: int, max_count: int = 50) -> List[Dict]:
        """截图会话列表 → OCR 提取每条会话信息

        TODO 阶段 2:
        1. 截图会话列表区域（x=60~310px 范围）
        2. OCR 全区域识别
        3. 按 Y 坐标分组，每组 = 一个会话
        4. 从每组中提取：名称、最后消息、时间、未读数
        """
        raise NotImplementedError("阶段 2 待实现")

    def read_messages(self, hwnd: int, count: int = 20) -> List[Dict]:
        """截图聊天区域 → OCR 提取消息内容

        TODO 阶段 3:
        1. 截图聊天区域（x=310px ~ 右边界）
        2. OCR 识别所有文字
        3. 按气泡位置判断发送方（左=对方，右=自己）
        4. 按 Y 坐标排序，还原消息时间线
        """
        raise NotImplementedError("阶段 3 待实现")

    def click_session(self, hwnd: int, session_name: str) -> bool:
        """OCR 定位会话 → 物理点击

        TODO 阶段 2:
        1. scan_sessions() 获取所有会话及坐标
        2. 匹配 session_name
        3. 物理点击该会话的中心坐标
        """
        raise NotImplementedError("阶段 2 待实现")

    def send_text(self, hwnd: int, text: str) -> bool:
        """发送文本消息 — 纯 Win32，不需要 OCR

        TODO 阶段 4:
        1. 截图确认输入框位置（一次性标定）
        2. 点击输入框
        3. SetClipboardData 写入文本
        4. Ctrl+V 粘贴
        5. Enter 发送
        """
        raise NotImplementedError("阶段 4 待实现")

    def is_available(self) -> bool:
        """检查 OCR 依赖是否已安装"""
        try:
            import paddleocr
            import cv2
            import numpy
            return True
        except ImportError:
            return False
