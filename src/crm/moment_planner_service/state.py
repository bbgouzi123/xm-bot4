import logging
import json
import threading
from datetime import datetime
from typing import List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# ===== Shared Memory State =====
_schedules: List[dict] = []  # [{id, scheduled_time, content_text, media_urls, status, industry_tag, ...}]
_schedule_lock = threading.RLock()
_next_id = 1
_executed_ids: set = set()

def get_local_schedule_dir() -> Path:
    from src.crm.account_data import get_account_data_dir
    return Path(get_account_data_dir())

def get_local_schedule_file() -> Path:
    return get_local_schedule_dir() / "moment_schedules_snapshot.json"

def get_local_plan_groups_file() -> Path:
    return get_local_schedule_dir() / "moment_plan_groups_snapshot.json"

MOMENT_SCHEDULE_LATENESS_SEC = 10800  # 3 小时，需 > 养号巡检间隔（1.5~2.5h）否则排期会被误标 failed

def _json_plain(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))

def _coerce_schedule_id(raw) -> int:
    if raw is None:
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.lstrip("-").isdigit():
            return int(s)
    return 0

def _schedule_time_str(s: dict) -> str:
    st = s.get("scheduled_time")
    if st is None:
        return ""
    return str(st).strip()

def _parse_schedule_datetime(st: str) -> Optional[datetime]:
    if not st:
        return None
    s = str(st).strip()
    if not s:
        return None
    try:
        if len(s) >= 19 and s[10] == " ":
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        if "T" in s:
            iso = s
            if iso.endswith("Z"):
                iso = iso[:-1] + "+00:00"
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is not None:
                return dt.astimezone().replace(tzinfo=None)
            return dt
        if len(s) >= 19:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, IndexError):
        pass
    return None

def _normalize_schedule_item(s: dict) -> dict:
    item = dict(s)
    if not item.get("bot_wxid"):
        try:
            from src.crm.account_data import get_active_account
            item["bot_wxid"] = get_active_account() or "default"
        except Exception:
            item["bot_wxid"] = "default"
    st = item.get("scheduled_time")
    if st:
        dt = _parse_schedule_datetime(st)
        if dt:
            item["scheduled_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
    media = item.get("media_urls", [])
    if isinstance(media, str):
        try:
            media = json.loads(media)
        except Exception:
            if media.startswith('[') or media.startswith('{'):
                media = []
            else:
                media = [media] if media else []
    if not isinstance(media, list):
        media = []
    item["media_urls"] = media
    return item

def _schedule_sort_ts(entry: dict) -> datetime:
    parsed = _parse_schedule_datetime(_schedule_time_str(entry))
    return parsed if parsed is not None else datetime.max


def sanitize_newlines_in_json(s: str) -> str:
    """将 JSON 字符串值内部的裸换行符（\\r\\n / \\n）替换为空格，保留结构层的换行不变。

    AI（如 Coze）有时在 JSON 字段值中输出原始换行（尤其是 URL 和长文本），
    这会导致 json.loads 抛 Unterminated string 错误。此函数通过状态机扫描，
    只替换字符串值内部的换行，不影响 JSON 结构级别的空白。
    """
    result = []
    in_string = False
    escape_next = False
    for ch in s:
        if escape_next:
            escape_next = False
            result.append(ch)
            continue
        if ch == '\\' and in_string:
            escape_next = True
            result.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch in ('\n', '\r'):
            result.append(' ')
            continue
        result.append(ch)
    return ''.join(result)
