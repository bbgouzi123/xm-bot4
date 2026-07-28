import logging
import hashlib
import time
from typing import Dict
from src.api.config_api import _load_configs
from src.utils.websocket_manager import ws_manager
from src.monitor.chat_monitor.message_scanner.utils import check_friend_in_list, check_group_in_list
from src.wechat_4x.db_unread_extractor import _clear_task_card

logger = logging.getLogger("DbUnreadSyncerHelper")


def process_unread_session(
    username: str,
    info: dict,
    scanner,
    account_id: str,
    active_username: str,
    wxid_to_name: Dict[str, str],
    group_wxid_to_name: Dict[str, str],
    reply_cfg: dict,
    friend_excludes: list,
    group_excludes: list,
    last_active_summaries: Dict[str, str],
    last_unread_counts: Dict[str, int] = None,
    is_startup_scan: bool = False,
):
    import datetime
    
    last_timestamp = info.get("last_timestamp") or 0
    summary = info.get("summary") or ""
    summary_stripped = summary.strip()

    is_friend_accept = ("我通过了你的朋友验证请求" in summary_stripped) or ("现在可以开始聊天了" in summary_stripped and ("你已添加了" in summary_stripped or "已同意你的好友" in summary_stripped))

    is_today = False
    if last_timestamp > 0:
        try:
            msg_date = datetime.datetime.fromtimestamp(last_timestamp).date()
            today = datetime.datetime.now().date()
            is_today = (msg_date == today)
        except Exception:
            pass

    if is_friend_accept and not is_today:
        logger.info(f"[数据库未读同步] 过滤非今天的新好友通过提示，会话: '{username}'")
        _clear_task_card(username, username, is_group=username.endswith("@chatroom"), friend_wxid=username)
        return

    # 🌟 极速去重与历史未读防刷防回滚校验：
    # 如果 unread_count > 0，但 summary 和上一次看到的完全相同，且当前未读数 <= 以前记录的未读数，
    # 说明只是以前就已经存在的陈旧未读消息（例如启动残留、或 DLL 内存极速刷新中的无变化波动），
    # 并没有任何新消息到来，直接阻断，防止反复切屏对齐死循环。
    if last_unread_counts is not None:
        u_prev = last_unread_counts.get(username, 0)
        u_now = info.get("unread_count", 0)
        prev_sum = last_active_summaries.get(username)
        # 始终更新最近消息采样
        last_active_summaries[username] = summary
        if u_now > 0 and prev_sum is not None and summary == prev_sum and u_now <= u_prev:
                _clear_task_card(username, username, is_group=username.endswith("@chatroom"), friend_wxid=username)
                return

    # 🌟 零时间限制：不论消息是今天、昨天或更久以前，只要未读数 > 0 且用户挂机/加白，均履约自动回复
    if not summary_stripped:
        # 空摘要通常是由于 4.x 接收多媒体消息（如图片、视频、文件）同步延迟或特殊空表导致。
        # 将其重置为 "[图片]"，以强制在回复阶段唤醒多媒体气泡物理获取逻辑，解析出真正的数据。
        info["summary"] = "[图片]"
        summary_stripped = "[图片]"

    if "语音" in summary_stripped:
        info["summary"] = "[语音]"

    # 🌟 核心系统会话与公众号过滤防御
    # @placeholder_foldgroup 是微信内部「折叠通知」虚拟会话，不可作为真实对话处理
    _INTERNAL_SESSIONS = {
        "fmessage", "medianote", "floatbottle", "filehelper", "newsapp",
        "helper_entry", "mphelper", "weibo", "qqmail", "tmessage", "blogapp",
        "@placeholder_foldgroup",  # 折叠通知虚拟会话
    }
    if not username or username.startswith("gh_") or username in _INTERNAL_SESSIONS:
        _clear_task_card(username, username, is_group=False, friend_wxid=username)
        return

    # 过滤所有 @ 开头的微信内部系统会话（非 @chatroom 群聊）
    if username.startswith("@") and not username.endswith("@chatroom"):
        _clear_task_card(username, username, is_group=False, friend_wxid=username)
        return

    is_group = username.endswith("@chatroom")

    name = group_wxid_to_name.get(username) if is_group else wxid_to_name.get(username)
    if not name or name == username:
        try:
            from src.utils.contacts_cache import contacts_cache
            if is_group:
                groups = contacts_cache.get_groups(account_id) or []
                for g in groups:
                    if g.get("wxid") == username:
                        name = g.get("name") or username
                        break
            else:
                friends = contacts_cache.get_friends(account_id) or []
                for f in friends:
                    if f.get("wxid") == username or f.get("alias") == username:
                        name = f.get("remark") or f.get("name") or username
                        break
        except Exception:
            pass
            
        # 如果缓存中临时没有，启动数据库单条实时精确反查自愈
        if not name or name == username:
            try:
                from src.utils.wcdb_name_helper import get_name_from_wcdb
                db_name = get_name_from_wcdb(account_id, username)
                if db_name:
                    name = db_name
            except Exception:
                pass
                
        if not name:
            name = username
    else:
        try:
            import app.state as app_state
            app_state.name_to_active_wxid[name] = username
        except Exception:
            pass
    
    # 🌟 提前做@检测与消息提取，以决定群聊下是否跳过白名单拦截卡片的展示
    from src.wechat_4x.db_unread_extractor import extract_and_check_at
    init_fp = hashlib.md5(f"{name}:{username}:{last_timestamp}:{summary_stripped}:REPLY".encode()).hexdigest()
    
    # 获取真正的消息摘要、是否有被@，以及最后一条是否为自发，防止未@群聊触发卡片闪烁与自回复误报
    real_summary, is_at_flag, sender_name, real_fp, sender_wxid, is_last_msg_self = extract_and_check_at(
        scanner, username, name, is_group, summary_stripped, last_timestamp, reply_cfg, account_id, init_fp
    )

    # 🌟 核心安全防线：通用自回复检测（防止机器人自己发出的回复被广播并在控制中心显示）
    is_self = False
    for prefix in ("我:", "我：", "Me:", "Me："):
        if real_summary.startswith(prefix):
            is_self = True
            break
            
    if not is_self:
        try:
            from src.uia.message_direction_helper import get_cached_message_direction
            if get_cached_message_direction(real_summary, session_name=name) or get_cached_message_direction(real_summary, session_name=username) or get_cached_message_direction(real_summary):
                is_self = True
                logger.info(f"[自回复防御] 检测到本地主动发送方向缓存命中。摘要: '{real_summary.strip()}'")
        except Exception as dir_ex:
            logger.debug(f"[自回复防御] 检查消息方向缓存异常: {dir_ex}")

    if not is_self:
        try:
            import app.state as app_state
            last_sents = getattr(app_state, 'last_sent_messages', [])
            summary_norm = real_summary.strip()
            for sent_msg in last_sents:
                sent_msg_norm = sent_msg.strip()
                if sent_msg_norm and (summary_norm.startswith(sent_msg_norm) or sent_msg_norm.startswith(summary_norm)):
                    is_self = True
                    logger.info(f"[自回复防御] 匹配到最近我方发送的消息记录。摘要: '{summary_norm}'")
                    break
        except Exception as app_ex:
            logger.debug(f"[自回复防御] 读取我方发送历史异常: {app_ex}")

    if not is_self:
        try:
            partition = scanner.get_account_partition(account_id)
            cache = partition.message_cache.get(username) or partition.message_cache.get(name)
            if cache and cache.get('reply_messages'):
                cache_age = time.time() - cache.get('timestamp', 0)
                if cache_age < 15:
                    summary_norm = real_summary.strip()
                    for r in cache['reply_messages']:
                        r_norm = r.replace('\n', '').strip()
                        if r_norm == summary_norm or (summary_norm in ("[图片]", "[视频]", "[文件]") and any(keyword in r_norm for keyword in ("[图片]", "[视频]", "[文件]"))):
                            is_self = True
                            logger.info(f"[自回复防御] 匹配到会话 '{name}' 在 15 秒内有我方发送记录 (age={cache_age:.1f}s)。摘要: '{summary_norm}'")
                            break
        except Exception as cache_ex:
            logger.debug(f"[自回复防御] 检查会话缓存异常: {cache_ex}")

    if is_self or is_last_msg_self:
        _clear_task_card(username, name, is_group=is_group, friend_wxid=username)
        return
    
    # 🌟 @所有人 豁免：含 @所有人/@all 的消息必须穿透白名单，与 UIA 通道 evaluator_group.py 保持一致
    import re as _wl_re
    _summary_clean = real_summary.replace('\u2005', ' ').replace('\u200b', '')
    _is_at_all = any(
        _wl_re.search(rf'@[\s\u2005]*{tag}', _summary_clean, _wl_re.IGNORECASE)
        for tag in ('所有人', 'all', 'All')
    )

    is_heat_active = False
    if is_group:
        # 如果是真实 @ 触发，立即在数据库同步器阶段就双向录入热度监控，实现双保险（防止 UIA 阶段脑裂）
        from src.monitor.chat_monitor.group_mention_decay import group_mention_decay_mgr
        if is_at_flag:
            group_mention_decay_mgr.record_at_dual(username, sender_name, sender_wxid)

        # 🌟 群聊仅艾特前置过滤：若开启了仅在被艾特时自动回复，且未判定为艾特，检测是否处于@热度有效期内
        auto_chat_group_at_only = reply_cfg.get("auto_chat_group_at_only", True)
        if auto_chat_group_at_only and not is_at_flag and not _is_at_all:
            if group_mention_decay_mgr.check_and_update_heat_dual(username, sender_name, sender_wxid, real_summary):
                is_heat_active = True
                logger.info(f"[热度衰减监控] 数据库未读同步检测到群聊 '{name}' 中的用户 '{sender_name}' ({sender_wxid}) 处于免 @ 活跃期，已放行。")
            else:
                # 写入指纹以防重复触发
                if hasattr(scanner, '_fingerprints'):
                    scanner._fingerprints.setdefault(username, set()).add(real_fp)
                    scanner._fingerprints.setdefault(name, set()).add(real_fp)
                _clear_task_card(username, name, is_group=True, friend_wxid=username)
                return

    is_blocked = False
    if is_group:
        bot_group_auto_start = reply_cfg.get("bot_group_auto_start", False)
        if not bot_group_auto_start:
            # 如果自动群聊开关关闭，直接跳过，不标记为被拦截，不广播
            _clear_task_card(username, name, is_group=True, friend_wxid=username)
            return
        group_mode = reply_cfg.get("auto_chat_group_mode", "black")
        in_group_list = check_group_in_list(name, username, group_excludes, account_id=account_id)

        if _is_at_all:
            logger.info(f"[数据库未读同步] 检测到 '@所有人' 群公告，豁免白名单拦截直接进入回复队列。会话='{name}'")
        elif group_mode == "white" and not in_group_list:
            is_blocked = True
    else:
        friend_mode = reply_cfg.get("auto_chat_friend_mode", "black")
        in_friend_list = check_friend_in_list(name, username, friend_excludes, account_id=account_id)
        if friend_mode == "white" and not in_friend_list:
            is_blocked = True

    if is_blocked:
        _clear_task_card(username, name, is_group=is_group, friend_wxid=username)
        # 统一使用前面校正后的 real_summary 和 real_fp 来触发卡片展示，防空摘要闪烁
        broadcasted = getattr(scanner, '_broadcasted_whitelist_fps', set())

        if real_fp not in broadcasted:
            print(f"[数据库未读同步] 拦截/过滤会话 '{name}' (wxid={username})，触发控制中心展示卡片. 消息: {real_summary}")
            scanner._update_overlay_and_broadcast_whitelist(name, is_group=is_group, wxid=username, incoming_msg=real_summary)
            if not hasattr(scanner, '_broadcasted_whitelist_fps'):
                scanner._broadcasted_whitelist_fps = set()
            scanner._broadcasted_whitelist_fps.add(real_fp)
            if not hasattr(scanner, '_session_broadcasted_fps'):
                scanner._session_broadcasted_fps = {}
            scanner._session_broadcasted_fps.setdefault(username, set()).add(real_fp)
    else:
        # 它是白名单中允许回复的会话，且目前处于未读状态！
        # 我们将其作为一条新消息注入到 scanner 的回复队列中，由 RPA 自动执行回复！
        
        # 🛡️ 零红点自回复防御与穿透机制：
        # 若该会话 unread_count=0，我们必须保证这条消息是对方发送的新消息，而非我方回复
        if info.get("unread_count", 0) == 0:
            # 💡 核心安全防线 1：如果当前未读数为 0，且当前并不处于活跃聊天窗口中，
            # 那么摘要的变化必然是由我方在其他端（如手机或群发群控）发送消息引起的。直接跳过，防止自回复与状态误判。
            if active_username != username:
                _clear_task_card(username, name)
                return

            # 1) 如果有系统自带或者非人类对话摘要，直接过滤
            if not real_summary.strip() or any(k in real_summary for k in ("撤回了一条消息", "已置顶", "已取消置顶", "群公告", "[红包]", "发送了", "收到红")):
                _clear_task_card(username, name)
                return
            
            # 3) 检查最新内容是否与上一次看到的一致
            prev_sum = last_active_summaries.get(username)
            last_active_summaries[username] = real_summary
            if prev_sum is None:
                # 启动或初次切换到活跃窗口时，进行基准采样保护，不触发自动回复
                _clear_task_card(username, name)
                return
            if real_summary == prev_sum:
                _clear_task_card(username, name)
                return

        # 1. 冷却避让保护：如果近期已回复，则不再注入（防重复触发）
        last_reply = 0.0
        if hasattr(scanner, '_last_reply_time'):
            last_reply = max(
                scanner._last_reply_time.get(username, 0.0),
                scanner._last_reply_time.get(name, 0.0)
            )
        cooldown = getattr(scanner, '_cooldown', 10.0)
        if time.time() - last_reply < cooldown:
            logger.info(f"[数据库未读同步] 会话 '{name}' (wxid={username}) 处于冷却避让期，跳过注入")
            _clear_task_card(username, name)
            return

        # 检查回复队列与去重（重发词豁免去重以穿透指纹拦截）
        is_re_request = False
        re_request_keywords = ["再发", "重发", "没收到", "重新", "再来", "发一份", "发一遍", "发个"]
        if any(kw in real_summary for kw in re_request_keywords):
            is_re_request = True
            logger.info(f"[数据库去重豁免] 检出重请求意图词，豁免好友 '{name}' 的指纹去重拦截")

        # 🌟 同时校验 username (wxid) 和 name 下的指纹缓存，避免重复
        existing_fps = set()
        if hasattr(scanner, '_fingerprints'):
            if username:
                existing_fps.update(scanner._fingerprints.get(username, set()))
            if name:
                existing_fps.update(scanner._fingerprints.get(name, set()))
        
        wcdb_fp = hashlib.md5(f"{name}:WCDB:{real_summary}".encode()).hexdigest()
        if wcdb_fp in existing_fps:
            logger.debug(f"[双发防御] 轮询通道检测到 WCDB 实时通道已处理会话 '{name}' 的消息，跳过重复注入")
            return
                
        # 2. 队列状态与处理状态重入保护
        key = username or name
        is_already_active = False
        if hasattr(scanner, '_is_session_processing') and scanner._is_session_processing(name, username):
            is_already_active = True
        if hasattr(scanner, '_message_buffer') and key in scanner._message_buffer:
            is_already_active = True

        if not is_already_active and (is_re_request or (real_fp not in existing_fps)):
            # 🌟 群聊仅艾特前置过滤：若开启了仅在被艾特时自动回复，且未判定为艾特，直接跳过不入回复队列
            auto_chat_group_at_only = reply_cfg.get("auto_chat_group_at_only", True)
            if is_group and auto_chat_group_at_only and not is_at_flag and not is_heat_active:
                # 写入指纹以防重复触发
                if hasattr(scanner, '_fingerprints'):
                    scanner._fingerprints.setdefault(username, set()).add(real_fp)
                    scanner._fingerprints.setdefault(name, set()).add(real_fp)
                logger.info(f"[数据库未读同步] 群聊 '{name}' (wxid={username}) 开启了仅@模式，且该最新消息未被@，跳过注入。摘要: '{real_summary[:30]}'")
                _clear_task_card(username, name)
                return

            scanner._enqueue_to_reply_buffer(
                name=name,
                last_msg=real_summary,
                is_group=is_group,
                user_name=sender_name,
                is_at=is_at_flag or is_heat_active,
                fp=real_fp,
                wxid=username,
                is_physical_at=is_at_flag
            )
            logger.info(f"[数据库未读同步] 发现白名单活跃穿透会话 '{name}' (wxid={username}, is_at={is_at_flag}) 新消息: {real_summary[:60]}，已成功注入 RPA 回复队列")
        else:
            return
