"""UIBus handlers registration and lifecycle management."""
from typing import Any
from src.orchestrator.ui_bus import ui_bus, UICommandKind
from app.state import driver

def _get_driver_for_cmd(cmd) -> Any:
    """Helper to dynamically resolve account-specific driver based on command's wxid in multi-account environment."""
    account_id = getattr(cmd, "wxid", None)
    import app.state as app_state
    if account_id and hasattr(app_state, "account_manager") and app_state.account_manager:
        for inst in app_state.account_manager._instances.values():
            if inst.wxid == account_id or (inst.driver and getattr(inst.driver, "bot_wxid", None) == account_id):
                return inst.driver
    return app_state.driver


def _handle_send_message(cmd):
    """UIBus SEND_MESSAGE handler — 调用 WeChatDriver 发送消息"""
    target = cmd.payload.get("target", "")
    text = cmd.payload.get("text", "")
    if not target or not text:
        return False

    # 敏感词与黑名单物理安全过滤
    from src.utils.safety_utils import check_message_safety
    if not check_message_safety(target, text):
        return False

    # 🌟 系统号及公众号物理执行过滤防御
    if target.startswith("gh_") or target in (
        "fmessage", "medianote", "floatbottle", "filehelper", "newsapp", 
        "helper_entry", "mphelper", "weibo", "qqmail", "tmessage", "blogapp"
    ):
        return True

    drv = _get_driver_for_cmd(cmd)
    wxid = cmd.payload.get("wxid", None)
    return drv.send_message(target, text, wxid=wxid)

def _handle_send_file(cmd):
    """UIBus SEND_FILE handler — 调用 WeChatDriver 发送文件/图片"""
    target = cmd.payload.get("target", "")
    file_path = cmd.payload.get("file_path", "")
    if not target or not file_path:
        return False
    drv = _get_driver_for_cmd(cmd)
    wxid = cmd.payload.get("wxid", None)
    return drv.SendFiles(target, file_path, wxid=wxid)

def _handle_send_voice(cmd):
    """UIBus SEND_VOICE handler — 支持收藏夹转发与实时克隆 TTS 内录两种发送形式"""
    target = cmd.payload.get("target", "")
    voice_mode = cmd.payload.get("voice_mode", "favorite")
    drv = _get_driver_for_cmd(cmd)
    wxid = cmd.payload.get("wxid", None)
    
    if voice_mode == "tts_clone":
        text = cmd.payload.get("text", "")
        voice_id = cmd.payload.get("voice_id", "S_xiaomei")
        if not target or not text:
            return False
        return drv.send_voice_by_tts_clone(target, text, voice_id, wxid=wxid)
    else:
        favorite_name = cmd.payload.get("favorite_name", "")
        if not target or not favorite_name:
            return False
        return drv.send_voice_by_favorite(target, favorite_name, wxid=wxid)

def _handle_sync_tags(cmd):
    """UIBus SYNC_TAGS handler — 标签同步"""
    drv = _get_driver_for_cmd(cmd)
    from src.uia.tag_sync import WeChatTagSync
    sync = WeChatTagSync(drv)
    friend = cmd.payload.get("target", "")
    tags = cmd.payload.get("tags", [])
    if not friend or not tags:
        return False
    return sync.apply_tags_from_chat(friend, tags)

def _handle_publish_moment(cmd):
    """UIBus PUBLISH_MOMENT handler — 发布朋友圈"""
    text = cmd.payload.get("text", "")
    image_paths = cmd.payload.get("image_paths", None)
    if not text and not image_paths:
        return False
    drv = _get_driver_for_cmd(cmd)
    return drv.post_moment(text, image_paths)

def _handle_moment_interact(cmd):
    """UIBus MOMENT_INTERACT handler — 朋友圈点赞/评论"""
    settings = cmd.payload.get("settings", {})
    account_id = cmd.payload.get("account_id", "")
    
    import app.state as app_state
    manager = getattr(app_state, 'moment_interaction_manager', None)
    if manager:
        # 兼容多账号多开模式，获取当前指令对应实例的 driver
        driver_inst = None
        try:
            if hasattr(app_state, 'account_manager') and app_state.account_manager:
                for inst in app_state.account_manager._instances.values():
                    if inst.wxid == account_id or (not account_id and not inst.wxid):
                        driver_inst = inst.driver
                        break
        except Exception:
            pass
            
        if not driver_inst:
            driver_inst = app_state.driver
            
        if driver_inst:
            manager.driver = driver_inst
            
        return manager._patrol_round_body(settings, account_id)
    return False

def _handle_add_friend(cmd):
    """UIBus ADD_FRIEND handler — 加好友"""
    drv = _get_driver_for_cmd(cmd)
    from src.friend.friend_manager import FriendManager
    mgr = FriendManager(driver=drv)
    target = cmd.payload.get("target", "")
    remark = cmd.payload.get("remark", "")
    tags = cmd.payload.get("tags", "")
    verify_message = cmd.payload.get("verify_message", "")
    if not target:
        return False
    return mgr.add_single_friend(
        wxid=target,
        remark=remark,
        tags=tags,
        verify_message=verify_message
    )

