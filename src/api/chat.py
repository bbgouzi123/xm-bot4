"""
聊天 API — 会话列表 / 消息读取 / 消息发送 (完美支持多号聚合)
"""
from fastapi import APIRouter, Request
import asyncio
import logging
from src.utils.response import ok, err, ok_msg

router = APIRouter()
logger = logging.getLogger(__name__)

_driver = None
_monitor = None

def init(driver, monitor=None):
    global _driver, _monitor
    _driver = driver
    _monitor = monitor

async def _run_uia(func, *args):
    """在专用 UIA 单线程池中运行 UIA 操作（COM 线程安全）"""
    from src.utils.uia_task_runner import run_in_uia_thread
    return await run_in_uia_thread(func, *args)

def _get_target_instance(bot_hwnd: int = 0):
    """获取目标微信实例，如果未传 bot_hwnd 或没找到，则退回默认 driver"""
    if bot_hwnd:
        try:
            from app.state import account_manager
            inst = account_manager._instances.get(bot_hwnd)
            if inst and inst.driver.is_connected():
                return inst.driver, inst
        except Exception: pass
    return _driver, None

# ==================== 会话列表 ====================

@router.get("/api/latest_sessions")
async def latest_sessions(limit: int = 20, prepare: bool = True, bot_hwnd: int = 0):
    """获取最新会话列表（支持多号双轨制：数据库优先，UIA兜底）"""
    target_drv, inst = _get_target_instance(bot_hwnd)
    logger.info(f"[DEBUG_CHAT_API] latest_sessions: bot_hwnd={bot_hwnd}, target_drv={target_drv is not None}, inst={inst.nickname if inst else 'None'}")
    if not target_drv or not target_drv.is_connected():
        logger.info(f"[DEBUG_CHAT_API] latest_sessions: driver not connected")
        return ok([])

    sessions = None
    # 1. 尝试优先通过 WCDB 数据库通道获取（即使 wcdb_mon 未启动，只要连接成功就尝试解密）
    if inst:
        try:
            logger.info(f"[DEBUG_CHAT_API] 尝试 WCDB 数据库通道获取会话列表 (实例={inst.nickname})")
            from src.wechat_4x.db_session_helper import get_latest_sessions_from_db
            loop = asyncio.get_running_loop()
            sessions = await loop.run_in_executor(
                None, lambda: get_latest_sessions_from_db(inst, limit)
            )
            logger.info(f"[DEBUG_CHAT_API] WCDB 数据库通道获取结果: {sessions is not None}, 数量={len(sessions) if sessions else 0}")
            if sessions:
                logger.info(f"[API] 成功从 WCDB 数据库通道获取会话列表 (实例={inst.nickname or bot_hwnd}, 数量={len(sessions)})")
        except Exception as dbe:
            logger.error(f"[API] WCDB 数据库通道获取会话列表异常: {dbe}", exc_info=True)
            sessions = None

    # 2. 数据库不可用、获取失败或数据为空，降级/兜底走 UIA 物理扫描通道
    if not sessions:
        try:
            if prepare:
                from src.utils.stop_signal import stop_signal
                stop_signal.reset()
            logger.info(f"[DEBUG_CHAT_API] 开始降级 UIA 扫描. prepare={prepare}")
            sessions = await _run_uia(target_drv.get_latest_sessions, limit, prepare)
            logger.info(f"[API] 降级: 从 UIA 物理扫描通道获取会话列表 (实例={inst.nickname if inst else 'main'}, 数量={len(sessions or [])})")
        except Exception as e:
            logger.error(f"获取 UIA 会话列表失败: {e}", exc_info=True)
            sessions = []

    try:
        if sessions:
            from src.utils.contacts_cache import contacts_cache
            account_id = inst.wxid if inst else "main"
            friends = contacts_cache.get_friends(account_id)
            takeover_map = {f.get("name"): f.get("is_takeover", False) for f in friends if f.get("name")}
            wxid_map = {f.get("name"): f.get("wxid", "") for f in friends if f.get("name")}
            for f in friends:
                if f.get("wxid"):
                    takeover_map[f["wxid"]] = f.get("is_takeover", False)
            for s in sessions:
                sname = s.get("name")
                s["is_takeover"] = takeover_map.get(sname, False) or takeover_map.get(s.get("wxid"), False)
                s["wxid"] = s.get("wxid") or wxid_map.get(sname, "")
                s["bot_hwnd"] = bot_hwnd or (inst.hwnd if inst else 0)
                s["bot_wxid"] = inst.wxid if inst else getattr(_driver, "bot_wxid", "")
                s["bot_nickname"] = inst.nickname if inst else getattr(_driver, "_nickname", "微信")
    except Exception as ex:
        logger.error(f"合并接管状态失败: {ex}")

    return ok(sessions or [])

