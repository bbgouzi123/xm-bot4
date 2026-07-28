import os
import tempfile
import logging
import win32gui
import uiautomation as uia
import re
from src.utils.safe_uia import (
    safe_walk_control,
    safe_control_type,
    safe_get_name,
    safe_bounding_rect
)
from src.uia.retry import random_delay

logger = logging.getLogger("SearchHelperUtils")

def get_popover_helper(driver):
    # 优先使用 win32 快速查找窗口句柄，彻底规避从 desktop 根部遍历导致的死锁或长超时
    try:
        hwnd = win32gui.FindWindow("mmui::SearchContentPopover", None)
        if hwnd:
            return uia.ControlFromHandle(hwnd)
    except Exception:
        pass
    # 兜底：在微信主窗口范围内查找，避免遍历整个桌面
    try:
        p = driver.root.WindowControl(ClassName="mmui::SearchContentPopover")
        if p.Exists(0.05):
            return p
    except Exception:
        pass
    try:
        p = driver.root.PaneControl(ClassName="mmui::SearchContentPopover")
        if p.Exists(0.05):
            return p
    except Exception:
        pass
    return None

def normalize_name(s: str) -> str:
    return re.sub(r'[^\w\u4e00-\u9fa5]', '', s).lower().strip()

def match_name_helper(val: str, session_name: str, session_name_truncated: str) -> bool:
    if not val:
        return False
    val_clean = val.strip()
    # 1. 精确/子串匹配
    if session_name in val_clean or session_name_truncated in val_clean:
        return True
    # 2. 归一化匹配
    norm_val = normalize_name(val_clean)
    norm_session = normalize_name(session_name)
    norm_session_trunc = normalize_name(session_name_truncated)
    if norm_session and norm_session in norm_val:
        return True
    if norm_session_trunc and norm_session_trunc in norm_val:
        return True
    return False

def get_dhash_obj(img, size=8):
    try:
        from PIL import Image
        img_gray = img.convert('L').resize((size + 1, size), Image.Resampling.LANCZOS)
        pixels = list(img_gray.getdata())
        difference = []
        for row in range(size):
            for col in range(size):
                pixel_left = pixels[row * (size + 1) + col]
                pixel_right = pixels[row * (size + 1) + col + 1]
                difference.append(pixel_left > pixel_right)
        decimal_value = 0
        hash_string = ""
        for index, value in enumerate(difference):
            if value:
                decimal_value += 2 ** (index % 8)
            if (index % 8) == 7:
                hash_string += f"{decimal_value:02x}"
                decimal_value = 0
        return hash_string
    except Exception as e:
        logger.debug(f"[UIA] get_dhash_obj 异常: {e}")
        return None

def get_dhash_file(img_path, size=8):
    try:
        from PIL import Image
        with Image.open(img_path) as img:
            return get_dhash_obj(img, size)
    except Exception as e:
        logger.debug(f"[UIA] get_dhash_file 异常: {e}")
        return None

def get_hamming_distance(h1, h2):
    if not h1 or not h2 or len(h1) != len(h2):
        return 999
    try:
        val1 = int(h1, 16)
        val2 = int(h2, 16)
        diff = val1 ^ val2
        return bin(diff).count('1')
    except Exception:
        return 999

def get_hover_preview_popover():
    try:
        hwnd = win32gui.FindWindow("Qt51514QWindowToolSaveBits", None)
        if hwnd:
            return uia.ControlFromHandle(hwnd)
    except Exception:
        pass
    return None

