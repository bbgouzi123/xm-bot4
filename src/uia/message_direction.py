# 消息发送方向判定（是否自己发的消息）
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

from .message_direction_helper import (
    mark_message_direction,
    get_cached_message_direction,
    get_dpi_scale,
    find_wechat_window,
    find_avatar_node_and_rect,
    find_avatar_rect,
    detect_is_self_by_avatar_location
)

from src.uia.message_direction_debug import (
    draw_debug_cross,
    print_detect_result,
    print_fallback_result
)

from src.uia.message_direction_click import (
    find_profile_hwnd,
    detect_is_self_by_avatar_click
)

def extract_avatar_name(ctrl) -> Optional[str]:
    # 通过 BFS 提取头像控件的 Name 属性（发送者昵称/备注）。
    try:
        scale = get_dpi_scale()
        node, rect = find_avatar_node_and_rect(ctrl, scale)
        if node:
            name = node.Name or ""
            if name and not any(k in name for k in ("搜索", "Search", "微信号", "wxid", "清除", "删除", "关闭", "返回")):
                return name
    except Exception as ex:
        logger.debug(f"[消息解析] 提取头像名字异常: {ex}")
    return None

def detect_is_self(ctrl, nickname: Optional[str] = None, session_name: Optional[str] = None, use_click_check: bool = False) -> bool:
    name = ctrl.Name or ""
    if name:
        name_clean = name.strip()
        is_media_placeholder = name_clean in (
            "[图片]", "[语音]", "[文件]", "[视频]", 
            "图片", "语音", "文件", "视频", 
            "[新消息]", "新消息", "[图片本地路径]"
        ) or "[图片本地路径]:" in name_clean
        
        def safe_mark(n, val):
            if not is_media_placeholder:
                mark_message_direction(n, val, session_name)

        # 🚀 尝试从带 session_name 的精确缓存中命中消息方向（包括多媒体占位符）
        cached_val = get_cached_message_direction(name, session_name)
        if cached_val is not None:
            logger.info(f"[消息解析] 🚀 命中内存防风控缓存 => 内容='{name_clean[:20]}' => is_self={cached_val}")
            return cached_val

        # 🌟 检查微信数据库（WCDB）通道是否在线（包括 DLL 模式和纯 Python 降级监控模式）
        is_wcdb_online = False
        is_fallback_online = False
        active_acct = None
        hex_key = ""
        try:
            import os
            from src.crm.account_data import get_active_account
            from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
            active_acct = get_active_account()
            if active_acct and active_acct != 'default':
                # 1. 检测主 DLL 引擎状态
                monitor = get_wcdb_monitor(active_acct)
                if monitor and monitor.is_active():
                    is_wcdb_online = True
                
                # 2. 检测纯 Python 影子消息历史同步器状态
                # 只要有了 hex_key，并且后台的影子拷贝同步器在运行，我们同样能通过内存/离线库判定方向，免去物理检测
                hex_key = os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
                if hex_key:
                    is_fallback_online = True
        except Exception:
            pass

        # 只要 DLL 在线，或者后台纯 Python 离线消息同步引擎启动了（有 hex_key 且同步就绪）
        # 我们就可以绝对禁用最后消息检测的物理采色和物理点击，以实现真正的零触碰和防风控
        if is_wcdb_online or is_fallback_online:
            use_click_check = False

        name_clean = name.strip()

        # ========================================================
        # 🛡️ P0 优先级：如果数据库或历史记忆在线，必须作为唯一权威真相源（Source of Truth）优先匹配，杜绝任何 UI 误差
        # ========================================================

        # 1️⃣ 优先匹配本地内存历史消息（防冷启动/频繁点击）
        if name_clean and session_name and name_clean not in ("[图片]", "[语音]", "[文件]", "图片", "语音", "文件"):
            try:
                from src.utils.chat_history import ChatHistoryManager
                from src.crm.account_data import get_active_account
                import re
                clean_session_id = re.sub(r'[\(\[\uff08]\d+[\)\]\uff09]$', '', session_name).strip()
                active_acct = get_active_account()
                history_mgr = ChatHistoryManager(active_acct)
                history = None
                # 双重匹配，保证带括号 and 不带括号 of session_id 都能取到历史
                for sid in (session_name, clean_session_id):
                    if sid:
                        history = history_mgr.load_history(sid)
                        if history:
                            break
                if history:
                    for h_msg in reversed(history):
                        h_content = (h_msg.get("content") or "").strip()
                        is_match = False
                        if h_content == name_clean:
                            is_match = True
                        elif h_content.startswith("[文件]"):
                            fn = h_content[4:].strip()
                            if fn and fn in name_clean:
                                is_match = True
                        elif "material_" in h_content and h_content in name_clean:
                            is_match = True

                        if is_match:
                            is_self = (h_msg.get("role") == "assistant")
                            logger.info(f"[消息解析] 🚀 优先匹配到本地内存历史消息 => 内容='{name_clean[:20]}' => is_self={is_self}")
                            safe_mark(name, is_self)
                            return is_self
            except Exception as e:
                logger.debug(f"[消息解析] 优先匹配本地内存历史异常: {e}")

        # 2️⃣ 若数据库在线或影子库在线，实时查询匹配数据库记录
        if (is_wcdb_online or is_fallback_online) and name_clean and session_name and active_acct:
            try:
                # 🌟 首先通过 contacts_cache 将 session_name 翻译为 talker_wxid (wxid)
                import re
                clean_session_id = re.sub(r'[\(\[\uff08]\d+[\)\]\uff09]$', '', session_name).strip() if session_name else ""
                
                from src.utils.contacts_cache import contacts_cache
                talker_wxid = None
                friends = contacts_cache.get_friends(active_acct) or []
                for f in friends:
                    f_name = f.get("name")
                    f_wxid = f.get("wxid")
                    f_alias = f.get("alias")
                    if f_name in (session_name, clean_session_id) or f_wxid in (session_name, clean_session_id) or (f_alias and f_alias in (session_name, clean_session_id)):
                        talker_wxid = f_wxid
                        break
                if not talker_wxid:
                    groups = contacts_cache.get_groups(active_acct) or []
                    for g in groups:
                        g_name = g.get("name")
                        g_wxid = g.get("wxid")
                        if g_name in (session_name, clean_session_id) or g_wxid in (session_name, clean_session_id):
                            talker_wxid = g_wxid
                            break
                if not talker_wxid:
                    talker_wxid = clean_session_id or session_name

                db_msgs = []
                if is_wcdb_online:
                    from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
                    monitor = get_wcdb_monitor(active_acct)
                    db_msgs = monitor.get_latest_messages(talker_wxid, limit=5)
                elif is_fallback_online:
                    # 从本地影子拷贝解密库读取最近 5 条消息
                    session_db = os.environ.get("WCDB_SESSION_DB_PATH", "")
                    if session_db and os.path.exists(session_db):
                        db_storage_dir = os.path.dirname(os.path.dirname(session_db))
                        decrypted_db = os.path.join(db_storage_dir, "message", "temp_msg_monitor", "message_shadow_dec.db")
                        if os.path.exists(decrypted_db):
                            import sqlite3
                            conn = sqlite3.connect(decrypted_db)
                            conn.row_factory = sqlite3.Row
                            cursor = conn.cursor()
                            try:
                                cursor.execute(
                                    "SELECT StrContent, IsSender FROM message WHERE StrTalker = ? ORDER BY localId DESC LIMIT 5",
                                    (talker_wxid,)
                                )
                                rows = cursor.fetchall()
                                for r in rows:
                                    db_msgs.append({
                                        "is_self": bool(r["IsSender"] == 1),
                                        "content": str(r["StrContent"] or "")
                                    })
                            finally:
                                conn.close()

                if db_msgs:
                    if not is_media_placeholder:
                        # 文本消息：精准匹配内容
                        for db_m in db_msgs:
                            db_content = db_m["content"].strip()
                            if db_content == name_clean:
                                is_self = db_m["is_self"]
                                logger.info(f"[消息解析] 🚀 优先匹配到微信数据库实时记录 => 内容='{name_clean[:20]}' => is_self={is_self}")
                                safe_mark(name, is_self)
                                return is_self
                    else:
                        # 多媒体消息：匹配最新的一条数据库记录
                        latest_db_msg = db_msgs[0]
                        db_content = latest_db_msg["content"].strip()
                        is_db_xml = db_content.startswith("<msg") or db_content.startswith("<?xml") or "<appmsg" in db_content or "<img" in db_content
                        if is_db_xml or not db_content:
                            is_self = latest_db_msg["is_self"]
                            logger.info(f"[消息解析] 🚀 多媒体占位符 '{name_clean}' 优先匹配到微信数据库最新多媒体记录 => is_self={is_self}")
                            return is_self
            except Exception as db_ex:
                logger.debug(f"[消息解析] 查询微信数据库最近记录异常: {db_ex}")

        # 3️⃣ 影子模式·冷启动增量同步解密兜底
        if not is_wcdb_online and is_fallback_online and name_clean and session_name and active_acct and hex_key and name_clean not in ("[图片]", "[语音]", "[文件]", "图片", "语音", "文件"):
            try:
                from src.wechat_4x.db_message_monitor import MessageDbFallbackMonitor
                temp_sync = MessageDbFallbackMonitor(active_acct)
                session_db = os.environ.get("WCDB_SESSION_DB_PATH", "")
                if not session_db:
                    from src.wechat_4x.db_match_helper import auto_detect_db_path
                    session_db = auto_detect_db_path(hex_key, active_acct)
                if session_db and os.path.exists(session_db):
                    tmp_dir = os.path.join(os.path.dirname(session_db), "temp_msg_monitor")
                    os.makedirs(tmp_dir, exist_ok=True)
                    shadow_db = os.path.join(tmp_dir, "message_shadow_sync.db")
                    decrypted_db = os.path.join(tmp_dir, "message_shadow_dec_sync.db")
                    
                    temp_sync._db_path = os.path.join(os.path.dirname(os.path.dirname(session_db)), "message", "message_0.db")
                    temp_sync._hex_key = hex_key
                    temp_sync._sync_once(shadow_db, decrypted_db, is_baseline=False)
                    
                    cached_val = get_cached_message_direction(name)
                    if cached_val is not None:
                        logger.info(f"[消息解析] 🚀 降级模式下通过同步解密更新缓存命中 => is_self={cached_val}")
                        return cached_val
            except Exception as sync_err:
                logger.debug(f"[消息解析] 降级同步解密兜底异常: {sync_err}")

        # ========================================================
        # ⚠️ Fallback 降级：只有在数据库未命中，或数据库离线时，才执行物理 UI 左右气泡偏向判定（防风控备选）
        # ========================================================

        # 🌟 00. 【零物理动作·备选物理坐标气泡左右偏向判定法】
        try:
            ctrl_rect = ctrl.BoundingRectangle
            if ctrl_rect and ctrl_rect.width() > 0:
                scale = get_dpi_scale()
                bubble_rect = None
                for child in ctrl.GetChildren():
                    c_rect = child.BoundingRectangle
                    # 过滤掉宽度小于 50 * scale 的超小控件（如头像），防止将头像误判为消息气泡
                    if c_rect and (50 * scale) < c_rect.width() < ctrl_rect.width() * 0.8:
                        c_cls = getattr(child, "ClassName", "") or ""
                        if "Chat" in c_cls or "mmui::" in c_cls or child.ControlTypeName in ("TextControl", "ImageControl", "ButtonControl", "PaneControl", "CustomControl"):
                            bubble_rect = c_rect
                            break
                if bubble_rect:
                    mid_x = ctrl_rect.left + ctrl_rect.width() // 2
                    bubble_mid_x = bubble_rect.left + bubble_rect.width() // 2
                    is_self_by_pos = (bubble_mid_x > mid_x)
                    logger.info(f"[消息解析] 🚀 [UIA备选] 气泡物理位置判定成功 => 气泡中点={bubble_mid_x}, 整行中点={mid_x} => is_self={is_self_by_pos}")
                    safe_mark(name, is_self_by_pos)
                    return is_self_by_pos
                else:
                    # 💡 兜底：通用左侧留白判定法（完美适应文件、图片、视频等无典型气泡的宽消息）
                    # 只要所有子控件的 left 都偏右（即没有子控件位于最左侧留白区），说明是自己发送的。
                    # ⚠️ 修复：必须存在子控件（且不是空列表），才做此留白判定，避免因 UIA 延迟返回空列表而误判为自己发送
                    has_left_content = False
                    left_boundary = ctrl_rect.left + int(65 * scale)
                    has_children = False
                    for child in ctrl.GetChildren():
                        c_rect = child.BoundingRectangle
                        if c_rect and c_rect.width() > 0:
                            # 剔除可能存在的整行背景或大框架
                            if c_rect.width() >= ctrl_rect.width() * 0.98:
                                continue
                            has_children = True
                            if c_rect.left < left_boundary:
                                has_left_content = True
                                break
                    if has_children and not has_left_content:
                        logger.info(f"[消息解析] 🚀 [UIA备选] 通用左侧留白判定成功 => left_boundary={left_boundary}，无左侧内容 => is_self=True")
                        safe_mark(name, True)
                        return True
        except Exception as pos_ex:
            logger.debug(f"[消息解析] 气泡物理位置判定异常: {pos_ex}")

    if not nickname:
        try:
            from src.crm.account_data import get_active_nickname
            nickname = get_active_nickname()
        except Exception:
            pass

    scale = get_dpi_scale()
    import re
    
    # 优先清理 session_name 的后缀，得到干净的会话昵称
    clean_session = ""
    is_group = False
    if session_name:
        is_group = bool(re.search(r'[\(\[\uff08]\d+[\)\]\uff09]$', session_name))
        clean_session = re.sub(r'[\(\[\uff08]\d+[\)\]\uff09]$', '', session_name).strip()

    try:
        # 🌟 1. [零物理动作·首选] 采用基于头像按钮在 UIA 中物理坐标左右偏向判定法（零物理风控，优先使用）
        ctrl_rect = ctrl.BoundingRectangle
        if ctrl_rect and ctrl_rect.width() > 0:
            is_self_by_avatar = detect_is_self_by_avatar_location(ctrl, ctrl_rect, scale)
            if is_self_by_avatar is not None:
                if name:
                    safe_mark(name, is_self_by_avatar)
                return is_self_by_avatar

        # 🌟 2. [零物理动作·次选] 头像名字匹配判定
        avatar_name = extract_avatar_name(ctrl)
        if avatar_name:
            if avatar_name == "我" or (nickname and avatar_name == nickname):
                logger.debug(f"[消息解析] 头像名字匹配自己成功: {avatar_name} => is_self=True")
                if name:
                    safe_mark(name, True)
                return True
            else:
                logger.debug(f"[消息解析] 头像名字为 '{avatar_name}'（非自己） => is_self=False")
                if name:
                    safe_mark(name, False)
                return False

        # 🌟 3. [零物理动作·三选] 昵称和“我”文字节点暴力匹配
        if nickname:
            from .message_direction_util import check_nickname_or_me_in_ctrl
            if check_nickname_or_me_in_ctrl(ctrl, nickname, ctrl_rect, scale):
                if name:
                    safe_mark(name, True)
                return True

        # 🌟 4. [零物理动作·四选] 颜色像素取样定位
        from .message_direction_color import detect_is_self_by_color
        res_by_color = detect_is_self_by_color(ctrl, name, session_name, use_click_check, scale)
        if res_by_color is not None:
            return res_by_color

        # 🌟 5. [类型过滤降级] 非文本类型一律降级判定为对方发送
        from .message_direction_util import check_non_text_class_name
        if check_non_text_class_name(ctrl):
            cls_name = ctrl.ClassName or ""
            logger.debug(f"[消息解析] 颜色检测未确认，过滤非文本控件 '{cls_name}'，降级判定为对方发送 (is_self=False)")
            if name:
                safe_mark(name, False)
                if session_name:
                    try:
                        from src.utils.chat_history import ChatHistoryManager
                        ChatHistoryManager().add_message(session_name, session_name, "user", name)
                    except Exception:
                        pass
            return False

        logger.debug(f"[消息解析] 零触碰降级：直接静默判定为对方发送 (is_self=False)")
        if use_click_check:
            print_fallback_result(name)
        return False
                
    except Exception as ex:
        logger.error(f"[消息解析] 判定是否为自己发的消息异常: {ex}")
        
    return False
