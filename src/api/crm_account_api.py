"""
CRM API — 统计分析 + 多账号管理子模块 (crm_account_api.py)
从 crm_api.py 中拆分以遵守 300 行代码质量规范。
"""
from fastapi import APIRouter, Request
from src.utils.response import ok, ok_msg, err
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


def clear_chat_context_for_account(account_id: str):
    """切换行业后异步清空指定账号的所有聊天上下文，防止旧行业历史污染新行业 AI 回复。
    
    使用 mark_industry_switched() 同时完成内存清空 + 60秒保护期标记，
    确保清空后不会被云端懒加载机制重新拉回旧数据。
    """
    import threading
    def _do_clear():
        try:
            from src.utils.chat_history import ChatHistoryManager
            mgr = ChatHistoryManager(account_id)
            mgr.mark_industry_switched()  # 清空内存 + 设置 60s 云端回填保护期
        except Exception as e:
            logger.warning(f"[行业切换] 清空聊天上下文异常（非严重）: {e}")
    threading.Thread(target=_do_clear, daemon=True, name="clear-chat-ctx").start()



# ==================== 统计分析 ====================

@router.get("/api/crm/stats")
async def crm_stats():
    from src.crm.profile_manager import ProfileManager
    pm = ProfileManager()
    stats = pm.get_profile_stats()

    from src.crm.industry_config import IndustryConfigManager
    icm = IndustryConfigManager(account_id="global")
    active = icm.get_active_profile()

    return ok({
        "stats": {
            "total_customers": stats.get("total_customers", 0),
            "intent_distribution": stats.get("intent_distribution", {}),
            "recent_active": stats.get("recent_active", 0),
            "active_industry": active.name if active else "未配置",
            "active_industry_icon": active.icon if active else "⚙️",
        },
    })


# ==================== 多账号 ====================

@router.get("/api/crm/accounts")
async def list_accounts():
    """获取所有已有数据的微信账号"""
    from src.crm.account_data import (
        list_accounts as _list, get_active_account, get_active_nickname,
        APP_DATA_DIR,
    )
    return ok({
        "accounts": _list(),
        "active": get_active_account(),
        "active_nickname": get_active_nickname(),
        "data_dir": APP_DATA_DIR,
    })


@router.get("/api/crm/account/settings")
async def get_account_settings_api(account_id: str = None):
    """获取当前连接账号的私有设置 (AI 和 自动化风控)"""
    from src.crm.account_data import get_account_settings
    return ok({"settings": get_account_settings(account_id)})


@router.post("/api/crm/account/settings")
async def save_account_settings_api(request: Request):
    """保存当前连接账号的私有设置"""
    data = await request.json()
    from src.crm.account_data import save_account_settings

    settings = data
    wxid = None
    if isinstance(data, dict):
        if "settings" in data:
            settings = data["settings"]
        if "account_id" in data:
            wxid = data["account_id"]

    save_account_settings(settings, wxid=wxid)
    return ok_msg("保存成功")


@router.post("/api/crm/accounts/copy-config")
async def copy_account_config(request: Request):
    """从源账号复制行业配置到目标账号"""
    data = await request.json()
    source = data.get("source", "")
    target = data.get("target", "")  # 空表示当前活跃账号
    if not source:
        return err(40000, "缺少 source")

    from src.crm.account_data import copy_config_from
    success = copy_config_from(source, target or None)

    if success:
        return ok_msg("复制成功")
    return err(50000, "复制失败")
