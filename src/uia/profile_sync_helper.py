import logging
from typing import Optional

logger = logging.getLogger(__name__)

def sync_contact_profile_if_needed(profile_win, session_name: str) -> None:
    """
    检查好友是否需要在 CRM 中完善或更新资料，并在需要时调用同步抓取工具。
    """
    if not session_name:
        return

    try:
        from app.state import account_manager
        
        bot_wxid = "main"
        driver_inst = None
        for h_id, inst in account_manager._instances.items():
            if inst.driver.is_connected():
                driver_inst = inst.driver
                bot_wxid = getattr(inst, "wxid", "main")
                break
                
        from src.crm.account_data import get_account_settings
        if not get_account_settings(bot_wxid).get("reply", {}).get("fetch_profile_enabled", True):
            return

        from src.crm.profile_manager import ProfileManager
        pm = ProfileManager(account_id=bot_wxid)
        has_wxid = False
        for p in pm.get_all_profiles():
            if (p.nickname == session_name or p.remark == session_name) and p.wxid and not p.wxid.startswith("nick_"):
                has_wxid = True
                break
                
        if not has_wxid:
            from src.uia.tag_sync.profile_extractor_moment import extract_and_sync_profile_from_moment
            logger.info(f"[名片同步] 检测到会话对象 '{session_name}' 在 CRM 中无真实微信号(wxid)，正在抓取并同步弹窗资料...")
            extract_and_sync_profile_from_moment(driver_inst, profile_win, session_name)
    except Exception as sync_err:
        logger.error(f"[名片同步] 提取并更新好友完整资料异常: {sync_err}")
