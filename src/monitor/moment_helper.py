import logging
import threading
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def get_moment_status(manager) -> Dict[str, Any]:
    from src.utils.daily_counter import DailyCounter
    from src.utils.moment_config import get_moment_settings
    import app.state as app_state
    
    status_str = "paused" if manager._paused else ("running" if manager._running else "stopped")
    accounts_detail = []
    try:
        dc = DailyCounter()
        account_manager = getattr(app_state, 'account_manager', None)
        if account_manager:
            for inst in account_manager._instances.values():
                if inst.wxid:
                    acc_id = inst.wxid
                    acc_settings = get_moment_settings(acc_id)
                    
                    err_count = manager.account_errors.get(acc_id, 0)
                    enabled = acc_settings.get("enabled", True)
                    likes = dc.get_count("moment_like", acc_id)
                    comments = dc.get_count("moment_comment", acc_id)
                    likes_limit = acc_settings.get("daily_like_limit", 30)
                    comments_limit = acc_settings.get("daily_comment_limit", 10)
                    
                    has_quota = (likes < likes_limit) or (comments < comments_limit)
                    
                    if not enabled:
                        acc_status = "disabled"
                    elif err_count >= 5:
                        acc_status = "fused"
                    elif not has_quota:
                        acc_status = "limit_reached"
                    elif manager._running and getattr(manager, "current_patrol_wxid", "") == acc_id and not manager._paused:
                        acc_status = "running"
                    else:
                        acc_status = "waiting"
                        
                    accounts_detail.append({
                        "wxid": acc_id,
                        "nickname": inst.nickname or acc_id,
                        "likes": likes,
                        "comments": comments,
                        "likes_limit": likes_limit,
                        "comments_limit": comments_limit,
                        "enabled": enabled,
                        "status": acc_status,
                        "err_count": err_count
                    })
    except Exception as ex:
        logger.debug(f"get_moment_status 获取巡游账号明细异常: {ex}")
        
    return {
        "status": status_str,
        "interactions_count": len(manager._interactions_log),
        "pending_tags": len(manager._pending_tags),
        "current_patrol_wxid": getattr(manager, "current_patrol_wxid", ""),
        "current_patrol_nickname": getattr(manager, "current_patrol_nickname", ""),
        "accounts_detail": accounts_detail,
    }

def persist_moment_log(manager, action_type: str, author_name: str,
                       content: str = "", reply_text: str = "",
                       fingerprint: str = "", author_wxid: str = ""):
    import time as _t
    log_entry = {
        "time": _t.strftime("%Y-%m-%d %H:%M:%S"),
        "type": action_type,
        "author": author_name,
        "content": content[:50],
        "reply": reply_text,
    }
    manager._interactions_log.append(log_entry)
    if len(manager._interactions_log) > 200:
        manager._interactions_log = manager._interactions_log[-200:]

    def _cloud_push():
        try:
            from src.utils.cloud_sync import get_cloud_client
            cloud = get_cloud_client()
            event_data = {
                "author_wxid": author_wxid,
                "target_name": author_name,
                "content_snippet": content[:200],
                "action_type": action_type,
                "reply_text": reply_text,
                "fingerprint": fingerprint,
                "status": "success"
            }
            cloud.sync_moment_interactions([{
                "author_wxid": author_wxid,
                "author_name": author_name,
                "content_snippet": content[:200],
                "action_type": action_type,
                "reply_text": reply_text,
                "fingerprint": fingerprint,
            }])
            cloud.report_event(action_type, event_data)
        except Exception as e:
            logger.debug(f"[互动日志·同步后端] 推送失败: {e}")
            
    threading.Thread(target=_cloud_push, daemon=True, name="cloud-interaction").start()
