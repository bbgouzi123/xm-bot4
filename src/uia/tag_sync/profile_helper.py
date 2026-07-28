import time
import logging
from typing import Optional
import uiautomation as uia

from .utils import (
    random_delay,
    exists_safe,
    try_click,
    bfs_find,
)

logger = logging.getLogger(__name__)

from src.uia.message_direction import find_profile_hwnd

def open_friend_profile(driver) -> Optional[object]:
    root = driver.root
    if not root:
        print("[资料名片] 微信 root 控件无效")
        return None

    # 0. 预先清理和关闭任何残留的资料名片弹窗，避免检测到历史残留窗口
    print("[资料名片] 清理可能残留的历史 ProfileUniquePop 弹窗...")
    try:
        import win32gui
        import win32con
        for _ in range(3):
            hwnd = find_profile_hwnd()
            if hwnd:
                print(f"[资料名片] 发现残留名片窗口 hwnd={hwnd}，正在关闭...")
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                random_delay(0.2, 0.4)
            else:
                break
    except Exception as ex:
        logger.warning(f"[资料名片] 清理历史弹窗异常: {ex}")

    # 1. 优先尝试从聊天记录中直接点击好友头像打开名片页
    print("[资料名片] 尝试从当前聊天记录列表定位好友头像并物理点击...")
    try:
        list_ctrl = root.ListControl(ClassName="RecyclerListView")  # 兼容以前的类名匹配
        if not exists_safe(list_ctrl, 0.5):
            list_ctrl = root.ListControl(ClassName="mmui::RecyclerListView")
            
        if exists_safe(list_ctrl, 1.0):
            children = list_ctrl.GetChildren()
            print(f"[资料名片] 找到聊天记录 RecyclerListView，包含 {len(children)} 条消息")
            for child in reversed(children):
                cls_name = getattr(child, "ClassName", "") or ""
                if any(msg_cls in cls_name for msg_cls in ["ChatTextItemView", "ChatBubbleItemView", "ChatBubbleReferItemView", "ChatVoiceItemView", "ChatFileItemView", "ChatImageItemView", "ChatVideoItemView"]):
                    # 提取当前会话名称以准确区分消息方向
                    session_name = ""
                    try:
                        chat_view = root.GroupControl(ClassName="mmui::ChatDetailView")
                        if exists_safe(chat_view, 0.1):
                            edit = chat_view.EditControl(ClassName="mmui::ChatInputField", searchDepth=4)
                            if edit.Exists(0.1):
                                session_name = edit.Name or ""
                                import re
                                session_name = re.sub(r'\s+按住.*$', '', session_name)
                                session_name = re.sub(r'\(\d+\)$', '', session_name).strip()
                    except Exception:
                        pass

                    from src.uia.message import _detect_is_self
                    from src.uia.message_direction import find_avatar_rect, get_dpi_scale
                    if not _detect_is_self(child, nickname=driver._nickname, session_name=session_name):
                        rect = child.BoundingRectangle
                        if rect and rect.left > 0 and rect.top > 0:
                            # 再次确保在物理点击前没有残留的名片窗口
                            try:
                                import win32gui, win32con
                                hwnd = find_profile_hwnd()
                                if hwnd:
                                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                                    random_delay(0.2, 0.4)
                            except Exception:
                                pass

                            scale = get_dpi_scale()
                            avatar_rect = find_avatar_rect(child, scale)
                            if avatar_rect:
                                x = (avatar_rect.left + avatar_rect.right) // 2
                                y = (avatar_rect.top + avatar_rect.bottom) // 2
                                print(f"[资料名片] 探测到头像子控件，使用精确坐标: ({x}, {y})")
                            else:
                                # 微信 PC 端聊天记录项(child)左边界 rect.left 对应聊天区左侧分界线，好友头像中心固定在偏置位置并需计算 DPI 缩放
                                x = rect.left + int(38 * scale)
                                y = rect.top + int(30 * scale)
                                print(f"[资料名片] 未探测到头像子控件，使用带缩放的偏右偏移坐标: ({x}, {y})，当前缩放: {scale}")

                            from src.uia.retry.clicks import physical_click
                            driver.last_avatar_pos = (x, y)
                            physical_click(x, y)
                            random_delay(1.0, 1.5)

                            # 循环等待并获取 ProfileUniquePop 弹窗
                            profile_win = None
                            for _ in range(15):
                                hwnd = find_profile_hwnd()
                                if hwnd:
                                    profile_win = uia.ControlFromHandle(hwnd)
                                    break
                                time.sleep(0.1)

                            if profile_win:
                                print("[资料名片] 成功通过聊天区域直接点击头像打开好友资料名片")
                                return profile_win
                            else:
                                print("[资料名片] 物理点击后，未检测到名片弹出")
    except Exception as avatar_ex:
        logger.warning(f"[资料名片] 尝试在聊天区直接定位并点击头像失败，将回退到群成员列表方案: {avatar_ex}")

    # 2. 回退到传统的“聊天信息 -> 成员列表 -> 点击头像”流程
    print("[资料名片] 回退方案：寻找 '聊天信息' 按钮...")
    chat_info_btn = root.ButtonControl(Name="聊天信息")
    if not exists_safe(chat_info_btn, 1.5):
        chat_view = root.GroupControl(ClassName="mmui::ChatDetailView")
        if exists_safe(chat_view, 1.0):
            chat_info_btn = chat_view.ButtonControl(Name="聊天信息")

    if not exists_safe(chat_info_btn, 1.5):
        print("[资料名片] 回退失败：未找到 '聊天信息' 按钮")
        return None

    print("[资料名片] 尝试点击 '聊天信息' 按钮...")
    if not try_click(chat_info_btn, max_retries=3, delay=0.3):
        print("[资料名片] 回退失败：点击 '聊天信息' 失败")
        return None
    random_delay(0.8, 1.3)

    print("[资料名片] 寻找成员列表中的好友头像 (ChatMemberCell)...")
    member_btn = root.ButtonControl(ClassName="mmui::ChatMemberCell")
    if not exists_safe(member_btn, 1.5):
        print("[资料名片] 未直接发现 ChatMemberCell，限制深度执行 BFS 查找...")
        member_btn = bfs_find(
            root,
            ControlTypeName="ButtonControl",
            ClassName="mmui::ChatMemberCell",
            max_depth=5,
            max_count=100
        )

    if not member_btn or not exists_safe(member_btn, 1.0):
        print("[资料名片] 回退失败：未找到好友头像 (ChatMemberCell)")
        uia.SendKeys("{ESC}")
        return None

    print("[资料名片] 尝试点击好友头像单元格...")
    rect = member_btn.BoundingRectangle
    if rect:
        mx = (rect.left + rect.right) // 2
        my = (rect.top + rect.bottom) // 2
        driver.last_avatar_pos = (mx, my)

    if not try_click(member_btn, max_retries=3, delay=0.3):
        print("[资料名片] 回退失败：点击好友头像失败")
        uia.SendKeys("{ESC}")
        return None
    random_delay(0.9, 1.5)

    print("[资料名片] 循环等待 ProfileUniquePop 名片弹窗弹出...")
    profile_win = None
    for i in range(10):
        hwnd = find_profile_hwnd()
        if hwnd:
            profile_win = uia.ControlFromHandle(hwnd)
            break
        print(f"[资料名片] 等待名片弹出中 (第 {i+1} 次等待)...")
        time.sleep(0.2)

    if not profile_win:
        print("[资料名片] 回退失败：好友资料弹窗未出现")
        uia.SendKeys("{ESC}")
        return None

    print("[资料名片] 成功打开好友资料弹窗")
    return profile_win