@router.get("/api/chat/aggregated_sessions")
async def aggregated_sessions(limit: int = 20, prepare: bool = False):
    """多账号会话一键聚合汇总"""
    from app.state import account_manager
    all_sessions = []
    # 先快照，防止多开线程并发修改 _instances 时抛出 RuntimeError: dictionary changed size during iteration
    for hwnd, inst in list(account_manager._instances.items()):
        if inst.driver.is_connected():
            res = await latest_sessions(limit=limit, prepare=prepare, bot_hwnd=hwnd)
            if res.get("code") in (200, 20000) and isinstance(res.get("data"), list):
                all_sessions.extend(res["data"])
    return ok(all_sessions)


@router.post("/api/chat/scroll-sessions")
async def scroll_sessions(request: Request):
    body = await request.json()
    bot_hwnd = body.get("bot_hwnd", 0)
    target_drv, _ = _get_target_instance(bot_hwnd)
    if not target_drv or not target_drv.is_connected():
        return ok({"success": False})
    try:
        from src.utils.stop_signal import stop_signal
        stop_signal.reset()
        success = await _run_uia(target_drv.scroll_sessions, body.get("direction", "down"), body.get("times", 5))
        return ok({"success": success})
    except Exception as e:
        logger.error(f"滚动加载失败: {e}")
        return ok({"success": False})

from src.utils.chat_image_helper import resolve_chat_images_in_history


# ==================== 聊天消息 ====================

@router.get("/api/chat/messages")
async def get_chat_messages(
    session_name: str = "",
    parse_file: bool = False,
    context_count: int = 20,
    bot_hwnd: int = 0,
    lock: bool = False,
):
    target_drv, inst = _get_target_instance(bot_hwnd)
    if not target_drv or not target_drv.is_connected():
        return ok({"messages": [], "chatType": "unknown"})
    if not session_name:
        return err(40000, "缺少 session_name")

    # 1. 优先尝试数据库通道直接高速读取与解密
    try:
        import os
        from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
        from src.utils.chat_image_helper import resolve_wxid_from_cache_or_db
        
        active_wxid = (inst.wxid if inst else None) or target_drv._wxid or ""
        talker_wxid = resolve_wxid_from_cache_or_db(active_wxid, session_name)
        if talker_wxid:
            is_db_online = False
            if active_wxid:
                monitor = get_wcdb_monitor(active_wxid)
                if monitor and monitor.is_active():
                    is_db_online = True
                elif os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", ""):
                    is_db_online = True
                    
            if is_db_online:
                # 确定会话类别
                chat_type = "unknown"
                from src.uia.session import session_type_cache
                from src.utils.contacts_cache import contacts_cache
                cached_type = session_type_cache.get_type(session_name)
                if cached_type:
                    chat_type = cached_type
                else:
                    if active_wxid:
                        friends = contacts_cache.get_friends(active_wxid) or []
                        if any(f.get("name") == session_name for f in friends):
                            chat_type = "friend"
                        else:
                            groups = contacts_cache.get_groups(active_wxid) or []
                            if any(g.get("name") == session_name for g in groups):
                                chat_type = "group"
                
                # 如果是 lock，我们仅通过 UIA 界面异步做会话跳转，读库本身完全独立返回，大幅降低响应延迟
                if lock:
                    def _switch_work():
                        try:
                            target_drv.ChatWith(session_name, lock_input=True, foreground=True)
                        except Exception: pass
                    asyncio.create_task(_run_uia(_switch_work))
                
                from src.utils.chat_image_helper import get_chat_history_from_db_clean
                nickname = target_drv._nickname or "我"
                db_messages = get_chat_history_from_db_clean(
                    active_wxid, talker_wxid, context_count, nickname, session_name, chat_type
                )
                logger.info(f"[API] 🚀 成功走数据库通道高速读取聊天历史 (会话={session_name}, 数量={len(db_messages)})")
                return ok({"messages": db_messages, "chatType": chat_type})
    except Exception as ex_db:
        logger.warning(f"[API] 数据库通道读取历史异常，将自动降级走 UIA 物理扫描: {ex_db}")

    # 2. 数据库不可用时，兜底走 UIA 物理扫描通道
    try:
        from src.utils.chat_image_helper import get_chat_messages_uia_fallback
        res = await get_chat_messages_uia_fallback(
            target_drv, session_name, parse_file, context_count, lock, _run_uia
        )
        return ok(res)
    except Exception as e:
        logger.error(f"获取聊天记录失败: {e}")
        return ok({"messages": [], "chatType": "unknown"})

