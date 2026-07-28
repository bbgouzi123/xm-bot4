"""
朋友圈 API 路由（核心模块）

POST /api/moment/post           — 发布朋友圈
GET  /api/moment/schedules      — 查询排期
POST /api/moment/schedules      — 保存排期
DELETE /api/moment/schedules/{id} — 删除排期
POST /api/moment/toggle-auto-comment  — 开关互动巡游
GET  /api/moment/auto-comment/status  — 互动状态
POST /api/moment/auto-comment/resume  — 恢复巡游
POST /api/moment/auto-comment/pause   — 暂停巡游
GET  /api/moment/interaction-logs     — 互动日志
GET  /api/moment/settings             — 互动配置
POST /api/moment/settings             — 保存互动配置

AI 生成路由 → moment_generate_api.py
截图管理路由 → moment_screenshot_api.py
"""
from fastapi import APIRouter, Request
import json
import logging

# 【xm-bot4防封体系】
from src.utils.rest_time import is_rest_time
from src.utils.daily_counter import DailyCounter
from src.utils.response import ok, err, ok_msg

logger = logging.getLogger(__name__)
router = APIRouter()

_driver = None
_moment_counter = DailyCounter()


from .compose_utils import ensure_absolute_oss_url


def init(driver):
    global _driver
    _driver = driver


