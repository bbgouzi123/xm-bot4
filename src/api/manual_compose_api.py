"""
手动合成排期 API — 将文案渲染到底图上并创建排期

路由：
  POST /api/moment/manual-compose         批量合成图文并创建排期
  POST /api/moment/compose-preview        合成单张预览图
  POST /api/moment/compose-preview-batch  批量合成预览图
  POST /api/moment/split-text             拆分文案文本
"""
import os
import math
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request

from src.utils.response import ok, err
from src.crm.moment_planner_service.text_image_composer import (
    compose_text_on_image,
    calculate_publish_times,
    split_copy_text,
)

logger = logging.getLogger(__name__)
router = APIRouter()


from .compose_utils import ensure_local_image_path, upload_file_to_oss, _parse_style


@router.post("/api/moment/manual-compose")
async def manual_compose(request: Request):
    """
    批量合成图文并创建排期。

    接收底图 + 多条文案，逐条合成图片，计算发布时间，
    批量写入排期列表并同步到同步后端。

    请求体：
    {
        "background_image": "/path/to/image.png",
        "texts": ["文案1", "文案2", "文案3"],
        "target_date": "2026-06-02",
        "deadline_time": "18:00",
        "publish_mode": "image_only",   // or "image_and_text"
        "industry_id": "optional",
        "use_precomposed": false,        // 是否使用已合成图片
        "composed_images": [],           // 已合成图片路径列表
        "style": { "font_size": 36, "font_color": "#FFFFFF", "overlay_opacity": 63, "position": "bottom" }
    }
    """
    try:
        data = await request.json()
        background_image = data.get("background_image", "")
        texts = data.get("texts", [])
        target_date = data.get("target_date", "")
        deadline_time = data.get("deadline_time", "18:00")
        publish_mode = data.get("publish_mode", "image_only")
        industry_id = data.get("industry_id", "")
        use_precomposed = data.get("use_precomposed", False)
        composed_images_input = data.get("composed_images", [])

        # ── 参数校验 ──
        if not background_image:
            return err(40000, "底图路径不能为空")
        try:
            background_image = ensure_local_image_path(background_image)
        except Exception as e:
            return err(40000, f"底图下载失败：{str(e)}")
        if not os.path.exists(background_image):
            return err(40400, f"底图文件不存在：{background_image}")
        if not texts or not isinstance(texts, list):
            return err(40000, "文案列表不能为空")
        if not target_date:
            return err(40000, "目标日期不能为空")

        # ── 1. 获取合成图片 ──
        composed_images: list[str] = []

        if use_precomposed and composed_images_input:
            # 使用已合成的预览图片（两步流程第二步）
            for img_path in composed_images_input:
                if os.path.exists(img_path):
                    composed_images.append(img_path)
                else:
                    logger.warning(f"[手动合成] 预合成图片不存在：{img_path}")
                    return err(40400, f"预合成图片不存在：{img_path}")
            logger.info(f"[手动合成] 使用 {len(composed_images)} 张预合成图片")
        else:
            # 逐条合成图片
            style_kwargs = _parse_style(data)
            for idx, text in enumerate(texts):
                try:
                    img_path = compose_text_on_image(
                        background_path=background_image,
                        text=text,
                        **style_kwargs,
                    )
                    composed_images.append(img_path)
                    logger.info(f"[手动合成] 第 {idx + 1}/{len(texts)} 条合成成功")
                except Exception as e:
                    logger.error(f"[手动合成] 第 {idx + 1} 条合成失败：{e}")
                    return err(40000, f"第 {idx + 1} 条文案合成失败：{str(e)}")

        # ── 2. 计算发布时间 ──
        publish_times = calculate_publish_times(
            target_date=target_date,
            deadline_time=deadline_time,
            count=len(texts),
        )

        # ── 3. 批量创建排期记录 ──
        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import MomentPlannerService
        import src.crm.moment_planner_service as mps

        account_id = get_active_account()
        planner = MomentPlannerService(account_id)

        created_schedules: list[dict] = []

        with mps._schedule_lock:
            for idx, (text, img_path, pub_time) in enumerate(
                zip(texts, composed_images, publish_times)
            ):
                new_id = mps._next_id
                mps._next_id += 1

                # 自动将合成物理图片上传至同步后端 OSS
                oss_url = upload_file_to_oss(img_path)
                final_media_url = oss_url if oss_url else img_path

                # 根据 publish_mode 决定内容
                if publish_mode == "image_and_text":
                    content_text = text
                    media_urls = [final_media_url]
                else:
                    # image_only：文案已合成到图片中，文本留空
                    content_text = ""
                    media_urls = [final_media_url]

                schedule = {
                    "id": new_id,
                    "bot_wxid": account_id or "default",
                    "scheduled_time": pub_time,
                    "content_text": content_text,
                    "media_urls": media_urls,
                    "status": "pending",
                    "industry_tag": industry_id or "手动合成",
                    "created_at": datetime.now().isoformat(),
                    "source": "manual_compose",
                }
                mps._schedules.append(schedule)
                created_schedules.append(schedule)

        # ── 4. 同步到同步后端 ──
        try:
            planner._sync_schedules_to_cloud()
            logger.info(f"[手动合成] 已同步 {len(created_schedules)} 条排期到同步后端")
        except Exception as e:
            logger.warning(f"[手动合成] 同步服务失败（本地已保存）：{e}")

        return ok({
            "message": f"成功创建 {len(created_schedules)} 条排期",
            "count": len(created_schedules),
            "schedules": created_schedules,
        })

    except Exception as e:
        logger.error(f"[手动合成] 异常：{e}")
        return err(40000, f"手动合成失败：{str(e)}")


