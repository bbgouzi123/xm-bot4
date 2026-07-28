"""
朋友圈 AI 生成 API 路由
POST /api/moment/generate-text  — 一键生成单条朋友圈文案
POST /api/moment/generate       — 触发 AI 全托管生成未来 N 天排期（异步后台）
GET  /api/moment/generate/status — 获取后台生成任务状态
"""
import asyncio
import logging
from fastapi import APIRouter, Request

from src.utils.response import ok, err

logger = logging.getLogger(__name__)
router = APIRouter()

_is_generating = False
_generate_error = None
_tasks = {}


@router.post("/api/moment/generate-text")
async def generate_moment_text(request: Request):
    """一键根据行业和提示词生成朋友圈文案"""
    try:
        from src.api.config_api import _ai_service
        from src.crm.account_data import get_active_account

        body = await request.json()
        industry_tag = body.get("industry_tag", "")
        keywords = body.get("keywords", "")
        account_id = body.get("account_id") or get_active_account() or 'main'

        if not _ai_service or not _ai_service.is_configured():
            return err(40000, "操作失败", {"message": "AI服务未配置，请先在系统设置中配置大模型"})

        industry_profile = None
        try:
            from src.crm.industry_config import IndustryConfigManager
            from src.api.instance_settings_api import load_instance_settings
            icm = IndustryConfigManager(account_id=account_id)
            for profile in icm.get_all_profiles():
                if profile.name == industry_tag:
                    industry_profile = profile
                    break
            if not industry_profile:
                try:
                    cfg = load_instance_settings(account_id)
                    profile_id = cfg.get("industry_profile_id")
                    if profile_id:
                        industry_profile = icm.get_profile_by_id(profile_id)
                except Exception:
                    pass
            if not industry_profile:
                industry_profile = icm.get_active_profile()
        except Exception as e:
            logger.warning(f"[朋友圈文案生成] 读取行业配置失败: {e}")

        if industry_profile:
            product_hint = f"产品/服务：{industry_profile.product}" if industry_profile.product else ""
            selling_hint = f"核心卖点：{industry_profile.selling_point}" if industry_profile.selling_point else ""
            tone_hint = f"口吻调性：{industry_profile.moment_tone or '亲切、专业'}"
            style_hint = f"文案风格要求：{industry_profile.moment_style or '真实有趣'}"
            keywords_hint = f"文案中必须自然植入的关键词：{industry_profile.moment_keywords or ''}"
            forbidden_hint = f"绝对禁止使用的词汇：{industry_profile.moment_forbidden or ''}"
            hashtags_hint = f"每条文案末尾请带上标签：{industry_profile.moment_hashtags or ''}"
        else:
            product_hint = selling_hint = keywords_hint = forbidden_hint = hashtags_hint = ""
            tone_hint = "口吻调性：亲切、专业"
            style_hint = "文案风格要求：真实有趣"

        user_keywords_hint = f"\n用户指定的额外提示关键词/新鲜事：{keywords}" if keywords else ""

        prompt = f"""你是一个专业的朋友圈文案策划师。请为【{industry_tag or '通用'}】行业，根据以下设定生成一条高质量的微信朋友圈文案。

【行业与产品设定】
{product_hint}
{selling_hint}
{tone_hint}
{style_hint}
{keywords_hint}
{forbidden_hint}
{hashtags_hint}
{user_keywords_hint}

【写作铁律】
1. 只返回朋友圈文案内容本身，绝对不要包含任何"好的，这是为您生成的文案："或 markdown 代码块标记，不要包含其他解释性语言。
2. 语言要生动、真实、接地气，像一个真人在发朋友圈，避免浓浓的 AI 腔。
3. 长度控制在 50~150 字之间，适当使用 1-3 个 emoji 点缀。
4. 自然融入相关设定和用户指定的额外提示关键词/新鲜事。
"""

        response = await _ai_service.start_chat(agent_id="", message=prompt, cache_session=False)

        if response.get("success"):
            return ok({"text": response.get("content", "").strip()})
        else:
            return err(40000, response.get("error", "生成失败"), {"message": response.get("error", "生成失败")})
    except Exception as e:
        logger.error(f"生成朋友圈文案异常: {e}")
        return err(40000, "操作失败", {"message": str(e)})


