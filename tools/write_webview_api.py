"""Extract WebviewApi from main.py into app/webview_api.py (UTF-8)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "main.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, ln in enumerate(lines) if ln.startswith("class WebviewApi("))
end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("# ==================== 入口"))
chunk = "".join(lines[start:end])
chunk = chunk.replace(
    "return Path(__file__).resolve().parent\n",
    "return Path(__file__).resolve().parent.parent\n",
)
hdr = '''"""xm-bot4 Pywebview JS API（继承 PywebviewShell）。"""
from __future__ import annotations

import sys
from pathlib import Path

from xm_py_server.shell import PywebviewShell


'''
(ROOT / "app" / "webview_api.py").write_text(hdr + chunk, encoding="utf-8")
print("wrote webview_api.py", len(hdr + chunk))
