import json
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".xm-ai-bot"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "add_friend_logs.json"
FRIEND_LIST_FILE = DATA_DIR / "friend_list.json"

def load_friend_list() -> List[Dict[str, Any]]:
    """从 JSON 文件加载好友名单"""
    try:
        if FRIEND_LIST_FILE.exists():
            data = json.loads(FRIEND_LIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []

def save_friend_list(friends: List[Dict[str, Any]]):
    """保存好友名单到 JSON 文件"""
    try:
        FRIEND_LIST_FILE.write_text(
            json.dumps(friends, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"保存好友名单失败: {e}")

def get_pending_friends(limit: int = 3) -> List[Dict[str, Any]]:
    """获取待添加的好友"""
    friends = load_friend_list()
    pending = [f for f in friends if f.get("status") == "pending"]
    return pending[:limit]

def update_friend_status(wxid: str, status: str, nickname: str = "", error: str = ""):
    """更新好友状态"""
    try:
        friends = load_friend_list()
        for f in friends:
            if f.get("wxid") == wxid:
                f["status"] = status
                f["nickname"] = nickname or f.get("nickname", "")
                f["error"] = error
                f["updated_at"] = datetime.now().isoformat()
                break
        save_friend_list(friends)
    except Exception as e:
        logger.error(f"更新好友状态失败: {e}")

def save_log(entry: Dict[str, Any]):
    """追加一条添加日志"""
    try:
        logs = []
        if LOG_FILE.exists():
            try:
                logs = json.loads(LOG_FILE.read_text(encoding="utf-8"))
            except Exception:
                logs = []
        if not isinstance(logs, list):
            logs = []
        logs.append(entry)
        if len(logs) > 500:
            logs = logs[-500:]
        LOG_FILE.write_text(
            json.dumps(logs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"保存添加日志失败: {e}")

def import_friends_store(friends: List[Dict]) -> Dict[str, Any]:
    try:
        existing = load_friend_list()
        existing_wxids = {f["wxid"] for f in existing}
        imported = 0
        for f in friends:
            wxid = f.get("wxid", "").strip()
            if not wxid or wxid in existing_wxids:
                continue
            existing.append({
                "wxid": wxid,
                "remark": f.get("remark", ""),
                "tags": f.get("tags", ""),
                "status": "pending",
                "nickname": "",
                "error": "",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            })
            existing_wxids.add(wxid)
            imported += 1
        save_friend_list(existing)
        return {"success": True, "imported": imported}
    except Exception as e:
        logger.error(f"导入好友名单失败: {e}")
        return {"success": False, "imported": 0, "error": str(e)}

def get_friend_list_store(status: str = None, limit: int = 100) -> List[Dict]:
    try:
        friends = load_friend_list()
        if status:
            friends = [f for f in friends if f.get("status") == status]
        return friends[:limit]
    except Exception:
        return []

def delete_friend_store(wxid: str) -> bool:
    try:
        friends = load_friend_list()
        friends = [f for f in friends if f.get("wxid") != wxid]
        save_friend_list(friends)
        return True
    except Exception:
        return False

def get_add_logs_store(limit: int = 50) -> List[Dict]:
    try:
        if LOG_FILE.exists():
            logs = json.loads(LOG_FILE.read_text(encoding="utf-8"))
            if isinstance(logs, list):
                logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                return logs[:limit]
    except Exception:
        pass
    return []

def is_rest_time_store(task_name: str) -> bool:
    try:
        from src.api.config_api import _load_configs
        config = _load_configs()
        rest = config.get("rest_time_settings", {})
        if not rest:
            return False
        selected_tasks = rest.get("selectedTasks", [])
        if task_name not in selected_tasks:
            return False
        start_time = rest.get("startTime", 0)
        end_time = rest.get("endTime", 28)
        now = datetime.now()
        current_interval = now.hour * 4 + now.minute // 15
        if start_time <= end_time:
            return start_time <= current_interval <= end_time
        else:
            return current_interval >= start_time or current_interval <= end_time
    except Exception as e:
        logger.error(f"检查休息时间失败: {e}")
        return False

def random_interval(min_sec: float, max_sec: float) -> float:
    return random.uniform(min_sec, max_sec)
