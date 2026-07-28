"""Win10 运行环境自检主入口。

启动顺序：
  1. 重定向日志（打包模式）
  2. 依次自检 WebView2、.NET 4.7.2+、VC++ 2015-2022
  3. 缺失时：询问用户 → 自动下载 → 静默安装 → 验证 → 失败则引导手动
"""
from __future__ import annotations

import os
import sys
import time


# ── 日志重定向（打包模式） ─────────────────────────────────────────────────────

class _Tee:
    def __init__(self, file, stream):
        self.file = file
        self.stream = stream

    def write(self, data):
        try:
            if self.file:
                self.file.write(data)
                self.file.flush()
        except Exception:
            pass
        try:
            if self.stream:
                self.stream.write(data)
                self.stream.flush()
        except Exception:
            pass

    def flush(self):
        try:
            if self.file:
                self.file.flush()
        except Exception:
            pass
        try:
            if self.stream:
                self.stream.flush()
        except Exception:
            pass


def _setup_logging_to_file():
    if not getattr(sys, "frozen", False):
        return
    try:
        app_data = os.environ.get("APPDATA")
        if not app_data:
            return
        log_dir = os.path.join(app_data, "xm-bot4", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "latest.log")
        f = open(log_file, "w", encoding="utf-8", buffering=1)
        sys.stdout = _Tee(f, sys.stdout)
        sys.stderr = _Tee(f, sys.stderr)
        print(f"--- xm-bot4 Log Start: {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        print(f"[日志] 写入文件: {log_file}")
    except Exception as e:
        print(f"Failed to setup logging: {e}", file=sys.__stderr__)


# ── 公共入口 ───────────────────────────────────────────────────────────────────

def check_runtime_environment() -> bool:
    """
    检查并自动修复关键运行环境（WebView2、.NET 4.7.2+、VC++ 2015-2022）。
    所有组件就绪则返回 True，若有组件修复失败则返回 False。
    """
    _setup_logging_to_file()

    if sys.platform != "win32":
        return True

    print("[环境自检] 开始检查运行环境...")

    from app.bootstrap.env_consts import (
        WEBVIEW2_DOWNLOAD_URL, WEBVIEW2_FALLBACK_URL,
        DOTNET_DOWNLOAD_URL, DOTNET_FALLBACK_URL,
        VCREDIST_DOWNLOAD_URL, VCREDIST_FALLBACK_URL,
        detect_webview2, detect_dotnet_472, detect_vcredist,
    )
    from app.bootstrap.env_gui import run_unified_installer

    components = [
        {
            "key": "webview2",
            "name": "Microsoft Edge WebView2 运行时",
            "size_hint": "约 2 MB (自动拉取约 120 MB)",
            "download_url": WEBVIEW2_DOWNLOAD_URL,
            "fallback_url": WEBVIEW2_FALLBACK_URL,
            "filename": "MicrosoftEdgeWebview2Setup.exe",
            "silent_args": ["/install"],
            "recheck_fn": detect_webview2,
        },
        {
            "key": "dotnet",
            "name": ".NET Framework 4.8",
            "size_hint": "约 120 MB",
            "download_url": DOTNET_DOWNLOAD_URL,
            "fallback_url": DOTNET_FALLBACK_URL,
            "filename": "ndp48-x86-x64-allos-enu.exe",
            "silent_args": ["/q", "/norestart"],
            "recheck_fn": detect_dotnet_472,
        },
        {
            "key": "vcredist",
            "name": "Visual C++ 2015-2022 运行时 (x64)",
            "size_hint": "约 25 MB",
            "download_url": VCREDIST_DOWNLOAD_URL,
            "fallback_url": VCREDIST_FALLBACK_URL,
            "filename": "vc_redist.x64.exe",
            "silent_args": ["/quiet", "/norestart"],
            "recheck_fn": detect_vcredist,
        }
    ]

    # 依次过滤出本机目前缺失的依赖组件
    missing = [c for c in components if not c["recheck_fn"]()]
    if not missing:
        print("[环境自检] ✅ 所有运行环境就绪，继续启动...")
        return True

    # 启动图形化统一配置界面
    return run_unified_installer(components, missing)

