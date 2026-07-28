"""
群运营·智能分群分发服务

核心能力：
- 管理多个目标群的容量配置
- 维护全局已入群名单，跨群去重
- 按顺序将联系人依次填入目标群
- 支持按标签/指定联系人两种受众模式
"""
import json
import logging
import os
import time
import random
import asyncio
from typing import List, Optional, Dict, Any
from fastapi import APIRouter
from pydantic import BaseModel

from src.utils.response import ok, err
from src.crm.account_data import APP_DATA_DIR

logger = logging.getLogger(__name__)
router = APIRouter()

# ── 持久化文件路径 ────────────────────────────────────────────────
_DISPATCH_STATE_FILE = os.path.join(APP_DATA_DIR, "group_dispatch_state.json")

# ── 运行时状态 ────────────────────────────────────────────────────
_dispatch_state: Dict[str, Any] = {
    "running": False,
    "paused": False,
    "plans": [],            # 当前配置的群计划列表
    "dispatched_names": [], # 全局已入群昵称集合（去重用）
    "progress": {
        "total": 0,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "current_group": "",
    },
    "logs": [],             # 最近 200 条操作日志
}


# ═══════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════

class GroupPlan(BaseModel):
    """单个目标群配置"""
    group_name: str
    capacity: int = 300         # 该群最多拉入的人数
    current_count: int = 0      # 当前已确认在群人数（由同步功能填充）


class StartDispatchRequest(BaseModel):
    """启动分发任务请求"""
    plans: List[GroupPlan]                  # 目标群计划（有序）
    target_mode: str = "tag"                # "tag" | "contact"
    target_tags: List[str] = []             # 按标签圈选的标签名列表
    target_contacts: List[str] = []         # 按指定联系人（昵称列表）
    batch_size: int = 3                     # 每次批量邀请人数（1-10）
    interval_min: float = 15.0             # 每批之间最短间隔（秒）
    interval_max: float = 30.0             # 每批之间最长间隔（秒）
    discard_rate: float = 0.03             # 安全抛弃率（0~0.2）


# ═══════════════════════════════════════
# 持久化读写
# ═══════════════════════════════════════