def evaluate_candidate_score(ctrl, name, ctrl_type, current_group_name, session_name, session_name_truncated, wxid, target_alias, exclude_keywords):
    name_matched = False
    matched_text = ""
    if name and match_name_helper(name, session_name, session_name_truncated):
        name_matched = True
        matched_text = name.strip()
    else:
        for child, _ in safe_walk_control(ctrl, max_depth=3):
            c_name = safe_get_name(child)
            c_type = safe_control_type(child)
            if c_name and any(ek in c_name for ek in exclude_keywords):
                continue
            if c_type == 'TextControl' and c_name and match_name_helper(c_name, session_name, session_name_truncated):
                name_matched = True
                matched_text = c_name.strip()
                break
                
    if not name_matched:
        return None, ""
        
    best_match_score = 0
    if matched_text == session_name or matched_text == session_name_truncated:
        best_match_score = 50
    elif matched_text.strip() == session_name.strip() or matched_text.strip() == session_name_truncated.strip():
        best_match_score = 45
    else:
        best_match_score = 20
        
    score = 50 + best_match_score
        
    if current_group_name:
        if any(gk in current_group_name for gk in ["联系人", "群聊", "微信群", "企业", "文件传输助手", "功能"]):
            score += 30
        elif any(gk in current_group_name for gk in ["搜一搜", "搜索网络结果", "最近在搜", "小程序", "公众号"]):
            score -= 60
            
    if ctrl_type in ['ButtonControl', 'CustomControl', 'ListItemControl']:
        score += 10
        
    # 🌟 精准匹配防同名冲突：如果列表项的 AutomationId 匹配真实的 wxid，赋予超级高分
    ctrl_autoid = getattr(ctrl, "AutomationId", "") or ""
    if wxid and ctrl_autoid in (f"search_item_{wxid}", f"search_item_function_{wxid}"):
        score += 500
        logger.info(f"[UIA] 检索扫描行匹配到目标 wxid: '{wxid}'，AutomationId: '{ctrl_autoid}'，评分加 500")

    # 🌟 检查子级 Text 节点是否匹配目标的 wxid 或其微信号别名 (alias)
    if wxid:
        for child, _ in safe_walk_control(ctrl, max_depth=3):
            c_name = safe_get_name(child)
            c_type = safe_control_type(child)
            if c_type == 'TextControl' and c_name:
                c_name_clean = c_name.strip()
                if wxid in c_name_clean or (target_alias and target_alias in c_name_clean):
                    score += 500
                    logger.info(f"[UIA] 子节点文本 '{c_name_clean}' 成功匹配目标 wxid '{wxid}' 或别名 '{target_alias}'，评分加 500")
                    break

    # 🌟 方案二首选：图像头像哈希比对防同名冲突
    matched_avatar = False
    if wxid:
        try:
            from src.crm.account_data import ACCOUNTS_DIR
            target_avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
            if not os.path.exists(target_avatar_path) and target_alias:
                target_avatar_path = os.path.join(ACCOUNTS_DIR, f"{target_alias}.png")
            
            if os.path.exists(target_avatar_path):
                temp_dir = tempfile.gettempdir()
                import time
                cell_img_path = os.path.join(temp_dir, f"wechat_cell_{int(time.time() * 1000)}.png")
                
                ctrl.CaptureToImage(cell_img_path)
                
                from PIL import Image
                with Image.open(cell_img_path) as cell_img:
                    W, H = cell_img.size
                    if H > 0:
                        avatar_size = int(H * 56 / 96)
                        x0 = int(H * 16 / 96)
                        y0 = (H - avatar_size) // 2
                        crop_box = (x0, y0, x0 + avatar_size, y0 + avatar_size)
                        cropped_avatar = cell_img.crop(crop_box)
                        
                        candidate_hash = get_dhash_obj(cropped_avatar)
                        target_hash = get_dhash_file(target_avatar_path)
                        
                        if candidate_hash and target_hash:
                            distance = get_hamming_distance(candidate_hash, target_hash)
                            if distance <= 12:
                                score += 600
                                matched_avatar = True
                                logger.info(f"[UIA] 方案二（头像哈希）精准匹配成功！汉明距离: {distance}，评分加 600")
                            else:
                                logger.info(f"[UIA] 方案二（头像哈希）不匹配，汉明距离: {distance}")
                
                if os.path.exists(cell_img_path):
                    try:
                        os.remove(cell_img_path)
                    except Exception:
                        pass
        except Exception as e_img:
            logger.debug(f"[UIA] 方案二头像比对异常: {e_img}")

    # 🌟 方案一保底：若头像哈希未匹配，通过 Hover 触发侧边聊天历史匹配
    if wxid and not matched_avatar:
        try:
            rect = safe_bounding_rect(ctrl)
            if rect and (rect.right - rect.left) > 0:
                center_x = (rect.left + rect.right) // 2
                center_y = (rect.top + rect.bottom) // 2
                uia.MoveTo(center_x, center_y)
                random_delay(0.2, 0.3)
                
                popover = get_hover_preview_popover()
                if popover and popover.Exists(0.2):
                    msg_list = popover.ListControl(AutomationId="chat_message_list")
                    if msg_list.Exists(0.1):
                        preview_texts = []
                        for child in msg_list.GetChildren():
                            c_name = safe_get_name(child)
                            if c_name:
                                preview_texts.append(c_name.strip())
                        
                        db_history = []
                        try:
                            from src.crm.account_data import get_active_account
                            from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
                            active_acct = get_active_account()
                            if active_acct:
                                monitor = get_wcdb_monitor(active_acct)
                                if monitor and monitor.is_active():
                                    db_msgs = monitor.get_latest_messages(wxid, limit=3)
                                    db_history = [m["content"].strip() for m in db_msgs if m.get("content")]
                        except Exception as e_db:
                            logger.debug(f"[UIA] 方案一保底匹配读取数据库历史异常: {e_db}")
                            
                        matched_hist = False
                        for pt in preview_texts:
                            for db_msg in db_history:
                                if db_msg and (db_msg in pt or pt in db_msg):
                                    matched_hist = True
                                    break
                            if matched_hist:
                                break
                                
                        if matched_hist:
                            score += 600
                            logger.info(f"[UIA] 方案一保底（Hover聊天记录）精准匹配成功，评分加 600")
        except Exception as e_hover:
            logger.debug(f"[UIA] 方案一保底 Hover 匹配异常: {e_hover}")

    return score, matched_text
