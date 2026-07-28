"""
Fulfillment API — 自动履约能力配置与承诺任务审批路由器
"""
import logging
from datetime import datetime
from fastapi import APIRouter
from src.utils.db_manager import WeChatDBManager
from src.utils.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fulfillment", tags=["fulfillment"])


def ok(data: any) -> dict:
    return {"code": 20000, "data": data}


def err(code: int, message: str) -> dict:
    return {"code": code, "message": message}


@router.get("/capabilities")
async def get_fulfillment_capabilities():
    """获取所有已注册的自动履约能力选项"""
    db = WeChatDBManager()
    return ok(db.get_fulfillment_capabilities())


@router.put("/capabilities/{key}")
async def update_fulfillment_capability(key: str, payload: dict):
    """更新自动履约能力的配置（开关、配置JSON等）"""
    db = WeChatDBManager()
    success = db.update_fulfillment_capability(key, payload)
    if success:
        return ok({"message": f"履约能力 {key} 配置更新成功"})
    return err(40000, f"未找到该履约能力: {key}")


@router.post("/capabilities")
async def add_fulfillment_capability(payload: dict):
    """新增自定义履约能力"""
    db = WeChatDBManager()
    success = db.add_fulfillment_capability(payload)
    if success:
        return ok({"message": "新增自定义能力成功"})
    return err(40000, "新增失败，可能唯一标识 Key 已存在或参数不合法")


@router.delete("/capabilities/{key}")
async def delete_fulfillment_capability(key: str):
    """删除指定的自定义履约能力"""
    db = WeChatDBManager()
    success = db.delete_fulfillment_capability(key)
    if success:
        return ok({"message": f"自定义能力 {key} 已成功删除"})
    return err(40000, "删除失败，内置能力不允许删除或该能力不存在")



@router.post("/promises/{task_id}/approve")
async def approve_promise_task(task_id: str):
    """授权通过挂起的高危承诺待办任务，状态更改为 pending 并重新加入工作队列"""
    db = WeChatDBManager()
    success = db.update_promise_task(task_id, {
        "status": "pending",
        "approval_status": "approved",
        "error_message": ""
    })
    if success:
        await ws_manager.broadcast_json({
            "type": "promise_approval_change",
            "task_id": task_id,
            "status": "approved"
        })
        return ok({"message": "已授权通过该待办任务，准备异步执行"})
    return err(40000, "未找到指定的承诺待办任务")


@router.post("/promises/{task_id}/deny")
async def deny_promise_task(task_id: str):
    """拒绝并终止挂起的高危承诺待办任务，状态更改为 failed 并推送广播"""
    db = WeChatDBManager()
    success = db.update_promise_task(task_id, {
        "status": "failed",
        "approval_status": "denied",
        "finished_at": datetime.now().isoformat(),
        "error_message": "管理员人工拒绝执行该敏感任务"
    })
    if success:
        await ws_manager.broadcast_json({
            "type": "promise_approval_change",
            "task_id": task_id,
            "status": "denied"
        })
        return ok({"message": "已成功拒绝该承诺待办任务"})
    return err(40000, "未找到指定的承诺待办任务")
