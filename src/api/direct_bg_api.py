"""
底图直发排期 API — 底图原图直出，文案作为朋友圈正文

路由：
  POST /api/moment/direct-bg-schedule
"""
import os
import logging
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Request

from src.utils.response import ok, err
from src.crm.moment_planner_service.text_image_composer import calculate_publish_times

from .compose_utils import ensure_local_image_path, upload_file_to_oss

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/moment/direct-bg-schedule")
async def direct_bg_schedule(request: Request):
    """
    纯底图直发排期 — 底图原图直出，文案作为朋友圈正文。

    底图不做任何合成处理，上传到 OSS 后直接作为 media_urls 存入排期。
    文案作为 content_text（朋友圈正文）。多条文案生成多条排期，共用同一张底图。

    请求体：
    {
        "background_image": "https://oss.../image.png",
        "texts": ["文案1", "文案2"],
        "target_date": "2026-06-02",
        "deadline_time": "18:00",
        "sync_to_date": "",       // 同步复制截止日期（可选，格式 YYYY-MM-DD）
        "industry_id": ""
    }
    """
    try:
        data = await request.json()
        background_image = data.get("background_image", "")
        texts = data.get("texts", [])
        target_date = data.get("target_date", "")
        deadline_time = data.get("deadline_time", "18:00")
        sync_to_date = data.get("sync_to_date", "")
        industry_id = data.get("industry_id", "")
        compose_settings = data.get("compose_settings")

        # ── 参数校验 ──
        if not background_image:
            return err(40000, "底图路径不能为空")
        if not texts or not isinstance(texts, list):
            return err(40000, "文案列表不能为空")
        if not target_date:
            return err(40000, "目标日期不能为空")

        # ── 1. 确保底图可访问，并获取最终 URL ──
        if background_image.startswith("http") or background_image.startswith("/"):
            # 已是公网 URL 或 /api/... 相对路径的 OSS 重定向链接，直接使用
            final_bg_url = background_image
        else:
            try:
                local_path = ensure_local_image_path(background_image)
            except Exception as e:
                return err(40000, f"底图处理失败：{str(e)}")
            if not os.path.exists(local_path):
                return err(40400, f"底图文件不存在：{local_path}")
            oss_url = upload_file_to_oss(local_path)
            final_bg_url = oss_url if oss_url else background_image

        # ── 2. 计算发布时间（当天） ──
        publish_times = calculate_publish_times(
            target_date=target_date,
            deadline_time=deadline_time,
            count=len(texts),
        )

        # ── 3. 批量创建排期 ──
        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import MomentPlannerService
        import src.crm.moment_planner_service as mps
        from uuid import uuid4

        account_id = get_active_account()
        planner = MomentPlannerService(account_id)
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"

        created_schedules: list[dict] = []
        with mps._schedule_lock:
            for idx, (text, pub_time) in enumerate(zip(texts, publish_times)):
                new_id = mps._next_id
                mps._next_id += 1
                schedule = {
                    "id": new_id,
                    "bot_wxid": account_id or "default",
                    "scheduled_time": pub_time,
                    "content_text": text,          # 文案作为正文
                    "media_urls": [final_bg_url],  # 底图原图直接作为媒体
                    "media_type": "image",
                    "status": "pending",
                    "industry_tag": industry_id or "底图直发",
                    "created_at": datetime.now().isoformat(),
                    "source": "direct_bg",
                    "compose_settings": compose_settings,
                    "compose_batch_id": batch_id,
                    "split_index": idx,
                }
                mps._schedules.append(schedule)
                created_schedules.append(schedule)

        # ── 4. 同步复制到其它日期段（可选） ──
        synced_schedules = _sync_to_extra_days(
            texts=texts,
            final_bg_url=final_bg_url,
            target_date=target_date,
            sync_to_date=sync_to_date,
            deadline_time=deadline_time,
            industry_id=industry_id,
            mps=mps,
            compose_settings=compose_settings,
            batch_id=batch_id,
        )

        all_created = created_schedules + synced_schedules
        total = len(all_created)

        # ── 5. 同步到同步后端 ──
        try:
            planner._sync_schedules_to_cloud()
            logger.info(f"[底图直发] 已同步 {total} 条排期到同步后端")
        except Exception as e:
            logger.warning(f"[底图直发] 同步服务失败（本地已保存）：{e}")

        return ok({
            "message": f"成功创建 {total} 条排期（底图直发）",
            "count": total,
            "schedules": all_created,
        })

    except Exception as e:
        logger.error(f"[底图直发] 异常：{e}")
        return err(40000, f"底图直发排期创建失败：{str(e)}")


def _sync_to_extra_days(
    texts: list[str],
    final_bg_url: str,
    target_date: str,
    sync_to_date: str,
    deadline_time: str,
    industry_id: str,
    mps,
    compose_settings=None,
    batch_id: str = None,
) -> list[dict]:
    """将同一批排期同步复制到后续日期段，返回新增的排期列表。"""
    if not sync_to_date or sync_to_date == target_date:
        return []
    synced_schedules = []
    try:
        d1 = date.fromisoformat(target_date)
        d2 = date.fromisoformat(sync_to_date)
        if d2 <= d1:
            return []
        day_diff = (d2 - d1).days
        for delta in range(1, day_diff + 1):
            extra_date = (d1 + timedelta(days=delta)).isoformat()
            extra_times = calculate_publish_times(
                target_date=extra_date,
                deadline_time=deadline_time,
                count=len(texts),
            )
            with mps._schedule_lock:
                for idx, (text, pub_time) in enumerate(zip(texts, extra_times)):
                    new_id = mps._next_id
                    mps._next_id += 1
                    from src.crm.account_data import get_active_account
                    account_id = get_active_account() or "default"
                    s = {
                        "id": new_id,
                        "bot_wxid": account_id,
                        "scheduled_time": pub_time,
                        "content_text": text,
                        "media_urls": [final_bg_url],
                        "media_type": "image",
                        "status": "pending",
                        "industry_tag": industry_id or "底图直发",
                        "created_at": datetime.now().isoformat(),
                        "source": "direct_bg",
                        "compose_settings": compose_settings,
                        "compose_batch_id": batch_id,
                        "split_index": idx,
                    }
                    mps._schedules.append(s)
                    synced_schedules.append(s)
    except Exception as e:
        logger.warning(f"[底图直发] 同步复制异常（本日排期已创建）：{e}")
    return synced_schedules
