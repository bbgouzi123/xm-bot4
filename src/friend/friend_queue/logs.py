import threading
from typing import List, Dict
from .storage import _lock, _logs, _queue, _now, _persist_after_mutation, logger
from . import storage

def add_log(
    queue_id: int,
    phone: str,
    company_name: str,
    action: str,
    status: str,
    message: str = "",
    industry_profile_id: str = "",
    industry_profile_name: str = "",
):
    """记录操作日志（内存 + 异步推同步后端）"""
    # 如果没传行业信息，从队列回查
    if not industry_profile_id and queue_id:
        with _lock:
            for q in _queue:
                if q.get("id") == queue_id:
                    industry_profile_id = q.get("industry_profile_id", "")
                    industry_profile_name = q.get("industry_profile_name", "")
                    break

    log_entry = {
        "id": storage._next_log_id,
        "queue_id": queue_id,
        "phone": phone,
        "company_name": company_name,
        "action": action,
        "status": status,
        "message": message,
        "industry_profile_id": industry_profile_id,
        "industry_profile_name": industry_profile_name,
        "timestamp": _now(),
    }

    with _lock:
        _logs.append(log_entry)
        storage._next_log_id += 1
        # 内存只保留最近 2000 条日志
        if len(_logs) > 2000:
            _logs[:] = _logs[-2000:]

    # 异步推同步后端（单条）
    def _push():
        try:
            from src.utils.cloud_sync import get_cloud_client
            get_cloud_client().sync_add_friend_logs([log_entry])
        except Exception as e:
            logger.debug(f"[加好友日志·同步后端] 推送失败: {e}")
    threading.Thread(target=_push, daemon=True, name="cloud-friend-log").start()
    _persist_after_mutation()


def get_logs(
    limit: int = 50,
    industry_profile_id: str = "",
    status_filter: str = "",
) -> List[Dict]:
    """获取最近日志"""
    with _lock:
        result = list(_logs)

    if industry_profile_id:
        result = [l for l in result if l.get("industry_profile_id") == industry_profile_id]
    if status_filter:
        result = [l for l in result if l.get("status") == status_filter]

    # 倒序 + 限制
    result.sort(key=lambda x: x.get("id", 0), reverse=True)
    return result[:limit]
