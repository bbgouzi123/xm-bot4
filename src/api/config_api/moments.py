from fastapi import Request
import threading
import json
import logging
from src.utils.response import ok, err, ok_msg
from .state import router, CONFIG_DIR
from . import state

logger = logging.getLogger(__name__)

@router.post("/api/moment/post-task")
async def create_moment_post(request: Request):
    body = await request.json()
    text = body.get("text", "")
    images = body.get("images", [])

    if not text and not images:
        return err(40000, "缺少 text 或 images")

    if not state._driver or not state._driver.is_connected():
        return err(40000, "微信未连接")

    from src.utils.license_validator import LicenseValidator
    import asyncio
    loop = asyncio.get_running_loop()
    features = await loop.run_in_executor(None, LicenseValidator.check_features)
    if not features.get("moments_auto", False):
        return err(40301, "当前版本不支持朋友圈发帖自动化功能，请升级套餐")

    if state._post_thread and state._post_thread.is_alive():
        return err(40000, "发帖任务正在运行中")

    state._post_running = True
    state._post_result = None

    def run_post():
        try:
            from src.monitor.moment_post import MomentPost
            poster = MomentPost(state._driver)
            if images:
                state._post_result = poster.publish_with_images(text, images)
            else:
                state._post_result = poster.publish_text(text)
        except Exception as e:
            state._post_result = {"success": False, "error": str(e)}
        finally:
            state._post_running = False

    state._post_thread = threading.Thread(target=run_post, daemon=True)
    state._post_thread.start()
    return ok({"message": "发帖任务已启动"})

@router.get("/api/moment/post-tasks")
async def list_moment_tasks():
    return ok([])

@router.get("/api/moment/interactions")
async def moment_interactions():
    log_file = CONFIG_DIR / "moment_interactions.json"
    try:
        if log_file.exists():
            data = json.loads(log_file.read_text(encoding='utf-8'))
            return ok(data if isinstance(data, list) else [])
    except Exception:
        pass
    return ok([])

@router.post("/api/moment/post-task/cancel")
async def cancel_moment_post(request: Request):
    return ok_msg("操作成功")

@router.get("/api/moment/post-logs")
async def moment_post_logs():
    return ok([])


@router.get("/api/moment-material/plans")
async def moment_material_plans():
    try:
        from src.utils.moment_material import MomentMaterialManager
        manager = MomentMaterialManager()
        return ok(manager.list_plans())
    except Exception as e:
        logger.error(f"获取发圈计划失败: {e}")
        return ok([])

@router.get("/api/moment-material/groups")
async def moment_material_groups(plan_name: str = ""):
    if not plan_name:
        return ok([])
    try:
        from src.utils.moment_material import MomentMaterialManager
        manager = MomentMaterialManager()
        res_data = manager.list_groups(plan_name)
        return ok(res_data)
    except Exception as e:
        logger.error(f"获取发圈组失败: {e}")
        return ok([])

@router.post("/api/moment-material/open-folder")
async def moment_material_open_folder(request: Request):
    try:
        body = await request.json()
        folder_path = body.get("path", "")
        from src.utils.moment_material import MomentMaterialManager
        manager = MomentMaterialManager()
        manager.open_folder(folder_path)
        return ok_msg("操作成功")
    except Exception as e:
        logger.error(f"打开素材目录异常: {e}")
        return err(40000, "操作失败", {"error": str(e)})

@router.get("/api/moment-material/select-folder")
async def moment_material_select_folder():
    from src.utils.moment_material import MomentMaterialManager
    manager = MomentMaterialManager()
    return ok({"path": manager.select_folder()})
