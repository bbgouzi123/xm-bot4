"""
话术组 API (由 task_api 拆分以满足单文件 300 行有效代码限制)
"""
import logging
from fastapi import APIRouter, Request
from src.utils.db_manager import WeChatDBManager
from src.utils.response import ok, err

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/task", tags=["task"])

@router.get("/scripts/list")
async def list_script_groups():
    """获取所有话术组"""
    return ok({"groups": WeChatDBManager().get_all_script_groups()})


@router.post("/scripts/add")
async def add_script_group(request: Request):
    """新增话术组"""
    return ok({"group": WeChatDBManager().add_script_group(await request.json())})


@router.post("/scripts/update/{group_id}")
async def update_script_group(group_id: str, request: Request):
    """更新话术组"""
    if WeChatDBManager().update_script_group(group_id, await request.json()):
        return ok({"message": "话术组更新成功"})
    return err(40000, "话术组更新失败")


@router.delete("/scripts/delete/{group_id}")
async def delete_script_group(group_id: str):
    """删除话术组"""
    if WeChatDBManager().delete_script_group(group_id):
        return ok({"message": "话术组删除成功"})
    return err(40000, "话术组删除失败")
