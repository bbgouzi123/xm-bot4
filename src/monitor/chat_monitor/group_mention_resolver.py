import logging
import re
import time
import os
import ctypes
import json
from typing import Any

logger = logging.getLogger(__name__)

def do_resolve_mention_sender_uia(
    engine: Any, name: str, account_id: str,
    nicknames_to_check: list[str], respond_to_all: bool, message: str, wxid: str = None
) -> dict:
    """
    跳转到@位置，获取准确的发送人姓名，并回填给工作流。返回 {"sender_name": xxx, "message": xxx}
    """
    # 🌟 @所有人 始终响应回复
    respond_to_all = True

    # 🌟 线程级 DPI 感知强制声明：保证多屏幕系统上 UIA 逻辑坐标与 Win32 物理屏幕点击坐标 100% 对齐，杜绝飞屏
    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    from src.uia.input_guard import uia_lock
    from src.uia.retry import smooth_click_at
    from src.uia.message_direction_helper import get_dpi_scale, find_profile_hwnd
    from src.utils.safe_uia import (
        safe_exists, safe_get_children, safe_walk_control, 
        safe_bounding_rect, safe_get_name, safe_control_type, safe_class_name
    )
    import win32gui
    import win32con

    # 🌟 1. 优先采用零物理动作的数据库提取法，秒级解析且 100% 避免 UI 挂起
    from .group_mention_wcdb import resolve_mention_sender_wcdb
    wcdb_res = resolve_mention_sender_wcdb(name, account_id, nicknames_to_check, respond_to_all, message)
    if wcdb_res:
        return wcdb_res

    # 🌟 2. 数据库提取失效时的物理 UIA 兜底提取流程
    clicked_avatar = False
    sender_name = ""  # 初始化防止 UnboundLocalError
    with uia_lock("正在定位群聊 @ 提及并解析发送人..."):
        if not engine.driver.ChatWith(name, lock_input=True, wxid=wxid):
            logger.warning(f"[Mention] ChatWith '{name}' failed")
            return None

        hwnd = getattr(engine.driver, 'hwnd', 0)
        if not hwnd:
            try:
                hwnd = win32gui.FindWindow("WeChatMainWndForPC", None) or win32gui.FindWindow("Qt51514QWindowIcon", None)
            except Exception:
                pass

        if not hwnd:
            logger.warning("[Mention] WeChat hwnd is invalid/zero")
            return None

        from src.utils.safe_uia import safe_control_from_handle
        wechat_win = safe_control_from_handle(hwnd)
        if not wechat_win:
            logger.warning("[Mention] safe_control_from_handle returned None")
            return None

        bar = wechat_win.ButtonControl(ClassName="mmui::UnreadBarView")
        bar_name = ""
        is_bar_present = safe_exists(bar, 0.2)
        win_rect = safe_bounding_rect(wechat_win) if wechat_win else None
        bar_rect = safe_bounding_rect(bar) if is_bar_present else None
        
        is_bar_visible = False
        if is_bar_present and win_rect and bar_rect and bar_rect.width() > 0 and bar_rect.height() > 0:
            if (win_rect.left < bar_rect.left < win_rect.right) and \
               (win_rect.top < bar_rect.top < win_rect.bottom):
                is_bar_visible = True
        
        if is_bar_visible:
            bar_name = safe_get_name(bar).strip()
            logger.info(f"[Mention] Found unread mention bar inside viewport: '{bar_name}'")
            smooth_click_at(bar)
            time.sleep(0.6)
        else:
            logger.info("[Mention] Unread mention bar is hidden or off-screen, skipping bar click.")
        
        from src.uia.elements import WxName
        msg_list = engine.driver._walk_find('ListControl', name=WxName.MESSAGE_LIST,
                                           class_name='mmui::RecyclerListView', max_depth=8) or \
                   engine.driver._walk_find('ListControl', name=WxName.MESSAGE_LIST, max_depth=8)
        
        found_item = None
        found_content = ""
        found_type = "none"
        if msg_list:
            from .group_mention_wcdb import extract_target_bubble
            found_item, found_content, found_type = extract_target_bubble(
                safe_get_children(msg_list), nicknames_to_check, respond_to_all, name, message=message
            )

            if not found_item:
                logger.info("[Mention] Mention bubble not in view, scrolling up to find it...")
                for scroll_idx in range(5):
                    try:
                        msg_list.WheelUp(wheelTimes=1)
                        time.sleep(0.4)
                    except Exception:
                        break
                found_item, found_content, found_type = extract_target_bubble(
                    safe_get_children(msg_list), nicknames_to_check, respond_to_all, name
                )

        if found_type == "all":
            logger.info(f"[Mention] Matched '@all' mention bubble: '{found_content[:20]}'")
            return {
                "sender_name": "",
                "message": found_content if found_content else None,
                "is_at_all": True
            }

        if found_item:
            logger.info(f"[Mention] Found target bubble: '{found_content[:20]}'")
            
            parent_ctrl = found_item.GetParentControl()
            # 🌟 零物理动作极速昵称提取：尝试直接从消息气泡的父容器中解析发送人昵称，规避高危的物理点击动作
            if parent_ctrl:
                # 1) 优先寻找同级的非气泡 TextControl（微信在群聊显示群成员昵称时，发送者昵称就是头像下或旁边的独立 TextControl）
                for c in safe_get_children(parent_ctrl):
                    try:
                        c_type = safe_control_type(c)
                        c_cls = safe_class_name(c)
                        c_name = safe_get_name(c).strip()
                        if c_type == "TextControl" and c_name and "ChatText" not in c_cls:
                            # 排除系统消息 and 时间占位符
                            if c_name not in ("SYS", "Time", "Recall") and not re.match(r'^\d+:\d+$', c_name):
                                sender_name = c_name
                                logger.info(f"[Mention] Successfully extracted sender name '{sender_name}' from TextControl sibling directly (Zero-Click)")
                                break
                    except Exception as e_text:
                        logger.debug(f"[Mention] Sibling TextControl parsing error: {e_text}")
                
                # 2) 其次尝试直接读取头像 ButtonControl 的 Name 属性（微信底层头像控件通常将用户昵称/备注设为 Name 以支持无障碍）
                if not sender_name:
                    for c in safe_get_children(parent_ctrl):
                        try:
                            c_type = safe_control_type(c)
                            c_cls = safe_class_name(c)
                            c_name = safe_get_name(c).strip()
                            if c_type == "ButtonControl" and "ChatAvatar" in c_cls:
                                # 剔除微信默认的无障碍占位词
                                if c_name and c_name not in ("头像", "avatar", "Avatar", "ChatAvatar", "发送者头像"):
                                    sender_name = c_name
                                    logger.info(f"[Mention] Successfully extracted sender name '{sender_name}' from avatar Button name directly (Zero-Click)")
                                    break
                        except Exception as e_avatar:
                            logger.debug(f"[Mention] Avatar Button parsing error: {e_avatar}")

            # 🌟 如果零物理动作法成功提取了名字，且它不是原始微信号/wxid，直接短路返回，完全跳过高风险的物理点击微信头像操作
            is_raw_wxid = False
            if sender_name:
                if sender_name.startswith("wxid_"):
                    is_raw_wxid = True
                elif re.match(r'^[a-zA-Z0-9_\-]{6,30}$', sender_name):
                    # 如果提取出来的是微信号（纯字母数字下划线减号），说明并非可读的群昵称/微信昵称，需要退回点击头像以获取真实昵称
                    is_raw_wxid = True

            if sender_name and not is_raw_wxid:
                return {
                    "sender_name": sender_name,
                    "message": found_content if found_content else None,
                    "is_at_all": False
                }

            # 3. 零物理动作提取失败时，才退回到点击头像弹窗的物理提取兜底
            avatar_ctrl = None
            if parent_ctrl:
                for c in safe_get_children(parent_ctrl):
                    if safe_control_type(c) == "ButtonControl" and "ChatAvatar" in safe_class_name(c):
                        avatar_ctrl = c
                        break
            
            if avatar_ctrl:
                dpi_scale = get_dpi_scale()
                rect = safe_bounding_rect(avatar_ctrl)
                if rect:
                    # 物理点击头像展开名片
                    try:
                        if win32gui.IsIconic(hwnd):
                            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                        win32gui.SetForegroundWindow(hwnd)
                        time.sleep(0.1)
                    except Exception:
                        pass
                        
                    smooth_click_at(avatar_ctrl)
                    clicked_avatar = True
                    time.sleep(0.5)

    if clicked_avatar:
        try:
            p_hwnd = find_profile_hwnd()
            if p_hwnd:
                logger.info(f"[Mention] Found avatar profile hwnd={p_hwnd}, extracting sender name...")
                profile_win = safe_control_from_handle(p_hwnd)
                if profile_win:
                    remark = ""
                    nickname_val = ""
                    title_name = ""
                    
                    for ctrl, d in safe_walk_control(profile_win, max_depth=4):
                        c_cls = safe_class_name(ctrl)
                        c_t = safe_control_type(ctrl)
                        if c_t in ("ImageControl", "ButtonControl", "PaneControl", "ThumbControl") or "Button" in c_cls:
                            continue
                            
                        c_n = safe_get_name(ctrl).strip()
                        if not c_n:
                            continue
                            
                        if c_t == "TextControl" and c_n in ("昵称：", "昵称:", "备注名：", "备注名:", "备注：", "备注:"):
                            try:
                                sib = ctrl.GetNextSiblingControl()
                                if sib:
                                    val = safe_get_name(sib).strip()
                                    if "昵称" in c_n:
                                        nickname_val = val
                                    else:
                                        remark = val
                            except Exception:
                                pass
                        elif c_cls == "mmui::XLineField":
                            if c_n != "添加备注名":
                                remark = c_n
                                    
                        if not title_name and c_t == "TextControl" and d in (2, 3):
                            if c_n not in ("微信号：", "地区：", "个性签名：", "标签：", "来源：", "备注名：", "备注：", "昵称：") and \
                               not c_n.startswith("微信号:") and not c_n.startswith("地区:") and not c_n.startswith("来源:"):
                                title_name = c_n
                                
                    extracted = remark or nickname_val or title_name
                    win32gui.PostMessage(p_hwnd, win32con.WM_CLOSE, 0, 0)
                    
                    for _ in range(10):
                        if not win32gui.IsWindow(p_hwnd):
                            break
                        time.sleep(0.05)

                    if extracted:
                        sender_name = extracted
                        logger.info(f"[Mention] Precise sender from avatar profile: '{sender_name}'")
            else:
                logger.warning("[Mention] Profile window not found after avatar click")
        except Exception as read_ex:
            logger.error(f"[Mention] Reading avatar profile failed: {read_ex}")

    return {
        "sender_name": sender_name,
        "message": found_content if found_content else None,
        "is_at_all": False
    }