def _handle_accept_friend(cmd):
    """UIBus ACCEPT_FRIEND handler — 同意好友申请"""
    remark_template = cmd.payload.get("remark_template", "")
    wechat_tags = cmd.payload.get("wechat_tags", [])
    keyword_tag_rules = cmd.payload.get("keyword_tag_rules", [])
    permission_type = cmd.payload.get("permission_type", "all")
    hide_my_moments = cmd.payload.get("hide_my_moments", False)
    hide_his_moments = cmd.payload.get("hide_his_moments", False)
    account_id = cmd.wxid
    
    import app.state as app_state
    # 获取正确的 driver
    driver_inst = None
    try:
        if hasattr(app_state, 'account_manager') and app_state.account_manager:
            for inst in app_state.account_manager._instances.values():
                if inst.wxid == account_id:
                    driver_inst = inst.driver
                    break
    except Exception:
        pass
        
    if not driver_inst:
        driver_inst = app_state.driver
        
    if driver_inst:
        from src.uia.accept_friend import AcceptFriendEngine
        engine = AcceptFriendEngine(driver_inst)
        return engine.accept_all(
            remark_template=remark_template,
            tags=wechat_tags,
            keyword_tag_rules=keyword_tag_rules,
            permission_type=permission_type,
            hide_my_moments=hide_my_moments,
            hide_his_moments=hide_his_moments
        )
    return False

def _handle_extract_user_info(cmd):
    """UIBus EXTRACT_USER_INFO handler — 提取用户信息"""
    skip_avatar_if_exists = cmd.payload.get("skip_avatar_if_exists", True)
    account_id = cmd.wxid
    
    import app.state as app_state
    driver_inst = None
    try:
        if hasattr(app_state, 'account_manager') and app_state.account_manager:
            for inst in app_state.account_manager._instances.values():
                if inst.wxid == account_id or (not account_id and not inst.wxid):
                    driver_inst = inst.driver
                    break
    except Exception:
        pass
        
    if not driver_inst:
        driver_inst = app_state.driver
        
    if not driver_inst:
        return {"success": False, "error": "WeChatDriver 未初始化"}
        
    from src.uia.input_guard import UIAInterruptError
    try:
        driver_inst._extract_user_info(skip_avatar_if_exists=skip_avatar_if_exists)
        if not driver_inst._wxid or not driver_inst._nickname:
            return {"success": False, "error": "微信未登录，请先在微信中完成登录"}
        return {
            "nickname": driver_inst._nickname or "",
            "wxid": driver_inst._wxid or "",
            "success": True
        }
    except UIAInterruptError as e:
        return {"success": False, "error": str(e), "interrupted": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

def _handle_enable_voice_to_text(cmd):
    """UIBus ENABLE_VOICE_TO_TEXT handler — 开启语音转文字"""
    account_id = cmd.wxid
    
    import app.state as app_state
    driver_inst = None
    try:
        if hasattr(app_state, 'account_manager') and app_state.account_manager:
            for inst in app_state.account_manager._instances.values():
                if inst.wxid == account_id or (not account_id and not inst.wxid):
                    driver_inst = inst.driver
                    break
    except Exception:
        pass
        
    if not driver_inst or not driver_inst.is_connected():
        if hasattr(app_state, 'account_manager') and app_state.account_manager:
            driver_inst = app_state.account_manager.primary_driver
            
    if not driver_inst:
        driver_inst = app_state.driver
        
    if not driver_inst:
        return {"success": False, "error": "WeChatDriver 未初始化"}
        
    from src.uia.settings_automation import SettingsAutomation
    from src.uia.input_guard import UIAInterruptError
    try:
        res = SettingsAutomation.ensure_voice_transcription(driver_inst)
        if not res.get("success"):
            res["error"] = res.get("reason") or "自动配置语音转文字失败"
        return res
    except UIAInterruptError as e:
        return {"success": False, "error": str(e), "interrupted": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

def _handle_fetch_avatar(cmd):
    """UIBus FETCH_AVATAR handler — 抓取头像"""
    account_id = cmd.wxid
    
    import app.state as app_state
    driver_inst = None
    try:
        if hasattr(app_state, 'account_manager') and app_state.account_manager:
            for inst in app_state.account_manager._instances.values():
                if inst.wxid == account_id or (not account_id and not inst.wxid):
                    driver_inst = inst.driver
                    break
    except Exception:
        pass
        
    if not driver_inst or not driver_inst.is_connected():
        if hasattr(app_state, 'account_manager') and app_state.account_manager:
            driver_inst = app_state.account_manager.primary_driver
            
    if not driver_inst:
        driver_inst = app_state.driver
        
    if not driver_inst:
        return {"success": False, "error": "WeChatDriver 未初始化"}
        
    from src.uia.input_guard import UIAInterruptError
    try:
        return driver_inst.sync_avatar()
    except UIAInterruptError as e:
        return {"success": False, "error": str(e), "interrupted": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

def register_ui_bus_handlers():
    """注册所有的 UIBus handler 并启动 UIBus worker"""
    ui_bus.register_handler(UICommandKind.SEND_MESSAGE, _handle_send_message)
    ui_bus.register_handler(UICommandKind.SEND_FILE, _handle_send_file)
    ui_bus.register_handler(UICommandKind.SEND_VOICE, _handle_send_voice)
    ui_bus.register_handler(UICommandKind.SYNC_TAGS, _handle_sync_tags)
    ui_bus.register_handler(UICommandKind.PUBLISH_MOMENT, _handle_publish_moment)
    ui_bus.register_handler(UICommandKind.MOMENT_INTERACT, _handle_moment_interact)
    ui_bus.register_handler(UICommandKind.ADD_FRIEND, _handle_add_friend)
    ui_bus.register_handler(UICommandKind.ACCEPT_FRIEND, _handle_accept_friend)
    ui_bus.register_handler(UICommandKind.EXTRACT_USER_INFO, _handle_extract_user_info)
    ui_bus.register_handler(UICommandKind.ENABLE_VOICE_TO_TEXT, _handle_enable_voice_to_text)
    ui_bus.register_handler(UICommandKind.FETCH_AVATAR, _handle_fetch_avatar)
    ui_bus.start()

