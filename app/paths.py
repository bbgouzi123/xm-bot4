"""backend-python 根路径与启动屏版本号（供 app 包内模块使用）。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def xm_bot4_splash_app_version() -> str:
    """pywebview 启动屏版本号，与 products/xm-bot4/frontend/package.json 的 version 字段对齐。"""
    paths: list[str] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "") or ""
        if meipass:
            paths.append(os.path.join(meipass, "assets", "package.json"))
        paths.append(os.path.join(str(BACKEND_ROOT), "assets", "package.json"))
    paths.append(os.path.normpath(BACKEND_ROOT.parent / "frontend" / "package.json"))
    for p in paths:
        try:
            if p and os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as fp:
                    raw = (json.load(fp).get("version") or "").strip()
                    if raw:
                        s = str(raw)
                        return s if s.lower().startswith("v") else f"v{s}"
        except Exception:
            continue
    return ""