# ==================== 发送消息 ====================

@router.post("/api/chat/send")
async def send_message(request: Request):
    body = await request.json()
    user = body.get("user", "")
    message = body.get("message", "")
    bot_hwnd = body.get("bot_hwnd", 0)
    if not user or not message:
        return err(40000, "缺少必要参数")
    target_drv, _ = _get_target_instance(bot_hwnd)
    if not target_drv or not target_drv.is_connected():
        return err(40000, "微信实例未连接")
    try:
        result = await _run_uia(target_drv.send_message, user, message)
        return ok({"message": "发送成功"}) if result else err(50000, "发送失败")
    except Exception as e:
        logger.error(f"发送消息失败: {e}")
        return err(40000, "操作失败", {"message": str(e)})

@router.post("/api/chat/send-file")
async def send_file(request: Request):
    body = await request.json()
    user = body.get("user", "")
    file_path = body.get("file_path", "")
    bot_hwnd = body.get("bot_hwnd", 0)
    target_drv, _ = _get_target_instance(bot_hwnd)
    if not target_drv or not target_drv.is_connected():
        return err(40000, "微信实例未连接")
    if not user or not file_path:
        return err(40000, "缺少必要参数")
    try:
        result = await _run_uia(target_drv.SendFiles, user, file_path)
        return ok({"message": "发送成功"}) if result else err(50000, "发送失败")
    except Exception as e:
        logger.error(f"发送文件失败: {e}")
        return err(40000, "操作失败", {"message": str(e)})

@router.post("/api/chat/set_session_type")
async def set_session_type(request: Request):
    body = await request.json()
    name = body.get("name", "")
    session_type = body.get("type", "")  # "friend", "chat", "group", "official_account"
    if not name or session_type not in ("friend", "chat", "group", "official_account"):
        return err(40000, "参数错误")
    if session_type == "chat":
        session_type = "friend"
    try:
        from src.uia.session import session_type_cache
        session_type_cache.set_type(name, session_type)
        logger.info(f"[会话类别修正] 手动设置会话 '{name}' 的类别为: {session_type}")
        return ok({"message": "修正成功"})
    except Exception as e:
        logger.error(f"修正会话类别失败: {e}")
        return err(50000, f"操作失败: {str(e)}")

# ==================== 自动化操作 ====================

@router.post("/api/chat/ensure-chat-view")
async def ensure_chat_view(request: Request):
    from src.utils.chat_actions_helper import ensure_chat_view_impl
    return await ensure_chat_view_impl(request, _get_target_instance, _run_uia)

# ==================== 退出聊天页面 ====================

@router.post("/api/chat/leave")
async def leave_chat_page():
    from src.utils.chat_actions_helper import leave_chat_page_impl
    return await leave_chat_page_impl()