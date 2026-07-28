"""One-off: build app/proxy.py from app/_proxy_chunk.txt."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
chunk = (ROOT / "app" / "_proxy_chunk.txt").read_text(encoding="utf-8")
lines = chunk.splitlines(True)
out: list[str] = []
skip = False
for ln in lines:
    if ln.startswith("_CROSS_SERVICE_MAP"):
        skip = True
        continue
    if skip:
        if "_CROSS_SERVICE_PREFIXES" in ln and "list" in ln:
            skip = False
        continue
    out.append(ln)
c2 = "".join(out)
c2 = c2.replace("_CROSS_SERVICE_MAP", "constants.CROSS_SERVICE_MAP")
c2 = c2.replace("_CROSS_SERVICE_PREFIXES", "constants.CROSS_SERVICE_PREFIXES")
c2 = c2.replace("@app.", "@router.")
hdr = '''"""Cross-service reverse proxy (HTTP + WebSocket)."""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import httpx
import socket
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from xm_py_server.proxy_forward import PROXY_FORWARD_HEADERS, PROXY_SKIP_RESPONSE_HEADERS
from xm_py_server.runtime_urls import LOOPBACK_HOST

from app import constants

router = APIRouter()

'''
(ROOT / "app" / "proxy.py").write_text(hdr + c2 + "\n\ncross_service_router = router\n", encoding="utf-8")
print("wrote app/proxy.py")
