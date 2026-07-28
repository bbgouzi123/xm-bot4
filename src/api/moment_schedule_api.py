"""
朋友圈排期日程 API 路由
"""
from fastapi import APIRouter, Request
import json
import logging
from src.utils.response import ok, err

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/moment/schedules")
async def get_schedules(start_date: str, end_date: str, industry_id: str = ""):
    try:
        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import MomentPlannerService

        account_id = get_active_account()
        planner = MomentPlannerService(account_id)
        events = planner.get_calendar_events(start_date, end_date, industry_id=industry_id)
        if not isinstance(events, list):
            events = []
        envelope = ok({"events": events})
        return json.loads(json.dumps(envelope, ensure_ascii=False, default=str))
    except Exception as e:
        logger.error(f"查询日程异常: {e}")
        return err(40000, "操作失败", {"events": []})


@router.post("/api/moment/schedules")
async def save_schedule(request: Request):
    try:
        data = await request.json()
        sid = data.get("id")
        text = data.get("content_text", "")
        media_urls = data.get("media_urls", [])
        scheduled_time = data.get("scheduled_time")
        status = data.get("status", "pending")
        industry_id = data.get("industry_id") or data.get("industry_tag", "通用营销")

        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import MomentPlannerService
        import src.crm.moment_planner_service as mps
        from src.crm.moment_planner_service.state import _coerce_schedule_id

        account_id = get_active_account()
        planner = MomentPlannerService(account_id)

        with mps._schedule_lock:
            if sid:
                coerced_sid = _coerce_schedule_id(sid)
                for s in mps._schedules:
                    if _coerce_schedule_id(s.get("id")) == coerced_sid:
                        # 智能状态重置：已发布的排期被修改了时间 → 自动重置为 pending
                        old_time = s.get("scheduled_time", "")
                        old_status = s.get("status", "")
                        if old_status == "published" and scheduled_time and str(scheduled_time) != str(old_time):
                            status = "pending"
                            logger.info(f"[日历排期] 排期 #{sid} 已发布但时间被修改，自动重置为 pending")
                            mps._executed_ids.discard(sid)

                        s["content_text"] = text
                        s["media_urls"] = media_urls
                        s["scheduled_time"] = scheduled_time
                        s["industry_tag"] = industry_id
                        s["status"] = status
                        s["bot_wxid"] = account_id or "default"
                        if status == "pending" and "error_msg" in s:
                            del s["error_msg"]
                        if "compose_settings" in data:
                            s["compose_settings"] = data["compose_settings"]
                        if "compose_batch_id" in data:
                            s["compose_batch_id"] = data["compose_batch_id"]
                        if "split_index" in data:
                            s["split_index"] = data["split_index"]
                        if "source" in data:
                            s["source"] = data["source"]
                        break
            else:
                from datetime import datetime
                new_id = mps._next_id
                mps._next_id += 1
                mps._schedules.append({
                    "id": new_id,
                    "bot_wxid": account_id or "default",
                    "scheduled_time": scheduled_time,
                    "content_text": text,
                    "media_urls": media_urls,
                    "status": status,
                    "industry_tag": industry_id,
                    "created_at": datetime.now().isoformat(),
                    "compose_settings": data.get("compose_settings"),
                    "compose_batch_id": data.get("compose_batch_id"),
                    "split_index": data.get("split_index"),
                    "source": data.get("source")
                })
        planner._sync_schedules_to_cloud()
        return ok({"message": "保存成功"})
    except Exception as e:
        logger.error(f"保存日程异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


@router.delete("/api/moment/schedules/{id}")
async def delete_schedule(id: int, delete_batch: bool = False, delete_day: bool = False):
    try:
        from src.crm.account_data import get_active_account
        import src.crm.moment_planner_service as mps
        from src.crm.moment_planner_service.state import _coerce_schedule_id, _parse_schedule_datetime
        from src.crm.moment_planner_service.bootstrap import _persist_schedules_sync

        def _get_date_ymd(scheduled_time_val) -> str:
            if not scheduled_time_val: return ""
            dt = _parse_schedule_datetime(scheduled_time_val)
            if dt: return dt.strftime("%Y-%m-%d")
            s = str(scheduled_time_val).strip()
            return s[:10] if (len(s) >= 10 and s[4] == '-' and s[7] == '-') else ""

        def _effective_source(s: dict) -> str:
            raw = s.get("source")
            if raw: return raw
            m = s.get("media_urls") or []
            ct = s.get("content_text", "")
            if (s.get("compose_settings") or s.get("compose_batch_id") or any("manual_compose_" in str(url) for url in m) or (not ct and m and any("/oss/" in str(url) for url in m))):
                return "manual_compose"
            return ""

        account_id = get_active_account()
        with mps._schedule_lock:
            target = next((s for s in mps._schedules if _coerce_schedule_id(s.get("id")) == id), None)
            if not target:
                logger.warning(f"[排期删除] 排期 #{id} 不存在于内存中")
                return ok({"message": "排期不存在"})

            ids_to_delete = {id}
            target_time = target.get("scheduled_time")
            target_text = target.get("content_text")
            target_media = target.get("media_urls")
            target_day = _get_date_ymd(target_time)

            if delete_day:
                if target_day:
                    for s in mps._schedules:
                        s_day = _get_date_ymd(s.get("scheduled_time"))
                        if s_day == target_day:
                            ids_to_delete.add(_coerce_schedule_id(s.get("id")))
                logger.info(f"[排期删除] 整天删除模式: 日期={target_day}, 匹配 {len(ids_to_delete)} 条")
            elif delete_batch:
                batch_id = target.get("compose_batch_id")
                created_at = target.get("created_at")
                source = _effective_source(target)

                if batch_id:
                    for s in mps._schedules:
                        if s.get("compose_batch_id") == batch_id:
                            ids_to_delete.add(_coerce_schedule_id(s.get("id")))
                elif source in ("manual_compose", "direct_bg") and created_at:
                    t_dt = _parse_schedule_datetime(created_at)
                    for s in mps._schedules:
                        if _effective_source(s) in ("manual_compose", "direct_bg"):
                            s_dt = _parse_schedule_datetime(s.get("created_at"))
                            if t_dt and s_dt and abs((t_dt - s_dt).total_seconds()) <= 10:
                                ids_to_delete.add(_coerce_schedule_id(s.get("id")))
                logger.info(f"[排期删除] 批量删除模式: batch_id={batch_id}, source={source}, 匹配 {len(ids_to_delete)} 条")
            else:
                # 单条删除时，清除同天且相同媒体的所有重复脏数据
                import json as _json
                def _media_key(m):
                    if isinstance(m, list):
                        try:
                            return _json.dumps(m, sort_keys=True, ensure_ascii=False)
                        except Exception:
                            return repr(m)
                    return str(m)

                target_media_key = _media_key(target_media)
                for s in mps._schedules:
                    s_day = _get_date_ymd(s.get("scheduled_time"))
                    s_media_key = _media_key(s.get("media_urls"))
                    if s_day == target_day and s_media_key == target_media_key:
                        ids_to_delete.add(_coerce_schedule_id(s.get("id")))
                logger.info(f"[排期删除] 单条删除模式: id={id}, 含脏数据去重共 {len(ids_to_delete)} 条")

            # 在删除前收集待清理 of OSS 媒体文件 URL
            media_urls_to_clean = []
            for s in mps._schedules:
                if _coerce_schedule_id(s.get("id")) in ids_to_delete:
                    urls = s.get("media_urls", [])
                    if isinstance(urls, list):
                        media_urls_to_clean.extend(urls)

            before_len = len(mps._schedules)
            mps._schedules[:] = [s for s in mps._schedules if _coerce_schedule_id(s.get("id")) not in ids_to_delete]
            deleted_count = before_len - len(mps._schedules)
            logger.info(f"[排期删除] 从内存移除 {deleted_count} 条，剩余 {len(mps._schedules)} 条")

        # 同步持久化（本地 + 同步后端带重试），确保重启后不会复活
        if deleted_count > 0:
            _persist_schedules_sync()

        # 异步清理 OSS 文件以释放服务器空间（不阻塞删除响应）
        if media_urls_to_clean:
            try:
                from src.utils.cloud_sync import get_cloud_client
                client = get_cloud_client()
                jwt_token = client.jwt_token if client else ""
                from xm_py_server import cleanup_oss_files_async
                cleanup_oss_files_async(media_urls_to_clean, jwt_token=jwt_token, product_name="xm-bot4", force=True)
            except Exception as e:
                logger.warning(f"[排期删除] OSS 文件清理启动失败: {e}")

        return ok({"message": "删除成功"})
    except Exception as e:
        logger.error(f"删除日程异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})
