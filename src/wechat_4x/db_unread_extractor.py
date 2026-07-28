import logging
import hashlib
import re
import time
from typing import Optional
from src.monitor.chat_monitor.check_utils import check_is_at_message

logger = logging.getLogger("DbUnreadExtractor")

def extract_and_check_at(
    scanner, username: str, name: str, is_group: bool, 
    summary: str, last_timestamp: int, reply_cfg: dict, 
    account_id: str, original_fp: str
) -> tuple[str, bool, str, str, Optional[str]]:
    """
    拉取微信数据库历史消息，修复空/占位/截断的私聊摘要，
    并提取和分析群聊中的 @ 状态以及被 @ 的真实内容。
    返回 (real_summary, is_at_flag, sender_name, final_fp, sender_wxid)
    """
    is_at_flag = False
    sender_wxid = None
    recent_msgs = []
    _needs_db_fetch = False
    
    if hasattr(scanner, '_wcdb_session_monitor') and scanner._wcdb_session_monitor:
        _needs_db_fetch = True

    if _needs_db_fetch:
        # 🌟 延迟落盘防抖：如果检测到数据库内容和会话摘要不匹配，重试最多3次以等待 SQLite WAL 写入完成并成功解密
        for attempt in range(3):
            try:
                recent_msgs = scanner._wcdb_session_monitor.get_latest_messages(username, limit=10)
            except Exception as e_msg:
                logger.debug(f"[数据库未读同步] 获取{'群聊' if is_group else '私聊'}历史消息失败: {e_msg}")
                break

            if recent_msgs:
                _db_content = (recent_msgs[-1].get("content") or "").strip()
                _session_sum = (summary or "").strip()
                
                # 清洗会话摘要（剥离 [@所有人]/[有人@我]/发送人姓名等前缀）
                _session_sum_clean = _session_sum
                for prefix in ("[@所有人]", "[有人@我]"):
                    if _session_sum_clean.startswith(prefix):
                        _session_sum_clean = _session_sum_clean[len(prefix):].strip()
                colon_idx = _session_sum_clean.find(':')
                if colon_idx == -1:
                    colon_idx = _session_sum_clean.find('：')
                if colon_idx != -1 and colon_idx < 35:
                    _session_sum_clean = _session_sum_clean[colon_idx + 1:].strip()
                
                _is_stale = False
                _media_tags = {"[图片]", "[视频]", "[语音]", "[文件]", "[动画表情]", "图片", "语音", "视频", "文件"}
                if _session_sum_clean and _session_sum_clean not in _media_tags:
                    if _session_sum_clean not in _db_content and _db_content not in _session_sum_clean:
                        _is_stale = True
                elif _session_sum_clean in _media_tags:
                    if not _db_content.startswith("<msg"):
                        _is_stale = True
                
                if not _is_stale:
                    break
                else:
                    logger.info(f"[数据库未读同步] 检测到新消息未完全落盘，等待 0.4s 重试 ({attempt+1}/3)...")
                    time.sleep(0.4)
            else:
                if (summary or "").strip():
                    logger.info(f"[数据库未读同步] 数据库明细为空，等待 0.4s 重试 ({attempt+1}/3)...")
                    time.sleep(0.4)
                else:
                    break

    # 1. 私聊修复：若 summary 为空/占位符/被截断，用数据库拉取到的最新非自发消息替换
    final_summary = summary
    final_fp = original_fp
    
    # 🌟 核心安全性防线：仅在聊天记录最新一条消息不是我方发送的情况下（说明当前处于对方未读触发态），
    # 才允许对媒体占位符或截断空摘要进行数据库修复。如果最后一封是我方发的，证明会话已处于我方回复态，保留原始摘要不修复。
    if not is_group and recent_msgs and not recent_msgs[-1].get("is_self"):
        _latest_user_msg = None
        for _m in reversed(recent_msgs):  # 从最新往旧找
            if not _m.get("is_self"):
                _latest_user_msg = _m
                break
        if _latest_user_msg:
            _real_content = _latest_user_msg.get("content", "").strip()
            if _real_content and not _real_content.startswith("<") and _real_content != summary.strip():
                # 🌟 核心修复：只有当会话列表中的摘要确实是空、特殊占位符、或者是遭遇截断时，才允许从数据库获取真实内容替换
                _sum_stripped = summary.strip()
                _is_placeholder_or_truncated = (
                    not _sum_stripped 
                    or _sum_stripped in {"[图片]", "[视频]", "[语音]", "[文件]", "[动画表情]", "图片", "语音", "视频", "文件"}
                    or _sum_stripped.endswith("...")
                    or _sum_stripped.endswith("…")
                )
                if _is_placeholder_or_truncated:
                    logger.info(f"[数据库私聊修复] 会话 '{name}' 用数据库真实消息替换截断/空摘要: '{summary}' → '{_real_content[:60]}'")
                    final_summary = _real_content
                    final_fp = hashlib.md5(f"{name}:{username}:{last_timestamp}:{final_summary}:REPLY".encode()).hexdigest()

    # 2. 提取发送者姓名
    sender_name = name
    if is_group:
        body = final_summary.strip()
        prefix_match = re.match(r'^(\[[^\]]+\]\s*)*', body)
        if prefix_match:
            body = body[prefix_match.end():].strip()
        
        colon_idx = body.find(':')
        if colon_idx == -1:
            colon_idx = body.find('：')
        if colon_idx != -1 and colon_idx < 35:
            parsed_sender = body[:colon_idx].strip()
            if parsed_sender and not (parsed_sender.startswith('[') and parsed_sender.endswith(']')):
                if parsed_sender.lower() not in ("http", "https", "ftp", "file", "ws", "wss"):
                    sender_name = parsed_sender

    # 3. 诊断与艾特检测
    if is_group and recent_msgs:
        logger.debug(f"[数据库@诊断] 群='{name}', recent_msgs={len(recent_msgs)}, summary='{final_summary[:30]}'")

    if recent_msgs:
        msgs_to_scan = _build_at_scan_msgs(scanner, recent_msgs, username, name)
        logger.debug(f"[数据库@诊断] msgs_to_scan={len(msgs_to_scan)}")

        for msg in msgs_to_scan:
            msg_content = msg.get("content", "")
            is_valid_receipt = False
            if hasattr(scanner, "_check_group_receipt"):
                is_valid_receipt = scanner._check_group_receipt(msg_content, is_group, reply_cfg)
                
            _at_result = check_is_at_message(msg_content, scanner.driver, account_id, reply_cfg)
            if is_valid_receipt or _at_result:
                is_at_flag = True
                logger.debug(f"[数据库@诊断] ✅ 命中@消息! content前50字='{msg_content[:50].replace(chr(10), chr(92)+chr(110))}'")
                try:
                    m_sender = re.match(r"^([a-zA-Z0-9_\-]+):\s*\n(.*)$", msg_content, re.DOTALL)
                    if m_sender:
                        sender_wxid = m_sender.group(1)
                        actual_body = m_sender.group(2).strip()
                        from src.wechat_4x.wcdb_monitor_helpers import resolve_display_name
                        disp_name = resolve_display_name(account_id, sender_wxid, False)
                        sender_name = disp_name if disp_name else sender_wxid
                        final_summary = actual_body
                        logger.info(f"[数据库未读修复] 检测到群聊中被覆盖 of @ 消息。已将摘要重置为被@的内容: '{final_summary}'，发送人: '{sender_name}'")
                    else:
                        final_summary = msg_content
                        logger.info(f"[数据库未读修复] 检测到群聊中被覆盖 of @ 消息(无前缀)。已将摘要重置为: '{final_summary}'")
                    
                    final_fp = hashlib.md5(f"{name}:{username}:{last_timestamp}:{final_summary}:REPLY".encode()).hexdigest()
                except Exception as parse_ex:
                    logger.error(f"[数据库未读修复] 提取 @ 消息内容异常: {parse_ex}")
                    break
                break
            else:
                logger.debug(f"[数据库@诊断] ❌ 非@消息: receipt={is_valid_receipt}, at={_at_result}, content前40字='{msg_content[:40].replace(chr(10), chr(92)+chr(110))}'")

    # 🌟 双保险外层兜底：如果前面数据库检索未认定为 @ 消息（可能是由于解密延迟导致最新的一条消息还没在 recent_msgs 里体现），
    # 但最终的 session.db 未读摘要明确提示被 @，则无条件相信会话层状态，强制判定为 @ 消息，防止漏回！
    if not is_at_flag:
        fallback_at = check_is_at_message(final_summary, scanner.driver, account_id, reply_cfg)
        if fallback_at:
            is_at_flag = True
            logger.info(f"[数据库@诊断] 数据库检索未命中但会话摘要 fallback 命中@: '{final_summary}'")

    # 🌟 核心修复：如果是 @ 消息，且前面成功提取了 sender_wxid，保留它，绝对不能被最新非 @ 消息的 wxid 覆盖！
    # 只有当非 @ 消息（追问模式）或者未成功提取到 sender_wxid 时，才从最近一条他人消息中提取 wxid
    if is_group and recent_msgs:
        if not is_at_flag or not sender_wxid:
            # 追问消息或未提取到，使用最新一条他人消息的 wxid/nickname 绑定，确保同源
            for msg in reversed(recent_msgs):
                if not msg.get("is_self"):
                    msg_content = msg.get("content", "")
                    m_sender = re.match(r"^([a-zA-Z0-9_\-]+):\s*\n", msg_content)
                    if m_sender:
                        sender_wxid = m_sender.group(1)
                        # 同时更新 sender_name 为该最新消息发送人的昵称，确保 WXID 与 昵称 100% 同源绑定，杜绝串脑裂！
                        from src.wechat_4x.wcdb_monitor_helpers import resolve_display_name
                        disp_name = resolve_display_name(account_id, sender_wxid, False)
                        if disp_name:
                            sender_name = disp_name
                        break

    is_last_msg_self = False
    if recent_msgs:
        # 🌟 核心防抖修复：检查数据库中的最新消息是否和当前的最新未读摘要相符（进行内容模糊对齐校验）
        # 如果当前未读摘要是一个普通文本，而数据库中的最新消息内容完全跟摘要无关，说明数据库同步（解密落盘或影子拷贝）存在滞后延迟（stale），
        # 此时绝对不能相信 recent_msgs[-1].get("is_self")，必须判定 is_last_msg_self 为 False 放行，防止将新消息错认成自己发的从而误触发跳过！
        _db_content = (recent_msgs[-1].get("content") or "").strip()
        _session_sum = (final_summary or "").strip()
        
        _is_stale = False
        _media_tags = {"[图片]", "[视频]", "[语音]", "[文件]", "[动画表情]", "图片", "语音", "视频", "文件"}
        if _session_sum and _session_sum not in _media_tags:
            if _session_sum not in _db_content and _db_content not in _session_sum:
                _is_stale = True
        elif _session_sum in _media_tags:
            # 🌟 对于媒体消息，如果数据库里的内容是 xml 媒体协议流（以 <msg 开头），说明数据库已成功同步该媒体消息，判定不 stale；
            # 如果数据库最新记录依然是普通的文本（代表刚才发的媒体消息在 DB 中尚未同步到），判定为 stale。
            if not _db_content.startswith("<msg"):
                _is_stale = True
                
        if not _is_stale:
            is_last_msg_self = bool(recent_msgs[-1].get("is_self"))

    return final_summary, is_at_flag, sender_name, final_fp, sender_wxid, is_last_msg_self


