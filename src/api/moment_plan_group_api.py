"""
朋友圈计划组 API 路由
"""
from fastapi import APIRouter, Request
import logging
from src.utils.response import ok, err

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/moment/plan-groups")
async def get_plan_groups_api():
    try:
        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import MomentPlannerService

        account_id = get_active_account()
        planner = MomentPlannerService(account_id)
        groups = planner.get_plan_groups()
        return ok({"plan_groups": groups})
    except Exception as e:
        logger.error(f"查询朋友圈计划组异常: {e}")
        return err(40000, "操作失败", {"plan_groups": []})


@router.post("/api/moment/plan-groups")
async def save_plan_group_api(request: Request):
    try:
        data = await request.json()
        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import MomentPlannerService

        account_id = get_active_account()
        planner = MomentPlannerService(account_id)
        result = planner.save_plan_group(data)
        return ok({"message": "保存计划组成功", "plan_group": result})
    except Exception as e:
        logger.error(f"保存朋友圈计划组异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


@router.delete("/api/moment/plan-groups/{id}")
async def delete_plan_group_api(id: str):
    try:
        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import MomentPlannerService

        account_id = get_active_account()
        planner = MomentPlannerService(account_id)
        success = planner.delete_plan_group(id)
        if success:
            return ok({"message": "删除计划组成功"})
        else:
            return err(40400, "删除失败", {"message": "计划组不存在"})
    except Exception as e:
        logger.error(f"删除朋友圈计划组异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})
