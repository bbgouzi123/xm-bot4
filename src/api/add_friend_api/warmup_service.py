import os
import json
import random
import asyncio
import logging
import datetime
import urllib.request
import tempfile
import uuid
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

def _resolve_media_paths(urls):
    resolved = []
    import requests
    for url in urls:
        if not url:
            continue
        if url.startswith('/api/file/download/'):
            file_id = url.split('/')[-1]
            from src.api.file_api import UPLOAD_DIR
            local_p = UPLOAD_DIR / file_id
            if local_p.exists():
                resolved.append(str(local_p))
        elif url.startswith('http://') or url.startswith('https://'):
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code == 200:
                    ext = os.path.splitext(urlparse(url).path)[1]
                    if not ext:
                        ext = '.png'
                    tmp_p = os.path.join(tempfile.gettempdir(), f"warmup_img_{uuid.uuid4().hex}{ext}")
                    with open(tmp_p, 'wb') as f:
                        f.write(resp.content)
                    resolved.append(tmp_p)
            except Exception as e:
                logger.error(f"[一键托管] 下载排期图片失败 {url}: {e}")
        else:
            if os.path.exists(url):
                resolved.append(url)
    return resolved


async def perform_warmup_actions(driver, task_state):
    """一键托管：执行朋友圈新消息消红点、流式刷圈去重与AI互动、日历排期发圈与行业AI发圈兜底等养号组合拳"""
    if not driver:
        return

    from src.utils.websocket_manager import ws_manager

    _task_id = "warmup_main"

    async def _broadcast(step: str, message: str, progress: int = 0, total: int = 100, status: str = "running"):
        try:
            await ws_manager.broadcast_task_update(
                task_id=_task_id,
                task_type="智能养号",
                status=status,
                progress=progress,
                total=total,
                message=message,
                step=step,
            )
        except Exception:
            pass

    logger.info("[一键托管] 🌟 启动防封养号动作组合拳...")
    await _broadcast("start", "启动防封养号动作组合拳...", 0)

    # 1. 消除朋友圈新消息红点并曝光互动 (已被用户要求废弃移除)
    logger.info("[一键托管] 朋友圈消红点功能已跳过")
    await _broadcast("clear_dot", "朋友圈消息红点已处理完毕 (跳过)", 25)
    await asyncio.sleep(0.5)

    # 2. 刷朋友圈、流式已读特征游标去重并概率性点赞/评论 (已被用户要求废弃移除)
    logger.info("[一键托管] 朋友圈流式浏览互动已跳过")
    await _broadcast("browse_moments", "朋友圈浏览互动完毕 (跳过)", 55)
    await asyncio.sleep(0.5)

    # 3. 朋友圈发表动作 (优先发送今日到期排期，无日程则 20% 概率触发行业AI发圈，每天上限 1 条)
    today_str = datetime.date.today().isoformat()
    if task_state.get("config", {}).get("active_warmup") and (task_state.get("last_warmup_post_date") != today_str):
        has_posted = False

        # 3a. 优先检测是否存在到期待发的日历排期日程
        try:
            from src.crm.moment_planner_service import MomentPlannerService
            from src.crm.account_data import get_active_account
            import src.crm.moment_planner_service.state as mps_state
            from src.crm.moment_planner_service.bootstrap import expire_stale_pending_moments_and_collect_due

            account_id = get_active_account() or 'main'
            planner = MomentPlannerService(account_id)

            stale_count, pending_tasks = expire_stale_pending_moments_and_collect_due()
            if stale_count:
                planner._sync_schedules_to_cloud()

            if pending_tasks:
                task = pending_tasks[0]
                task_id = task["id"]
                logger.info(f"[一键托管] 📍 发现今日有到期未发表的排期日程 #{task_id}，正在执行自动发表...")
                await _broadcast("post_moment", f"发现日历排期日程 #{task_id}，正在自动发圈...", 65)

                text = task["content_text"]
                media_raw = task.get("media_urls", [])
                if isinstance(media_raw, str):
                    try:
                        media_raw = json.loads(media_raw)
                    except Exception:
                        media_raw = []

                local_paths = _resolve_media_paths(media_raw) if media_raw else None

                success = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: driver.post_moment(text=text, image_paths=local_paths)
                )

                new_status = "published" if success else "failed"
                with mps_state._schedule_lock:
                    for s in mps_state._schedules:
                        if s["id"] == task_id:
                            s["status"] = new_status
                            if not success:
                                s["error_msg"] = "UIA 发表失败"
                            break
                planner._sync_schedules_to_cloud()

                if success:
                    has_posted = True
                    task_state["last_warmup_post_date"] = today_str
                    logger.info(f"[一键托管] ✅ 排期日程 #{task_id} 发表成功")
                    await _broadcast("post_moment", f"日历排期日程 #{task_id} 发圈成功！", 90)
                else:
                    logger.error(f"[一键托管] ❌ 排期日程 #{task_id} 发表失败")
                    await _broadcast("post_moment", f"排期日程 #{task_id} 发圈失败", 90)
        except Exception as e:
            logger.error(f"[一键托管] 处理日历日程发圈异常: {e}")

        # 3b. 若无日程排期，以 20% 概率触发 AI 动态文案生成发圈 (契合行业属性)
        if not has_posted and random.random() < 0.2:
            logger.info("[一键托管] 🎲 触发每日随机朋友圈发表 (AI 兜底模式)...")
            await _broadcast("ai_post", "概率触发每日 AI 文案发圈...", 65)
            text = None

            try:
                from src.api.config_api import _ai_service
                from src.crm.industry_config import IndustryConfigManager
                from src.api.instance_settings_api import load_instance_settings

                wxid = getattr(driver, "wxid", None) or "main"
                icm = IndustryConfigManager(account_id=wxid)
                active_profile = None

                # 优先读取微信号专属绑定的行业，保障多开实例隔离
                try:
                    cfg = load_instance_settings(wxid)
                    profile_id = cfg.get("industry_profile_id")
                    if profile_id:
                        active_profile = icm.get_profile_by_id(profile_id)
                except Exception:
                    pass

                if not active_profile:
                    active_profile = icm.get_active_profile()

                industry_tag = active_profile.name if active_profile else "通用营销"

                if _ai_service and _ai_service.is_configured():
                    product_hint = f"产品/服务：{active_profile.product}" if active_profile and active_profile.product else ""
                    selling_hint = f"核心卖点：{active_profile.selling_point}" if active_profile and active_profile.selling_point else ""
                    await _broadcast("ai_post", f"AI 正在为【{industry_tag}】行业生成朋友圈文案...", 75)

                    prompt = f"""你是一个专业的朋友圈文案策划师。请为【{industry_tag}】行业，根据以下设定生成一条高质量 of 微信朋友圈文案。
                    {product_hint}
                    {selling_hint}
                    口吻调性：亲切、专业。风格要求：真实有趣。长度控制在 50~150 字之间，适当使用 1-3 个 emoji 点缀。
                    只返回朋友圈文案内容本身，绝对不要包含任何"好的，这是为您生成的文案："或 markdown 代码块标记，不要包含其他解释性语言。
                    """

                    response = await _ai_service.start_chat(agent_id="", message=prompt, cache_session=False)
                    if response.get("success") and response.get("content"):
                        text = response.get("content").strip()
                        logger.info(f"[一键托管] 🌟 AI 为行业【{industry_tag}】成功生成了发圈文案")
            except Exception as e:
                logger.warning(f"[一键托管] 尝试 AI 生成朋友圈文案失败: {e}")

            if not text:
                text_pool = [
                    "生活明朗，万物可爱。每一天都要充满斗志！💪",
                    "努力提升自己，你想要的时间都会给你。☀️",
                    "专注和坚持是做好一切业务的不二法门，加油！🔥",
                    "又是能量满满的一天，祝各位朋友今天业务顺利，财源滚滚！💼",
                    "人生的每一步都是积累，不用着急，慢慢来，只要方向正确总会到达。☘️"
                ]
                text = random.choice(text_pool)
                logger.info("[一键托管] 回退到静态文案池发圈")

            try:
                await _broadcast("ai_post", "正在发表朋友圈...", 85)
                success = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: driver.post_moment(text=text)
                )
                if success:
                    task_state["last_warmup_post_date"] = today_str
                    logger.info("[一键托管] 静态朋友圈发表成功")
                    await _broadcast("ai_post", "朋友圈发表成功！", 90)
                else:
                    await _broadcast("ai_post", "朋友圈发表失败", 90)
            except Exception as e:
                logger.error(f"[一键托管] 模拟发表朋友圈异常: {e}")
                await _broadcast("ai_post", f"发圈异常: {e}", 90)
    else:
        # 今日已发过朋友圈，跳过发圈步骤
        await _broadcast("skip_post", "今日已发圈或无排期，跳过发圈步骤", 90)

    # 完成
    await _broadcast("done", "本轮养号动作组合拳执行完毕，下次将在 1.5~2.5 小时后再次执行", 100, status="completed")