def _build_at_scan_msgs(scanner, recent_msgs: list, username: str, name: str) -> list:
    """构建用于@消息检测的消息列表，优先按 is_self=True 作为 Bot 发言边界，降级用时间戳。"""
    unread_after_self = []
    for msg in reversed(recent_msgs):   # 倒序：新→旧
        if msg.get("is_self"):
            break
        unread_after_self.append(msg)

    if unread_after_self:
        return unread_after_self

    _last_reply_ts = max(
        getattr(scanner, '_last_reply_time', {}).get(username, 0.0),
        getattr(scanner, '_last_reply_time', {}).get(name, 0.0)
    )
    if _last_reply_ts > 0:
        ts_filtered = [
            m for m in reversed(recent_msgs)
            if not m.get("is_self") and m.get("timestamp", 0) > _last_reply_ts
        ]
        if ts_filtered:
            return ts_filtered
        return []

    return list(reversed(recent_msgs))


def _clear_task_card(username: str, name: str, is_group: bool = None, friend_wxid: str = None):
    """广播 completed 消除 5% 进度残留卡片。必须同时传 friend_wxid+is_group，否则 fill_whitelist_status
    无法正确识别 white_list_status，导致前端显示「列表：未设置」和多余「加白」按钮。"""
    try:
        from src.utils.websocket_manager import ws_manager
        import asyncio
        _is_group = is_group if is_group is not None else username.endswith("@chatroom")
        _friend_wxid = friend_wxid or username
        # 当 name 是 wxid 时，尝试从 contacts_cache 反查中文名
        _display_name = name
        if (not name or name == username):
            try:
                from src.utils.contacts_cache import contacts_cache as _cc
                from src.crm.account_data import get_active_account as _get_acct
                _acct = _get_acct() or ""
                if _acct and _acct != "default":
                    _pool = _cc.get_groups(_acct) if _is_group else _cc.get_friends(_acct)
                    _name_field = "name" if _is_group else "remark"
                    _display_name = next((x.get(_name_field) or x.get("name") or username for x in (_pool or []) if x.get("wxid") == username or x.get("alias") == username), username)
                    # 若 contacts_cache 仍未命中（缓存未建立），再用 wcdb 层做一次精确解密反查
                    if _display_name == username:
                        from src.utils.wcdb_name_helper import get_name_from_wcdb
                        _db_name = get_name_from_wcdb(_acct, username)
                        if _db_name:
                            _display_name = _db_name
            except Exception:
                pass
        task_id = f"auto_reply_{username}"
        if hasattr(ws_manager, "task_cache") and task_id in ws_manager.task_cache:
            # 🌟 [关键防护] 如果当前自动回复任务正处于运行中 (running) 状态，代表 RPA 正在为该会话读取
            # 微信上下文、发起大模型推理或在 UIBus 总线队列中排队等待物理发送，决不能因为物理窗口切换
            # 导致未读数归零，而强行将其广播为“已跳过回复”完成态。否则会导致前端进度条断裂、卡片状态异常。
            cached_task = ws_manager.task_cache[task_id]
            cached_status = cached_task.get("data", {}).get("status")
            if cached_status == "running":
                logger.info(f"[门卫清除] 会话 '{username}' 正在回复中 (status=running)，忽略本次未读归零清除卡片请求")
                return

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(ws_manager.broadcast_task_update(
                    task_id=task_id, task_type="自动回复",
                    status="completed", progress=100, total=100,
                    message="已跳过回复", friend_name=_display_name,
                    friend_wxid=_friend_wxid, is_group=_is_group
                ), loop)
            else:
                ws_manager.task_cache.pop(task_id, None)
    except Exception:
        pass

