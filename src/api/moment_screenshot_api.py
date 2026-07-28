"""
朋友圈产品截图管理 API 路由
GET    /api/moment/screenshot/status  — 获取产品截图配置状态
POST   /api/moment/screenshot/upload  — 上传/更新产品截图
DELETE /api/moment/screenshot/delete  — 删除产品截图
GET    /api/moment/screenshot/image   — 直接返回截图文件流
"""
import os
import logging
from pathlib import Path
from fastapi import APIRouter, UploadFile, File

from src.utils.response import ok, err

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_screenshot_path(industry_id: str = "") -> Path:
    current_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    # product_screenshot.png 图片只是“xm-bot4系统” (sys_001) 行业才能显示才能用
    if industry_id == "sys_001":
        path_sys001 = current_dir.parent.parent / "assets" / "product_screenshot_sys_001.png"
        if path_sys001.exists():
            return path_sys001
        return current_dir.parent.parent / "assets" / "product_screenshot.png"
    
    # 其它行业必须使用自己的专属截图，没有则不使用默认图
    if industry_id:
        return current_dir.parent.parent / "assets" / f"product_screenshot_{industry_id}.png"
        
    return current_dir.parent.parent / "assets" / "non_existing_placeholder.png"


def _get_upload_screenshot_path(industry_id: str = "") -> Path:
    current_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    target_id = industry_id or "sys_001"
    return current_dir.parent.parent / "assets" / f"product_screenshot_{target_id}.png"


@router.get("/api/moment/screenshot/status")
async def get_screenshot_status(industry_id: str = ""):
    """获取产品截图配置状态"""
    try:
        screenshot_path = _get_screenshot_path(industry_id)
        exists = screenshot_path.exists()

        info = {
            "exists": exists,
            "filename": f"product_screenshot_{industry_id}.png" if (exists and industry_id != "sys_001" and industry_id) else ("product_screenshot.png" if exists else None),
            "url": f"/api/moment/screenshot/image?industry_id={industry_id}" if exists else None,
            "size": os.path.getsize(screenshot_path) if exists else 0,
        }
        return ok(info)
    except Exception as e:
        logger.error(f"获取截图状态异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


@router.post("/api/moment/screenshot/upload")
async def upload_screenshot(industry_id: str = "", file: UploadFile = File(...)):
    """上传/更新产品截图"""
    try:
        if not file.content_type.startswith("image/"):
            return err(40000, "操作失败", {"message": "只支持上传图片格式文件"})

        screenshot_path = _get_upload_screenshot_path(industry_id)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)

        with open(screenshot_path, "wb") as f:
            content = await file.read()
            f.write(content)

        logger.info(f"[朋友圈] 用户通过界面更新了产品截图 ({industry_id}): {screenshot_path}")
        return ok({"message": "上传成功", "url": f"/api/moment/screenshot/image?industry_id={industry_id}"})
    except Exception as e:
        logger.error(f"上传产品截图异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


@router.delete("/api/moment/screenshot/delete")
async def delete_screenshot(industry_id: str = ""):
    """删除产品截图"""
    try:
        screenshot_path = _get_upload_screenshot_path(industry_id)
        if screenshot_path.exists():
            screenshot_path.unlink()
            logger.info(f"[朋友圈] 用户删除了产品截图 ({industry_id})")
            return ok({"message": "删除成功"})
        else:
            if not industry_id or industry_id == "sys_001":
                p_sys001 = screenshot_path.parent / "product_screenshot_sys_001.png"
                if p_sys001.exists():
                    p_sys001.unlink()
                    logger.info("[朋友圈] 用户删除了产品截图 (sys_001)")
                    return ok({"message": "删除成功"})
            return err(40000, "操作失败", {"message": "截图文件不存在，无需删除"})
    except Exception as e:
        logger.error(f"删除产品截图异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


@router.get("/api/moment/screenshot/image")
async def get_screenshot_image(industry_id: str = ""):
    """直接返回截图文件流 (供前端 UI 回显预览)"""
    from fastapi.responses import FileResponse
    screenshot_path = _get_screenshot_path(industry_id)
    if not screenshot_path.exists():
        return err(40400, "截图不存在")
    return FileResponse(str(screenshot_path), media_type="image/png")