async def _bg_generate(planner, industry_tag, days, image_model_id, custom_prompt, start_date, industry_profile, daily_count, task_id=""):
    global _is_generating, _generate_error
    try:
        await planner.generate_monthly_plan(
            industry_tag=industry_tag, days=days, image_model_id=image_model_id,
            custom_prompt=custom_prompt, start_date=start_date, industry_profile=industry_profile,
            daily_count=daily_count,
        )
    except Exception as e:
        logger.error(f"[后台生成] 异常: {e}")
        if task_id:
            if task_id in _tasks:
                _tasks[task_id]["error"] = str(e)
        else:
            _generate_error = str(e)
    finally:
        if task_id:
            if task_id in _tasks:
                _tasks[task_id]["generating"] = False
        else:
            _is_generating = False


@router.post("/api/moment/generate")
async def generate_moments(request: Request):
    """触发 AI 全托管生成未来 N 天朋友圈 (异步后台生成版)"""
    global _is_generating, _generate_error
    try:
        from src.crm.account_data import get_active_account
        from src.crm.moment_planner_service import MomentPlannerService
        from src.api.config_api import _ai_service

        body = await request.json()
        days = int(body.get("days", 7))
        start_date = body.get("start_date", "")
        industry_tag = body.get("industry_tag", "通用营销")
        custom_prompt = body.get("custom_prompt", "")
        daily_count = int(body.get("daily_count", 1))
        task_id = body.get("task_id", "")

        # 传统的全局锁逻辑：只有没传 task_id，且 days > 1 的大规模生成才加全局锁
        if not task_id and days > 1:
            if _is_generating:
                return err(40000, "操作失败", {"message": "日历排期生成任务正在运行中，请稍后再试"})
            _is_generating = True
            _generate_error = None

        account_id = get_active_account()
        if account_id == "default":
            if not task_id and days > 1:
                _is_generating = False
            return err(40000, "操作失败", {"message": "微信未连接，请先确保微信已登录并被系统识别"})

        planner = MomentPlannerService(account_id, _ai_service)

        from src.api.config_api import _load_configs
        image_model_id = _load_configs().get("external_api_settings", {}).get("image_model", "")

        industry_profile = None
        try:
            from src.crm.industry_config import IndustryConfigManager
            from src.api.instance_settings_api import load_instance_settings
            icm = IndustryConfigManager(account_id=account_id)
            for profile in icm.get_all_profiles():
                if profile.name == industry_tag:
                    industry_profile = profile
                    break
            if not industry_profile:
                try:
                    cfg = load_instance_settings(account_id)
                    profile_id = cfg.get("industry_profile_id")
                    if profile_id:
                        industry_profile = icm.get_profile_by_id(profile_id)
                except Exception:
                    pass
            if not industry_profile:
                industry_profile = icm.get_active_profile()
            if industry_profile:
                logger.info(f"[朋友圈] 已加载行业配置: {industry_profile.name}")
        except Exception as e:
            logger.warning(f"[朋友圈] 读取行业配置失败: {e}")

        if task_id:
            _tasks[task_id] = {"generating": True, "error": None}

        asyncio.create_task(_bg_generate(
            planner=planner, industry_tag=industry_tag, days=days,
            image_model_id=image_model_id, custom_prompt=custom_prompt,
            start_date=start_date, industry_profile=industry_profile,
            daily_count=daily_count, task_id=task_id
        ))
        return ok({"message": "排期生成任务已启动，正在后台生成中", "task_id": task_id})
    except Exception as e:
        logger.error(f"启动自动排期异常: {e}")
        if not task_id and days > 1:
            _is_generating = False
        return err(40000, "操作失败", {"message": str(e)})


@router.get("/api/moment/generate/status")
async def get_generate_status(task_id: str = ""):
    """获取朋友圈排期生成后台任务的状态"""
    if task_id:
        info = _tasks.get(task_id, {"generating": False, "error": None})
        return ok(info)
    return ok({"generating": _is_generating, "error": _generate_error})
