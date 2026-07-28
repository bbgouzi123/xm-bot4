"""Generate app/routes_builtin.py from app/_routes_chunk.txt."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
chunk = (ROOT / "app" / "_routes_chunk.txt").read_text(encoding="utf-8")
chunk = chunk.replace("@app.", "@router.")
chunk = re.sub(r"^[ \t]*global moment_scheduler[ \t]*\n", "", chunk, flags=re.MULTILINE)
chunk = re.sub(r"\bmoment_scheduler\b", "app_state.moment_scheduler", chunk)
chunk = chunk.replace("logger.debug", "_log.debug")

ws = '''
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        _log.error(f"[WebSocket/500] 通道发生未预期的异常: {e}")
        ws_manager.disconnect(websocket)
'''

hdr = '''"""Previously inline FastAPI routes from main.py."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

import app.state as app_state
from app.state import account_manager, driver, monitor
from src.api import config_api
from src.utils.response import err, ok, ok_msg
from src.utils.websocket_manager import ws_manager

router = APIRouter()
_log = logging.getLogger(__name__)

'''
(ROOT / "app" / "routes_builtin.py").write_text(hdr + chunk + ws, encoding="utf-8")
print("wrote routes_builtin.py")
