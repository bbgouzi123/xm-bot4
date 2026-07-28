from pathlib import Path

root = Path(__file__).resolve().parent.parent
lines = (root / "main.py").read_text(encoding="utf-8").splitlines(True)
body = "".join(lines[358:927])
body = body.replace("_BOT4_LOCAL_ORIGIN", "constants.BOT4_LOCAL_ORIGIN")
body = body.replace("    global moment_scheduler\n", "")
body = body.replace("moment_scheduler = MomentScheduler", "app_state.moment_scheduler = MomentScheduler")
body = body.replace("if moment_scheduler:", "if app_state.moment_scheduler:")
body = body.replace("await moment_scheduler.stop()", "await app_state.moment_scheduler.stop()")

header = '''"""Application lifespan (startup/shutdown)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.ai.factory import AIServiceFactory
from src.api import add_friend_api, chat, config_api, friend_api, moment_api, system, task_api
from src.utils.websocket_manager import ws_manager

from app import constants
import app.state as app_state
from app.state import account_manager, ai_service, driver, monitor


@asynccontextmanager
async def lifespan(app: FastAPI):
'''

(root / "app").mkdir(exist_ok=True)
(root / "app" / "lifespan.py").write_text(header + body, encoding="utf-8")
print("OK", (root / "app" / "lifespan.py").stat().st_size)
