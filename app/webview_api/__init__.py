"""xm-bot4 Pywebview JS API（继承 PywebviewShell）。"""
from __future__ import annotations

import os
import sys

from xm_py_server.shell import PywebviewShell
from app.webview_api.snake import SnakeMixin
from app.webview_api.updater import UpdaterMixin
from app.webview_api.utils import UtilsMixin
from app.webview_api.coze import CozeMixin
from app.webview_api.record import RecordMixin
from app.webview_api.remote import RemoteMixin
from app.webview_api.env_installer import EnvInstallerMixin

class WebviewApi(
    PywebviewShell,
    SnakeMixin,
    UpdaterMixin,
    UtilsMixin,
    CozeMixin,
    RecordMixin,
    RemoteMixin,
    EnvInstallerMixin
):
    """
    xm-bot4 产品专属的 Pywebview JS API。
    """
    def __init__(self):
        super().__init__()
        # 蛇越狱幽灵窗口管理器（延迟初始化）
        self._snake_ghost = None
        # 🐍 桐面悬浮蛇独立小窗口（越狱后的桌面独立悬浮小窗）
        self._snake_float_mgr = None
        self._downloading_version = None  # 正在下载的版本，用于防止重复拉起下载线程

    def close_app(self):
        """重写基类退出方法：先执行业务清理，再强制退出"""
        self._user_requested_close = True  # WebView2 崩溃检测标志
        try:
            from src.utils.cleanup import graceful_cleanup
            graceful_cleanup()
        except Exception:
            pass
        super().close_app()

    def setup_tray(self, window, product_name: str = "星码行空", icon_path: str = ""):
        """重写基类托盘方法：劫持『彻底退出』菜单项的回调，注入清理逻辑"""
        from app.webview_api.tray import setup_tray_impl
        setup_tray_impl(self, window, product_name, icon_path)

    def log_js_error(self, message: str, filename: str, lineno: int, colno: int, stack: str):
        """记录前端 JS 全局未捕获的错误。"""
        # 过滤浏览器自带的 ResizeObserver 循环布局良性警告，防止其污染终端日志和 crash.log
        if message and "ResizeObserver loop" in message:
            return
        try:
            import datetime
            appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
            log_path = os.path.join(appdata, "xm-bot4", "logs", "crash.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"\n{'='*60}\n"
                    f"[{datetime.datetime.now().isoformat()}] [前端JS报错]\n"
                    f"Message: {message}\n"
                    f"File: {filename}:{lineno}:{colno}\n"
                    f"Stack: {stack}\n"
                    f"{'='*60}\n"
                )
            print(f"[JS Error] {message} at {filename}:{lineno}")
        except Exception:
            pass

    def minimize(self):
        """兼容旧前端代码的 minimize 调用（基类方法名为 minimize_window）"""
        self.minimize_window()

    def maximize_window(self):
        """兼容旧前端代码"""
        if self._window:
            self._safe_invoke(self._window.maximize)
            self._is_max_state = True

    def restore_window(self):
        """兼容旧前端代码"""
        if self._window:
            self._safe_invoke(self._window.restore)
            self._is_max_state = False

    def open_hud_window(self, url: str):
        """打开 HUD 自动化控制中心子窗口（透明、无边框、置顶）。"""
        from app.webview_api.hud import open_hud_window_impl
        open_hud_window_impl(self, url)

    def sync_theme(self, mode: str, resolved_theme: str, brand_color: str | None = None):
        """同步主题到所有其他的 pywebview 窗口。"""
        import webview
        import json
        for win in webview.windows:
            if win != self._window:
                try:
                    script = f"window.__xm_theme_sync__?.({json.dumps(mode)}, {json.dumps(resolved_theme)}, {json.dumps(brand_color)})"
                    win.evaluate_js(script)
                except Exception as e:
                    pass
