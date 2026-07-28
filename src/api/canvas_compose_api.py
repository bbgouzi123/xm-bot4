"""
画布合成排期 API — 接收前端 Canvas 导出的已合成图片并创建排期

路由：
  POST /api/moment/upload-composed  接收前端已合成图片并创建排期
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Request

from src.utils.response import ok, err
from src.crm.moment_planner_service.text_image_composer import calculate_publish_times

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/moment/upload-composed")
async def upload_composed(request: Request):
    """
    接收前端画布导出的已合成图片并创建排期。

    请求体：
    {
        "composed_images": ["/api/file/download/xxx", ...],
        "texts": ["文案1", "文案2"],
        "target_date": "2026-06-02",
        "deadline_time": "18:00",
        "publish_mode": "image_only",
        "industry_id": "optional"
    }
    """
    try:
        data = await request.json()
        composed_images = data.get("composed_images", [])
        texts = data.get("texts", [])
        target_date = data.get("target_date", "")
        sync_to_date = data.get("sync_to_date", "")
        deadline_time = data.get("deadline_time", "18:00")
        publish_mode = data.get("publish_mode", "image_only")
        industry_id = data.get("industry_id", "")
        compose_settings = data.get("compose_settings")

        if not composed_images or not isinstance(composed_images, list):
            return err(40000, "合成图片列表不能为空")
        if not target_date:
            return err(40000, "目标日期不能为空")

        # 计算日期列表（支持同步复制到多天）
        date_list = [target_date]
        if sync_to_date and sync_to_date != target_date:
            try:
                # 兼容 ISO 格式，且只截取日期部分
                clean_target = target_date.split("T")[0].split(" ")[0]
                clean_sync = sync_to_date.split("T")[0].split(" ")[0]
                d1 = datetime.strptime(clean_target, "%Y-%m-%d")
                d2 = datetime.strptime(clean_sync, "%Y-%m-%d")
                
                start_d = min(d1, d2)
                end_d = max(d1, d2)
                
                from datetime import timedelta
                curr = start_d
                temp_list = []
                while curr <= end_d:
                    temp_list.append(curr.strftime("%Y-%m-%d"))
                    curr += timedelta(days=1)
                if temp_list:
                    date_list = temp_list
            except Exception as e:
                logger.error(f"[画布合成] 解析同步复制日期范围异常：{e}")

        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import MomentPlannerService
        import src.crm.moment_planner_service as mps

        account_id = get_active_account()
        planner = MomentPlannerService(account_id)
        from uuid import uuid4
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"

        created_schedules = []
        with mps._schedule_lock:
            for date in date_list:
                publish_times = calculate_publish_times(
                    target_date=date,
                    deadline_time=deadline_time,
                    count=len(composed_images),
                )
                for idx, (img_url, pub_time) in enumerate(
                    zip(composed_images, publish_times)
                ):
                    new_id = mps._next_id
                    mps._next_id += 1
                    content_text = ""
                    if publish_mode == "image_and_text" and idx < len(texts):
                        content_text = texts[idx]
                    schedule = {
                        "id": new_id,
                        "bot_wxid": account_id or "default",
                        "scheduled_time": pub_time,
                        "content_text": content_text,
                        "media_urls": [img_url],
                        "status": "pending",
                        "industry_tag": industry_id or "画布合成",
                        "created_at": datetime.now().isoformat(),
                        "source": "manual_compose",
                        "compose_batch_id": batch_id,
                        "compose_settings": compose_settings,
                        "split_index": idx,
                    }
                    mps._schedules.append(schedule)
                    created_schedules.append(schedule)

        try:
            planner._sync_schedules_to_cloud()
            logger.info(f"[画布合成] 已同步 {len(created_schedules)} 条排期到同步后端")
        except Exception as e:
            logger.warning(f"[画布合成] 同步服务失败（本地已保存）：{e}")

        return ok({
            "message": f"成功创建 {len(created_schedules)} 条排期",
            "count": len(created_schedules),
            "schedules": created_schedules,
        })

    except Exception as e:
        logger.error(f"[画布合成] 异常：{e}")
        return err(40000, f"画布合成排期失败：{str(e)}")


@router.post("/api/moment/compose-config/save")
async def save_compose_config(request: Request):
    """保存画布一切操控区域的草稿数据到本地及同步后端缓存"""
    try:
        data = await request.json()
        from src.utils.config_cache import config_cache
        config_cache.set("moment_compose_draft", data)
        return ok({"message": "保存画布配置成功"})
    except Exception as e:
        return err(40000, f"保存画布配置失败：{str(e)}")

@router.get("/api/moment/compose-config/get")
async def get_compose_config():
    """读取最近一次保存的画布草稿配置"""
    try:
        from src.utils.config_cache import config_cache
        data = config_cache.get("moment_compose_draft")
        if not data:
            config_cache.load_from_cloud(clear_before_load=False)
            data = config_cache.get("moment_compose_draft")
        return ok(data or {})
    except Exception as e:
        return err(40000, f"获取画布配置失败：{str(e)}")

