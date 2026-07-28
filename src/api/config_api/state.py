from fastapi import APIRouter
from pathlib import Path

router = APIRouter()

_driver = None
_ai_service = None
_comment_thread = None
_comment_running = False
_comment_result = None
_friend_request_monitor = None
_mass_sending_core = None
_post_thread = None
_post_running = False
_post_result = None
_moment_interaction_manager = None

CONFIG_DIR = Path.home() / ".xm-ai-bot"
