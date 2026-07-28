"""Build app/bootstrap.py from app/_bootstrap_chunk.txt (strip __main__ block, fix imports)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
raw = (ROOT / "app" / "_bootstrap_chunk.txt").read_text(encoding="utf-8")
# drop if __name__ block
idx = raw.find('if __name__ == "__main__":')
assert idx != -1
body = raw[:idx].rstrip() + "\n"

replacements = [
    ("kill_port(_BOT4_PORT)", "kill_port(constants.BOT4_PORT)"),
    ("port=_BOT4_PORT", "port=constants.BOT4_PORT"),
    ("uvicorn.run(app,", "uvicorn.run(_get_served_app(),"),
    ("_BOT4_LOCAL_ORIGIN", "constants.BOT4_LOCAL_ORIGIN"),
    ("_VITE_DEV_ORIGIN", "constants.VITE_DEV_ORIGIN"),
    ("_BOT4_VITE_PORT", "constants.BOT4_VITE_PORT"),
    ("_ANTI_DEBUG_BYPASS_TK", "constants.ANTI_DEBUG_BYPASS_TK"),
    ("_XM_PACKAGED_WITH_F12", "constants.XM_PACKAGED_WITH_F12"),
    ("_xm_bot4_splash_app_version()", "xm_bot4_splash_app_version()"),
    ("os.path.dirname(__file__)", "str(BACKEND_ROOT)"),
]
for a, b in replacements:
    body = body.replace(a, b)

hdr = '''"""进程入口：端口清理、uvicorn、pywebview 桌面壳。"""
from __future__ import annotations

import os
import sys
import threading

import uvicorn
from xm_py_server.runtime_urls import LOOPBACK_HOST

from app import constants
from app.paths import BACKEND_ROOT, xm_bot4_splash_app_version
from app.runtime_preamble import _ensure_clr_loader_dll_path
from app.webview_api import WebviewApi

_served_app = None


def register_app(app) -> None:
    """由 main 在 create_app() 之后注册，供 start_server / uvicorn 使用同一实例。"""
    global _served_app
    _served_app = app


def _get_served_app():
    if _served_app is None:
        raise RuntimeError("register_app() must be called before start_server()")
    return _served_app


def flush_cloud_before_exit(max_batches: int = 20) -> None:
    """退出前抢救性上报关键用量与事件队列（带批次上限，避免阻塞过久）"""
    try:
        from src.crm.account_data import get_active_account
        from src.utils.cloud_sync import get_cloud_client

        cloud = get_cloud_client()
        account_id = get_active_account() or "main"
        cloud.report_usage(account_id)
        flushed = cloud.flush_pending_events(max_batches=max_batches)
        print(f"[关闭] 同步后端事件抢救上报完成，补传 {flushed} 条")
    except Exception as e:
        print(f"[关闭] 同步后端抢救上报失败: {e}")


'''
(ROOT / "app" / "bootstrap.py").write_text(hdr + body, encoding="utf-8")
print("wrote bootstrap.py")