@router.post("/api/moment/post")
async def post_moment(request: Request):
    """发布朋友圈"""
    if not _driver or not _driver.is_connected():
        return err(40000, "操作失败", {"message": "微信未连接"})

    from src.utils.license_validator import LicenseValidator
    features = LicenseValidator.check_features()
    if not features.get("moments_auto", False):
        return err(40301, "操作失败", {"message": "当前版本不支持朋友圈发布功能，请升级套餐"})

    # 【防封守卫 1】休息时间检查 — 深夜不发圈
    from src.crm.account_data import get_active_account
    account_id = get_active_account() or 'main'
    if is_rest_time("moment_post", account_id, verbose=True):
        return err(40000, "操作失败", {"message": "当前在休息时间内，不建议发布朋友圈"})

    # 【防封守卫 2】日计数器 — 每天最多发5条
    if not _moment_counter.can_do("moment_post", account_id):
        remaining = _moment_counter.get_remaining("moment_post", account_id)
        return err(40000, "操作失败", {"message": f"今日发圈已达上限，剩余{remaining}条配额"})

    try:
        body = await request.json()
        schedule_id = body.get("schedule_id", None)
        text = body.get("text", "")
        image_paths = body.get("image_paths", None)

        if not text and not image_paths:
            return err(40000, "操作失败", {"message": "缺少 text 或图片参数"})

        # 预处理：将网络 URL 图片下载到本地临时目录
        if image_paths:
            import os, tempfile, hashlib
            import urllib.request

            temp_dir = os.path.join(tempfile.gettempdir(), "xm-bot-moment-images")
            os.makedirs(temp_dir, exist_ok=True)

            local_paths = []
            for p in image_paths:
                p = ensure_absolute_oss_url(p)
                if isinstance(p, str) and (p.startswith("http://") or p.startswith("https://")):
                    try:
                        url_hash = hashlib.md5(p.encode()).hexdigest()
                        ext = ".png"
                        for known_ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".avi", ".webm"]:
                            if known_ext in p.lower():
                                ext = known_ext
                                break
                        local_file = os.path.join(temp_dir, f"{url_hash}{ext}")
                        if not os.path.exists(local_file):
                            logger.info(f"[朋友圈] 下载远程图片: {p[:80]}...")
                            urllib.request.urlretrieve(p, local_file)
                        local_paths.append(local_file)
                    except Exception as dl_err:
                        logger.error(f"[朋友圈] 下载图片失败: {p} -> {dl_err}")
                else:
                    local_paths.append(p)
            image_paths = local_paths if local_paths else None

        # ── 状态更新与异步发布 ──
        from src.crm.moment_planner_service.state import _coerce_schedule_id
        from src.crm.moment_planner_service.bootstrap import _persist_schedules_after_mutation
        import src.crm.moment_planner_service as mps

        # 如果有排期ID，同步将其状态设为 publishing 并持久化，让前端能立刻感知到状态变化
        if schedule_id is not None:
            with mps._schedule_lock:
                coerced_sid = _coerce_schedule_id(schedule_id)
                for s in mps._schedules:
                    if _coerce_schedule_id(s.get("id")) == coerced_sid:
                        s["status"] = "publishing"
                        if "error_msg" in s:
                            del s["error_msg"]
                        break
            _persist_schedules_after_mutation()

        import asyncio
        loop = asyncio.get_event_loop()

        # 异步执行任务定义
        async def do_async_post():
            result = False
            try:
                from src.orchestrator.ui_bus import ui_bus, UICommand, UICommandKind, UICommandPriority, UICommandStatus
                cmd = UICommand(
                    wxid=account_id or "", kind=UICommandKind.PUBLISH_MOMENT,
                    payload={"text": text, "image_paths": image_paths},
                    priority=UICommandPriority.NORMAL, timeout=120.0,
                )
                ui_bus.submit(cmd)
                finished = await loop.run_in_executor(None, ui_bus.await_result, cmd.id, 150.0)
                if finished.status == UICommandStatus.SUCCESS:
                    result = bool(finished.result)
                else:
                    logger.warning(f"[朋友圈][UIBus] 执行未成功: status={finished.status.value}")
            except Exception as e:
                logger.error(f"[朋友圈][UIBus] 投递或执行异常: {e}")

            # 回写最终状态到内存和数据库
            if schedule_id is not None:
                with mps._schedule_lock:
                    coerced_sid = _coerce_schedule_id(schedule_id)
                    for s in mps._schedules:
                        if _coerce_schedule_id(s.get("id")) == coerced_sid:
                            s["status"] = "published" if result else "failed"
                            if not result:
                                s["error_msg"] = "发送失败或超时"
                            break
                _persist_schedules_after_mutation()
                logger.info(f"[朋友圈] 异步发布排期 #{schedule_id} 完成，结果为: {result}")

            if result:
                _moment_counter.increment("moment_post", account_id)
                logger.info(f"[朋友圈] 发布成功，今日已发 {_moment_counter.get_count('moment_post', account_id)} 条")
                try:
                    from src.utils.alert_notifier import alert_notifier
                    asyncio.create_task(alert_notifier.send_user_notification(
                        title="✨ 朋友圈发布成功",
                        body=f"朋友圈图文发布成功：{text[:50]}...",
                        category="moments"
                    ))
                except Exception:
                    pass
                try:
                    from src.utils.cloud_sync import get_cloud_client
                    get_cloud_client().report_moment_post(
                        content=text,
                        status="success"
                    )
                except Exception as report_err:
                    logger.error(f"[朋友圈] 上报发圈日志失败: {report_err}")
            else:
                try:
                    from src.utils.alert_notifier import alert_notifier
                    asyncio.create_task(alert_notifier.send_user_notification(
                        title="❌ 朋友圈发布失败",
                        body=f"朋友圈发布失败，请检查微信状态。文案：{text[:50]}...",
                        category="task"
                    ))
                except Exception:
                    pass

        # 使用 create_task 异步运行，不阻塞 HTTP 响应
        asyncio.create_task(do_async_post())

        return ok({"message": "朋友圈发布指令已提交，正在后台发送", "status": "publishing"})
    except Exception as e:
        logger.error(f"发布朋友圈异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


# ═════════════════════ 子模块路由挂载 ═════════════════════
from .moment_schedule_api import router as schedule_router
from .moment_plan_group_api import router as plan_group_router

router.include_router(schedule_router)
router.include_router(plan_group_router)