_warmup_idle_daemon_started = False

async def warmup_idle_daemon_loop(driver, task_state):
    """一键托管空闲养号守护协程：若开启了托管，但当前没有运行加人任务，每隔一段时间自动养号一次"""
    from src.utils.license_validator import LicenseValidator
    features = LicenseValidator.check_features()
    if not features.get("active_warmup", False):
        logger.debug("[一键托管守护] 当前 License 不支持一键智能养号功能，托管守护不执行。")
        return

    import os
    logger.info("[一键托管守护] 空闲养号监控守护协程已启动...")
    while True:
        try:
            active_warmup = task_state.get("config", {}).get("active_warmup", False)
            running_add_friend = task_state.get("running", False)
            if active_warmup and not running_add_friend and driver and driver.is_connected():
                logger.info("[一键托管守护] ⏰ 空闲养号时间到！检测到微信在线且加人任务空闲，开始执行养号组合拳...")
                await perform_warmup_actions(driver, task_state)

            interval = random.randint(5400, 9000)
            if os.getenv("XM_ENV") == "dev":
                interval = random.randint(30, 60)
            await asyncio.sleep(interval)
        except Exception as e:
            logger.error(f"[一键托管守护] 空闲养号主循环异常: {e}")
            await asyncio.sleep(60)

def ensure_warmup_idle_daemon_started(driver, task_state):
    global _warmup_idle_daemon_started
    if not _warmup_idle_daemon_started:
        import app.state as app_state
        main_loop = getattr(app_state, 'main_loop', None)
        if main_loop and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(warmup_idle_daemon_loop(driver, task_state), main_loop)
            _warmup_idle_daemon_started = True
        else:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(warmup_idle_daemon_loop(driver, task_state))
                _warmup_idle_daemon_started = True
            except RuntimeError:
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(warmup_idle_daemon_loop(driver, task_state))
                    _warmup_idle_daemon_started = True
                except Exception as e:
                    logger.error(f"启动一键托管空闲守护失败 fallback: {e}")


