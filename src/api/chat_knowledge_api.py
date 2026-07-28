"""
聊天知识库采集 API — 路由层（薄封装）

端点：
1. POST /api/chat-knowledge/collect      — 启动采集任务
2. GET  /api/chat-knowledge/collect/status — 查询采集进度
3. POST /api/chat-knowledge/collect/stop  — 终止采集
4. POST /api/chat-knowledge/preview       — 预览当前会话的可提取知识（不持久化）

核心采集逻辑在 chat_knowledge_collector.py 中。
"""
import asyncio
import logging
import threading

from fastapi import APIRouter, Request
from src.utils.response import ok, err, ok_msg
from src.api.chat_knowledge_collector import (
    collect_state, collect_lock, stop_flag,
    cloud_create_task, do_collect,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_driver = None


def init(driver):
    """由 main.py 注入 UIA 驱动"""
    global _driver
    _driver = driver


async def _run_uia(func, *args):
    """在专用 UIA 单线程池中运行 UIA 操作（COM 线程安全）"""
    from src.utils.uia_task_runner import run_in_uia_thread
    return await run_in_uia_thread(func, *args)


def _get_target_instance(bot_hwnd: int = 0):
    """获取目标微信实例"""
    if bot_hwnd:
        try:
            from app.state import account_manager
            inst = account_manager._instances.get(bot_hwnd)
            if inst and inst.driver.is_connected():
                return inst.driver, inst
        except Exception:
            pass
    return _driver, None


@router.post("/api/chat-knowledge/collect")
async def start_collect(request: Request):
    """启动聊天知识库采集任务

    请求体: { "friend_name", "industry_id", "max_scroll", "bot_hwnd" }
    """
    with collect_lock:
        if collect_state["running"]:
            return err(40900, "已有采集任务正在进行中，请等待完成或先终止")

    body = await request.json()
    friend_name = body.get("friend_name", "").strip()
    friend_wxid = body.get("friend_wxid", "").strip()
    industry_id = body.get("industry_id", "").strip()
    sync_all = body.get("sync_all", False)
    if sync_all:
        max_scroll = 10000
    else:
        max_scroll = min(body.get("max_scroll", 30), 10000)
    bot_hwnd = body.get("bot_hwnd", 0)

    if not friend_name:
        return err(40000, "请指定要采集的好友名称")

    target_drv, inst = _get_target_instance(bot_hwnd)
    if not target_drv or not target_drv.is_connected():
        return err(40000, "微信实例未连接，请先启动并绑定微信")

    bot_wxid = inst.wxid if inst else getattr(target_drv, "bot_wxid", "")

    # 暂停自动回复（防止采集操作与自动化冲突）
    try:
        from app.state import account_manager
        for _h, _inst in account_manager._instances.items():
            if hasattr(_inst, 'monitor') and _inst.monitor:
                _inst.monitor.pause()
                logger.info("[知识库] 已临时暂停 AI 自动回复")
    except Exception:
        pass

    # 创建 Cloud 端任务
    task_id = cloud_create_task(friend_name, industry_id, friend_wxid=friend_wxid, bot_wxid=bot_wxid)
    if not task_id:
        return err(50000, "创建采集任务失败，请检查网络连接")

    # 更新全局状态
    stop_flag.clear()
    with collect_lock:
        collect_state.update({
            "running": True, "task_id": task_id,
            "friend_name": friend_name, "industry_id": industry_id,
            "total": 0, "processed": 0, "extracted": 0,
            "status": "running", "error": "",
        })

    # 在后台线程中执行采集
    t = threading.Thread(
        target=do_collect,
        args=(target_drv, friend_name, industry_id, max_scroll, bot_wxid, task_id, friend_wxid),
        daemon=True, name="knowledge-collector",
    )
    t.start()

    return ok({"task_id": task_id, "friend_name": friend_name, "message": "采集任务已启动"})


@router.get("/api/chat-knowledge/collect/status")
async def get_collect_status():
    """查询当前采集进度"""
    return ok(dict(collect_state))


@router.post("/api/chat-knowledge/collect/stop")
async def stop_collect():
    """终止当前采集任务"""
    if not collect_state["running"]:
        return ok_msg("当前没有正在执行的采集任务")
    stop_flag.set()
    return ok_msg("已发送停止信号，采集将在当前批次完成后终止")


@router.post("/api/chat-knowledge/preview")
async def preview_knowledge(request: Request):
    """预览当前聊天窗口中的可提取知识（不持久化）"""
    body = await request.json()
    bot_hwnd = body.get("bot_hwnd", 0)
    friend_name = body.get("friend_name", "")
    friend_wxid = body.get("friend_wxid", "").strip()

    target_drv, inst = _get_target_instance(bot_hwnd)
    if not target_drv or not target_drv.is_connected():
        return err(40000, "微信实例未连接")

    bot_wxid = inst.wxid if inst else getattr(target_drv, "bot_wxid", "")
    nickname = target_drv._nickname or "我"

    try:
        # 1. 尝试走数据库获取最近 50 条
        from src.api.chat_knowledge_collector import get_chat_history_from_db
        messages = get_chat_history_from_db(bot_wxid, friend_name, friend_wxid, 50, nickname)
        
        if messages:
            logger.info(f"[知识库] 预览功能走数据库获取成功，获取到 {len(messages)} 条记录")
        else:
            logger.info(f"[知识库] 预览功能数据库获取不可用或无记录，回退 UIA 获取...")
            def _uia_work():
                if friend_name and not target_drv.ChatWith(friend_name):
                    return []
                raw = target_drv.get_all_messages(False, 50, friend_name or "")
                return [
                    {"content": msg[1], "isSelf": (msg[0] == nickname or msg[0] == "我"), "type": "text"}
                    for msg in raw if len(msg) > 1 and msg[1]
                ]

            messages = await _run_uia(_uia_work)

        if not messages:
            return ok({"total_messages": 0, "qa_pairs": [], "preview_count": 0})

        from src.ai.knowledge_extractor import KnowledgeExtractor
        extractor = KnowledgeExtractor(dedup=True)
        qa_pairs = extractor.extract_qa_pairs(messages)
        for qa in qa_pairs:
            qa["question"] = extractor.desensitize(qa["question"])
            qa["answer"] = extractor.desensitize(qa["answer"])

        return ok({"total_messages": len(messages), "qa_pairs": qa_pairs[:20], "preview_count": len(qa_pairs)})
    except Exception as e:
        logger.error(f"[知识库] 预览失败: {e}")
        return err(50000, f"预览失败: {str(e)}")
