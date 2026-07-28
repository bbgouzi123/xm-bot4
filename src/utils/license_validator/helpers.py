import json
import logging
from typing import Optional, Dict, Any
from .storage import CONFIG_DIR

logger = logging.getLogger(__name__)

def get_sso_user_id() -> Optional[str]:
    """从 SSO 共享文件获取当前登录用户的 ID"""
    try:
        from src.sso_bridge import read_sso_session
        session = read_sso_session()
        if session and session.get("user", {}).get("id"):
            return str(session["user"]["id"])
    except Exception as e:
        logger.debug(f"读取 SSO session 失败: {e}")
    return None

def get_current_wechat_id() -> Optional[str]:
    """获取当前接管的微信号 ID (wxid)"""
    try:
        bot_config = CONFIG_DIR / "bot_config.json"
        if bot_config.exists():
            data = json.loads(bot_config.read_text(encoding='utf-8'))
            wxid = data.get("wechat_id") or data.get("wxid") or data.get("current_wxid")
            if wxid: 
                return str(wxid)
        from src.sso_bridge import read_sso_session
        session = read_sso_session()
        if session and session.get("wechat_id"): 
            return str(session["wechat_id"])
    except Exception as e:
        logger.debug(f"读取当前微信 ID 失败: {e}")
    return None


def parse_subscription_server_response(
    data: dict, 
    user_id: str, 
    cached_license: dict, 
    current_wechat_id: str
) -> Dict[str, Any]:
    """解析服务端响应的通用工具函数，从 subscription.py 剥离"""
    from datetime import datetime, timezone
    
    plan_code = data.get("plan_code", "trial")
    status = data.get("status", "trial")
    expires_at = data.get("expires_at", "")
    plan_name = data.get("plan_name", "试用版")
    is_valid = status in ("active", "trial")
    
    days_remaining = 0
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            days_remaining = max(0, (exp_dt - datetime.now(timezone.utc)).days)
        except: pass
    
    if status == "trial":
        trial_ends = data.get("trial_ends_at", "")
        if trial_ends:
            try:
                trial_dt = datetime.fromisoformat(trial_ends.replace('Z', '+00:00'))
                now_utc = datetime.now(timezone.utc)
                if now_utc > trial_dt:
                    is_valid = False
                    status = "trial_expired"
                else:
                    days_remaining = max(0, (trial_dt - now_utc).days)
            except: pass
    
    trial_bonus_sent = cached_license.get("trial_bonus_sent", False)
    base_bonus_sent = cached_license.get("base_bonus_sent", False)
    professional_bonus_sent = cached_license.get("professional_bonus_sent", False)
    flagship_bonus_sent = cached_license.get("flagship_bonus_sent", False)

    try:
        from src.utils.db_manager import WeChatDBManager
        db = WeChatDBManager()
        trial_bonus_sent = trial_bonus_sent or getattr(db, 'trial_bonus_sent', False)
        base_bonus_sent = base_bonus_sent or getattr(db, 'base_bonus_sent', False)
        professional_bonus_sent = professional_bonus_sent or getattr(db, 'professional_bonus_sent', False)
        flagship_bonus_sent = flagship_bonus_sent or getattr(db, 'flagship_bonus_sent', False)
    except Exception:
        db = None

    if is_valid and current_wechat_id:
        try:
            from .bonus import trigger_bonus_notification
            trial_bonus_sent, base_bonus_sent, professional_bonus_sent, flagship_bonus_sent = trigger_bonus_notification(
                plan_code=plan_code,
                is_valid=is_valid,
                db=db,
                trial_bonus_sent=trial_bonus_sent,
                base_bonus_sent=base_bonus_sent,
                professional_bonus_sent=professional_bonus_sent,
                flagship_bonus_sent=flagship_bonus_sent
            )
        except Exception as e:
            logger.warning(f"[订阅] 触发福利赠送通知异常: {e}")

    return {
        "valid": is_valid, "status": status, "mode": "subscription", "user_id": user_id,
        "plan_code": plan_code, "plan_name": plan_name, "expires_at": expires_at, "days_remaining": days_remaining,
        "trial_ends_at": data.get("trial_ends_at", ""), "trial_starts_at": data.get("trial_starts_at") or data.get("started_at", ""),
        "created_at": data.get("created_at") or data.get("started_at", ""),
        "message": f"订阅正常 ({plan_name})" if is_valid else f"订阅已到期 ({plan_name})",
        "features": data.get("features", {}), "wechat_ids": data.get("wechat_ids", []),
        "max_wechat": data.get("max_wechat", 1), "max_unbinds": data.get("max_unbinds", 0),
        "used_unbinds": data.get("used_unbinds", 0), "max_industries": -1 if plan_code == 'flagship' else data.get("max_industries", 1),
        "ai_daily_limit": data.get("ai_daily_limit", 30),
        "gift_seats_count": data.get("gift_seats_count", 0),
        "trial_bonus_sent": trial_bonus_sent,
        "base_bonus_sent": base_bonus_sent,
        "professional_bonus_sent": professional_bonus_sent,
        "flagship_bonus_sent": flagship_bonus_sent,
    }