def _load_state():
    global _dispatch_state
    try:
        if os.path.exists(_DISPATCH_STATE_FILE):
            with open(_DISPATCH_STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 只恢复去重名单和计划，运行状态重置
            _dispatch_state["dispatched_names"] = saved.get("dispatched_names", [])
            _dispatch_state["plans"] = saved.get("plans", [])
    except Exception as e:
        logger.warning(f"[群分发] 读取持久化状态失败: {e}")


def _save_state():
    try:
        os.makedirs(os.path.dirname(_DISPATCH_STATE_FILE), exist_ok=True)
        with open(_DISPATCH_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "dispatched_names": _dispatch_state["dispatched_names"],
                "plans": _dispatch_state["plans"],
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[群分发] 写入持久化状态失败: {e}")


def _add_log(level: str, group: str, name: str, message: str):
    entry = {
        "time": time.strftime("%H:%M:%S"),
        "level": level,
        "group": group,
        "name": name,
        "message": message,
    }
    _dispatch_state["logs"].insert(0, entry)
    if len(_dispatch_state["logs"]) > 200:
        _dispatch_state["logs"] = _dispatch_state["logs"][:200]


# ═══════════════════════════════════════
# 核心分发引擎
# ═══════════════════════════════════════

async def _run_dispatch_loop(req: StartDispatchRequest):
    from .task_service import _driver
    from src.uia.group_invite_helper import invite_friends_to_group
    from src.utils.stop_signal import stop_signal

    state = _dispatch_state

    # 构建候选名单（去重已处理过的）
    already = set(state["dispatched_names"])

    if req.target_mode == "tag":
        from src.utils.contacts_cache import contacts_cache
        from src.crm.account_data import get_active_account
        account_id = get_active_account()
        all_contacts = contacts_cache.get_contacts(account_id) or []
        candidates = [
            c.get("display_name") or c.get("nickname") or c.get("remark", "")
            for c in all_contacts
            if any(tag in (c.get("tags") or []) for tag in req.target_tags)
        ]
    else:
        candidates = list(req.target_contacts)

    # 过滤空串和已处理
    candidates = [n for n in candidates if n and n not in already]

    # 安全抛弃（防风控随机跳过）
    if req.discard_rate > 0:
        candidates = [n for n in candidates if random.random() > req.discard_rate]

    state["progress"]["total"] = len(candidates)
    state["progress"]["processed"] = 0
    state["progress"]["succeeded"] = 0
    state["progress"]["failed"] = 0

    if not candidates:
        _add_log("warn", "", "", "候选名单为空，无可分发联系人")
        state["running"] = False
        return

    # 构建群 → 剩余容量映射
    plans_runtime = [
        {"group_name": p["group_name"], "remain": p["capacity"] - p.get("current_count", 0)}
        for p in req.plans
    ]

    candidate_idx = 0

    for plan in plans_runtime:
        group_name = plan["group_name"]
        remain = plan["remain"]

        if remain <= 0:
            _add_log("info", group_name, "", f"群已满员（容量 {plan['remain']}），跳过")
            continue

        state["progress"]["current_group"] = group_name

        while remain > 0 and candidate_idx < len(candidates):
            if not state["running"] or state.get("paused"):
                logger.info("[群分发] 任务被暂停或终止")
                _save_state()
                return

            if stop_signal.is_stopped:
                state["paused"] = True
                _save_state()
                return

            # 取一批
            batch = candidates[candidate_idx: candidate_idx + req.batch_size]
            batch = batch[:remain]  # 不超群剩余容量
            candidate_idx += len(batch)
            remain -= len(batch)

            logger.info(f"[群分发] 准备邀请 {batch} 进入群「{group_name}」")
            _add_log("info", group_name, ", ".join(batch), "开始邀请...")

            res = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda b=batch, g=group_name: invite_friends_to_group(_driver, g, b)
            )

            ok_count = res.get("success_count", 0)
            fail_names = res.get("failed_names", [])

            # 不管成功与否都纳入已处理集合（防止反复重试打扰同一人）
            for name in batch:
                if name not in state["dispatched_names"]:
                    state["dispatched_names"].append(name)

            state["progress"]["processed"] += len(batch)
            state["progress"]["succeeded"] += ok_count
            state["progress"]["failed"] += len(fail_names)

            if ok_count > 0:
                _add_log("success", group_name, ", ".join(batch),
                         f"成功邀请 {ok_count} 人，失败 {len(fail_names)} 人")
            else:
                _add_log("error", group_name, ", ".join(fail_names), res.get("message", "邀请失败"))

            _save_state()

            # 间隔等待（分段 sleep，支持中途响应暂停/停止）
            interval = random.uniform(req.interval_min, req.interval_max)
            slept = 0.0
            while slept < interval:
                if not state["running"] or state.get("paused") or stop_signal.is_stopped:
                    break
                await asyncio.sleep(0.5)
                slept += 0.5

    _add_log("info", "", "", f"分发任务完成，共处理 {state['progress']['processed']} 人，成功 {state['progress']['succeeded']} 人")
    state["running"] = False
    _save_state()


# ═══════════════════════════════════════
# API 端点
# ═══════════════════════════════════════

@router.get("/group-dispatch/state")
async def get_dispatch_state():
    """获取当前分发任务状态（含进度、日志、已入群名单数量）"""
    return ok({
        "running": _dispatch_state["running"],
        "paused": _dispatch_state["paused"],
        "progress": _dispatch_state["progress"],
        "logs": _dispatch_state["logs"][:50],
        "dispatched_count": len(_dispatch_state["dispatched_names"]),
        "plans": _dispatch_state["plans"],
    })


@router.post("/group-dispatch/start")
async def start_dispatch(req: StartDispatchRequest):
    """启动智能分群分发任务"""
    from .task_service import _driver
    if not _driver or not _driver.is_connected():
        return err(40001, "微信未连接，请先登录微信")

    if _dispatch_state["running"]:
        return err(40002, "分发任务正在运行中，请先停止")

    if not req.plans:
        return err(40003, "请至少配置一个目标群")

    if req.target_mode == "tag" and not req.target_tags:
        return err(40004, "按标签模式时，标签不能为空")

    if req.target_mode == "contact" and not req.target_contacts:
        return err(40005, "按指定好友模式时，好友列表不能为空")

    _dispatch_state["running"] = True
    _dispatch_state["paused"] = False
    _dispatch_state["plans"] = [p.dict() for p in req.plans]
    _dispatch_state["logs"] = []

    asyncio.create_task(_run_dispatch_loop(req))

    return ok({"message": "智能分群分发任务已启动", "total_groups": len(req.plans)})


@router.post("/group-dispatch/pause")
async def pause_dispatch():
    if not _dispatch_state["running"]:
        return err(40000, "没有正在运行的分发任务")
    _dispatch_state["paused"] = True
    return ok({"message": "分发任务已暂停"})


@router.post("/group-dispatch/resume")
async def resume_dispatch():
    if not _dispatch_state["running"]:
        return err(40000, "没有正在运行的分发任务")
    _dispatch_state["paused"] = False
    return ok({"message": "分发任务已恢复"})


@router.post("/group-dispatch/stop")
async def stop_dispatch():
    _dispatch_state["running"] = False
    _dispatch_state["paused"] = False
    _save_state()
    return ok({"message": "分发任务已终止"})


@router.post("/group-dispatch/clear-dispatched")
async def clear_dispatched():
    """清空全局已入群名单（重置去重状态，允许重新分发）"""
    _dispatch_state["dispatched_names"] = []
    _save_state()
    return ok({"message": "已入群名单已清空，下次启动将重新分发所有候选"})


@router.post("/group-dispatch/sync-group-count")
async def sync_group_count(req: dict):
    """同步指定群的实际成员数，用于更新群容量显示"""
    group_name = req.get("group_name", "")
    if not group_name:
        return err(40000, "群名不能为空")

    from .task_service import _driver
    from src.uia.group_sync_helper import sync_group_members_via_uia

    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: sync_group_members_via_uia(_driver, group_name)
    )
    if not result.get("success"):
        return err(40002, result.get("message", "同步失败"))

    count = len(result.get("members", []))
    return ok({"group_name": group_name, "member_count": count})


# 启动时加载持久化状态
_load_state()
