import asyncio
import logging
import time

logger = logging.getLogger(__name__)

class BarrierMixin:
    """UIA 初始化与账号元数据屏障"""

    async def _wait_for_initialization(self) -> bool:
        """原子初始化屏障：确保微信连接、UIA 树热身完成，且身份元数据（wxid/nickname）成功加载。"""
        if not self.driver.is_connected():
            return False

        # 确保防刷屏日志记录字典存在
        if not hasattr(self, "_last_barrier_log_time"):
            self._last_barrier_log_time = {}

        def log_with_throttle(key: str, message: str, level: int = logging.WARNING, interval: float = 60.0):
            now = time.time()
            if now - self._last_barrier_log_time.get(key, 0.0) >= interval:
                self._last_barrier_log_time[key] = now
                logger.log(level, message)
            else:
                logger.debug(message)

        # 1. 检查身份元数据是否已就绪
        nickname = getattr(self.driver, "_nickname", "")
        wxid = getattr(self.driver, "_wxid", "")
        if not nickname or not wxid:
            log_with_throttle("meta_not_ready", "[初始化屏障] 微信已连接，但身份元数据 (nickname/wxid) 未就绪，尝试从缓存恢复...", logging.INFO)
            # 尝试从缓存恢复
            if not self.driver._try_restore_from_cache():
                # 尝试调用 UIA 提取（物理操作），这通常需要确保在前台且有 uia_lock
                log_with_throttle("extract_info", "[初始化屏障] 本地快照为空，尝试调用 UIA 提取微信账号信息...", logging.INFO)
                try:
                    # 必须在一个 executor 中跑，因为 _extract_user_info 是同步物理输入 Block 过程
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: self.driver._extract_user_info(skip_avatar_if_exists=True)
                    )
                except Exception as e:
                    logger.error(f"[初始化屏障] 物理提取微信账号信息异常: {e}")
            
            # 再次检查
            nickname = getattr(self.driver, "_nickname", "")
            wxid = getattr(self.driver, "_wxid", "")
            if not nickname or not wxid:
                log_with_throttle("meta_incomplete", "[初始化屏障] 身份元数据未完全加载，阻止扫描循环启动")
                return False

        # 2. 检查微信 UIA 树是否热身成功且稳定 (YokoBot 的 OBJID_CLIENT pattern)
        if not getattr(self, "_uia_preheated", False):
            hwnd = getattr(self.driver, 'hwnd', 0)
            if hwnd:
                import ctypes
                WM_GETOBJECT = 0x003D
                OBJID_CLIENT = -4
                UIA_ROOT_OBJECT_ID = -25
                try:
                    # 强制触发无障碍树刷新
                    ctypes.windll.user32.SendMessageW(hwnd, WM_GETOBJECT, 0, UIA_ROOT_OBJECT_ID)
                    ctypes.windll.user32.SendMessageW(hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)
                    log_with_throttle("preheat_signal", f"[初始化屏障] 成功向微信窗口 hwnd={hwnd} 发送 OBJID_CLIENT 激活信号进行无障碍树热身", logging.INFO)
                except Exception as e:
                    logger.debug(f"[初始化屏障] 发送 UIA 热身消息异常: {e}")

                # 校验无障碍树是否渲染就绪
                try:
                    root = self.driver.root
                    if not root:
                        log_with_throttle("root_empty", "[初始化屏障] UIA 根节点为空，等待热身...")
                        return False
                    
                    # 检测基本导航栏是否存在
                    nav_toolbar = root.ToolBarControl(Name="导航")
                    if not nav_toolbar.Exists(0.2):
                        nav_toolbar = root.ToolBarControl(AutomationId="main_tabbar")
                    if not nav_toolbar.Exists(0.2):
                        log_with_throttle("nav_missing", "[初始化屏障] UIA 导航栏尚未载入，无障碍树不稳定，等待热身...")
                        return False
                    
                    self._uia_preheated = True
                    logger.info("[初始化屏障] 微信主窗口 UIA 无障碍树热身并稳定成功")
                except Exception as e:
                    log_with_throttle("verify_exception", f"[初始化屏障] 校验 UIA 稳定性时发生异常: {e}")
                    return False

        return True
