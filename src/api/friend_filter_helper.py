"""
好友与群聊过滤配置辅助逻辑 (friend_filter_helper.py)
"""
import logging
import re
import hashlib
import asyncio
from src.utils.contacts_cache import contacts_cache

logger = logging.getLogger(__name__)

def is_synthetic_group_name(name: str) -> bool:
    """利用群聊特有特征或群聊人数后缀做自动检测"""
    return bool(re.search(r'[\(（]\d+[\)）]$', name))

def resolve_is_group(name: str, given_is_group: bool, wxid: str) -> bool:
    """自动纠偏逻辑：如果该会话名称在缓存中被归类为群聊，或者含有群聊后缀特征，则自动纠偏为群聊"""
    all_friends = contacts_cache.get_friends(wxid)
    all_groups = contacts_cache.get_groups(wxid)
    clean_name = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
    
    if any(g.get("name") == name or g.get("name") == clean_name for g in all_groups):
        return True
    if any((f.get("name") == name or f.get("name") == clean_name) and f.get("category") == "群聊" for f in all_friends):
        return True
    if is_synthetic_group_name(name) or is_synthetic_group_name(clean_name):
        return True
    return given_is_group

async def trigger_whitelist_retry(wxid: str, name: str, is_group: bool, found_wxid: str = None):
    """
    当联系人/群聊被手动加入白名单后，触发对此前因白名单拦截而处于未读状态的会话的自动回复重试。
    """
    try:
        from src.monitor.chat_monitor.message_scanner import MessageScannerLogic
        from src.utils.websocket_manager import ws_manager

        # 💡 双重 key 兼容：拦截时广播的 id 格式通常为 auto_reply_xxx，重试时需要兼顾 whitelist_xxx 和 auto_reply_xxx，
        # 以确保能从 ws_manager.task_cache 中提取到正确的拦截消息，并自动清除/转换控制中心的报警卡片。
        possible_task_ids = [f"whitelist_{name}", f"auto_reply_{name}"]
        if found_wxid:
            possible_task_ids.extend([f"whitelist_{found_wxid}", f"auto_reply_{found_wxid}"])

        # 尝试直接从 task_cache 中提取之前被拦截的那条消息内容以及归属 bot_wxid 与 friend_wxid
        cached_msg = None
        task_bot_wxid = None
        task_friend_wxid = None
        if hasattr(ws_manager, "task_cache"):
            for t_id in possible_task_ids:
                if t_id in ws_manager.task_cache:
                    msg_val = ws_manager.task_cache[t_id].get("data", {}).get("incoming_msg")
                    if msg_val and "不在自动回复白名单" not in msg_val and "请点击右侧按钮" not in msg_val:
                        cached_msg = msg_val
                        task_bot_wxid = ws_manager.task_cache[t_id].get("data", {}).get("bot_wxid")
                        task_friend_wxid = ws_manager.task_cache[t_id].get("data", {}).get("friend_wxid")
                        break

        real_friend_wxid = found_wxid or task_friend_wxid

        for t_id in possible_task_ids:
            if hasattr(ws_manager, "task_cache") and t_id in ws_manager.task_cache:
                await ws_manager.broadcast_task_update(
                    task_id=t_id,
                    task_type="自动回复",
                    status="completed",
                    progress=100,
                    total=100,
                    message="已成功加入白名单，正在重新评估未读消息...",
                    friend_name=name,
                    friend_wxid=real_friend_wxid,
                    incoming_msg=cached_msg or "已成功加入白名单，正在重新评估未读消息...",
                    is_group=is_group
                )

        for scanner in getattr(MessageScannerLogic, "_all_scanner_instances", []):
            scanner_wxid = getattr(scanner.driver, 'bot_wxid', None) or getattr(scanner.driver, '_wxid', None) or 'default'
            
            is_matched = False
            if scanner_wxid == wxid:
                is_matched = True
            elif wxid == 'default' or scanner_wxid == 'default':
                is_matched = True
            elif not wxid or not scanner_wxid:
                is_matched = True
            elif task_bot_wxid and task_bot_wxid != 'default' and scanner_wxid != 'default':
                if scanner_wxid == task_bot_wxid:
                    is_matched = True
                
            if is_matched and scanner.is_running():
                # 💡 精准实例归属校验：对于运行中的多实例 scanner，强行要求该实例的通讯录中必须包含此好友，
                # 绝对防御热重载残留旧实例或多账号运行中，对不归属该实例的好友投递重复重试，从而彻底消除控制中心 4 重重复卡片的 Bug。
                friends_list = contacts_cache.get_friends(scanner_wxid)
                groups_list = contacts_cache.get_groups(scanner_wxid)
                name_clean = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
                
                has_friend = any(
                    (f.get("wxid") == real_friend_wxid or f.get("wxid") == name or f.get("name") == name or f.get("remark") == name or f.get("alias") == name)
                    for f in friends_list
                )
                has_group = any(
                    (g.get("wxid") == real_friend_wxid or g.get("wxid") == name or g.get("name") == name or g.get("name") == name_clean)
                    for g in groups_list
                )
                
                # 🌟 [放行陌生人与精准归属]：如果 task_bot_wxid 能精确匹配该 scanner_wxid，或者 scanner_wxid 对应当前 API 调用的活跃 wxid，
                # 说明该会话肯定归属于这个 scanner 实例，即使它不在好友/群聊列表里（如新加好友或群内陌生人），也应予以放行。
                is_exempt = False
                if task_bot_wxid and scanner_wxid == task_bot_wxid:
                    is_exempt = True
                elif wxid and scanner_wxid == wxid:
                    is_exempt = True
                
                if not is_exempt and not (has_friend or has_group):
                    logger.debug(f"[白名单同步重试] 忽略不包含好友 '{name}' 的实例 '{scanner_wxid}'")
                    continue

                logger.info(f"[白名单同步重试] 清理会话 '{name}' (wxid={real_friend_wxid}) 的指纹去重缓存...")
                scanner._fingerprints.pop(name, None)
                scanner._last_seen_msg.pop(name, None)
                if real_friend_wxid:
                    scanner._fingerprints.pop(real_friend_wxid, None)
                    scanner._last_seen_msg.pop(real_friend_wxid, None)

                # 🌟 额外保障：清理拦截卡片广播的指纹缓存，避免 DB 轮询时因为此指纹依然在 _broadcasted_whitelist_fps 中而导致无法将新消息注入
                if hasattr(scanner, '_session_broadcasted_fps'):
                    for k in [name, real_friend_wxid]:
                        if k and k in scanner._session_broadcasted_fps:
                            fps_to_remove = scanner._session_broadcasted_fps.pop(k, set())
                            if hasattr(scanner, '_broadcasted_whitelist_fps') and fps_to_remove:
                                scanner._broadcasted_whitelist_fps.difference_update(fps_to_remove)

                # 🌟 重置 4.x 数据库未读同步引擎的 mtime 缓存，强制下一次轮询 (0.2s内) 立即重新读取未读消息
                wcdb_monitor = getattr(scanner, "_wcdb_session_monitor", None)
                if wcdb_monitor:
                    syncer = getattr(wcdb_monitor, "_unread_syncer", None)
                    if syncer:
                        syncer._last_mtime = 0.0
                        syncer._last_mtime_wal = 0.0
                        print(f"[白名单同步重试] 成功重置 4.x 数据库未读同步引擎的 mtime 缓存，以放行未读消息")
                        logger.info(f"[白名单同步重试] 成功重置 4.x 数据库未读同步引擎的 mtime 缓存")

                # 3. 极速路径：若能直接从内存缓存拿到刚才被拦截的消息，直接内存重试，免读数据库，100% 成功
                if cached_msg:
                    from src.crm.account_data import get_account_settings
                    # 🛡️ [Fix C] 优先使用 scanner 实例的真实 bot_wxid 读配置，
                    # 避免入参 wxid 为 'default' 时读到空配置，导致 is_at 判断使用默认值
                    _real_wxid = (
                        getattr(scanner.driver, 'bot_wxid', None)
                        or getattr(scanner.driver, '_wxid', None)
                        or wxid
                        or 'default'
                    )
                    if _real_wxid == 'default' and wxid and wxid != 'default':
                        _real_wxid = wxid
                    settings = get_account_settings(_real_wxid)
                    reply_cfg = settings.get("reply", {})
                    
                    from src.monitor.chat_monitor.check_utils import check_is_at_message
                    is_at = check_is_at_message(cached_msg, scanner.driver, wxid, reply_cfg)
                    
                    fp = hashlib.md5(f"{name}:RETRY:{cached_msg}".encode()).hexdigest()
                    logger.info(f"[白名单同步重试] 提取到内存拦截消息，直接投递至决策队列: {cached_msg} (is_at={is_at})")
                    scanner._enqueue_to_reply_buffer(
                        name=name,
                        last_msg=cached_msg,
                        is_group=is_group,
                        user_name=name,
                        is_at=is_at,
                        fp=fp,
                        wxid=real_friend_wxid
                    )
    except Exception as ex:
        logger.error(f"[白名单同步重试] 执行重试时出现未捕获异常: {ex}", exc_info=True)