@router.post("/api/moment/compose-preview")
async def compose_preview(request: Request):
    """
    合成单张预览图并返回图片路径。

    请求体：
    {
        "background_image": "/path/to/image.png",
        "text": "单条文案",
        "style": { "font_size": 36, "font_color": "#FFFFFF", "overlay_opacity": 63, "position": "bottom" }
    }
    """
    try:
        data = await request.json()
        background_image = data.get("background_image", "")
        text = data.get("text", "")

        if not background_image:
            return err(40000, "底图路径不能为空")
        try:
            background_image = ensure_local_image_path(background_image)
        except Exception as e:
            return err(40000, f"底图下载失败：{str(e)}")
        if not os.path.exists(background_image):
            return err(40400, f"底图文件不存在：{background_image}")
        if not text:
            return err(40000, "文案内容不能为空")

        style_kwargs = _parse_style(data)

        img_path = compose_text_on_image(
            background_path=background_image,
            text=text,
            **style_kwargs,
        )

        return ok({
            "image_path": img_path,
            "image_url": f"/api/file/download/{os.path.basename(img_path)}",
        })

    except Exception as e:
        logger.error(f"[预览合成] 异常：{e}")
        return err(40000, f"预览合成失败：{str(e)}")


@router.post("/api/moment/compose-preview-batch")
async def compose_preview_batch(request: Request):
    """
    批量合成预览图并返回图片路径列表。

    请求体：
    {
        "background_image": "/path/to/image.png",
        "texts": ["文案1", "文案2", "文案3"],
        "style": { "font_size": 36, "font_color": "#FFFFFF", "overlay_opacity": 63, "position": "bottom" }
    }
    """
    try:
        data = await request.json()
        background_image = data.get("background_image", "")
        texts = data.get("texts", [])

        if not background_image:
            return err(40000, "底图路径不能为空")
        try:
            background_image = ensure_local_image_path(background_image)
        except Exception as e:
            return err(40000, f"底图下载失败：{str(e)}")
        if not os.path.exists(background_image):
            return err(40400, f"底图文件不存在：{background_image}")
        if not texts or not isinstance(texts, list):
            return err(40000, "文案列表不能为空")

        style_kwargs = _parse_style(data)

        previews: list[dict] = []
        for idx, text in enumerate(texts):
            try:
                img_path = compose_text_on_image(
                    background_path=background_image,
                    text=text,
                    **style_kwargs,
                )
                previews.append({
                    "image_path": img_path,
                    "image_url": f"/api/file/download/{os.path.basename(img_path)}",
                    "text": text,
                    "index": idx,
                })
                logger.info(f"[批量预览] 第 {idx + 1}/{len(texts)} 条合成成功")
            except Exception as e:
                logger.error(f"[批量预览] 第 {idx + 1} 条合成失败：{e}")
                return err(40000, f"第 {idx + 1} 条文案预览合成失败：{str(e)}")

        return ok({
            "previews": previews,
            "count": len(previews),
        })

    except Exception as e:
        logger.error(f"[批量预览] 异常：{e}")
        return err(40000, f"批量预览合成失败：{str(e)}")


@router.post("/api/moment/split-text")
async def split_text(request: Request):
    """
    拆分文案文本并返回结果列表。

    请求体：
    {
        "text": "原始文案",
        "mode": "newline",
        "custom_delimiter": ""
    }
    """
    try:
        data = await request.json()
        text = data.get("text", "")
        mode = data.get("mode", "newline")
        custom_delimiter = data.get("custom_delimiter", "")

        if not text:
            return err(40000, "文案内容不能为空")

        result = split_copy_text(
            text=text,
            mode=mode,
            custom_delimiter=custom_delimiter,
        )

        return ok({
            "texts": result,
            "count": len(result),
        })

    except Exception as e:
        logger.error(f"[文案拆分] 异常：{e}")
        return err(40000, f"文案拆分失败：{str(e)}")

