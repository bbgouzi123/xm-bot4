"""
AI 媒体与内容生成 API (解耦自 file_api)
"""
import os
import uuid
import logging
from pathlib import Path
from fastapi import APIRouter, Request
from src.utils.response import ok, err

router = APIRouter(prefix='/api/file', tags=['ai-media'])
logger = logging.getLogger(__name__)

# 文件存储目录
UPLOAD_DIR = Path.home() / '.xm-ai-bot' / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_ai_service():
    """动态获取配置生效的最新 AI 服务"""
    try:
        from src.api.config_api.state import _ai_service
        if _ai_service and _ai_service.is_configured():
            return _ai_service
    except Exception:
        pass
    try:
        import app.state as app_state
        if getattr(app_state, 'ai_service', None) and app_state.ai_service.is_configured():
            return app_state.ai_service
    except Exception:
        pass
    return None


@router.post('/ai-generate-copy')
async def ai_generate_copy(request: Request):
    try:
        data = await request.json()
        prompt = data.get('prompt', '')
        if not prompt:
            return err(40000, '请输入生成文案的主题或指令')
        
        # 获取当前用户绑定的行业配置（使用全局 IndustryConfigManager 且结合当前活跃微信专属配置）
        industry_info = ""
        try:
            from src.crm.industry_config.manager import IndustryConfigManager
            from src.crm.account_data import get_active_account
            from src.api.instance_settings_api import load_instance_settings
            active_wxid = data.get("account_id") or data.get("wxid") or get_active_account()
            inst_settings = load_instance_settings(active_wxid)
            inst_profile_id = inst_settings.get("industry_profile_id", "")
            
            global_icm = IndustryConfigManager(account_id="global")
            active_profile = None
            if inst_profile_id:
                active_profile = global_icm.get_profile_by_id(inst_profile_id)
            if not active_profile:
                active_profile = global_icm.get_active_profile()

            if active_profile:
                industry_info = (
                    f"\n【用户当前绑定的核心业务背景】"
                    f"\n行业名称: {active_profile.name}"
                    f"\n主营产品/服务: {active_profile.product}"
                    f"\n核心卖点: {active_profile.selling_point}"
                    f"\n产品/行业知识库: {active_profile.knowledge}"
                )
        except Exception as e:
            print(f"[CRM] 获取绑定行业配置异常: {e}")

        # 构造给大模型的 system prompt 与 user prompt
        system_instructions = (
            "你是一个顶级的社群与微信朋友圈文案策划专家。请根据用户的需求，并结合当前绑定的行业背景，生成一段极具说服力、吸引人的群发文案。"
            "文案要求亲切、简练、有营销张力，适度带有符合语境的 Emoji。支持替换变量：可以使用 {昵称} 代替客户微信名。"
            f"{industry_info}"
        )
        
        message = f"指令任务：{prompt}\n\n请直接输出生成的正文，无需任何多余的引言和 markdown 标记包裹。"
        
        # 调用大模型
        active_ai = get_ai_service()
        if active_ai:
            res = await active_ai.start_chat(
                agent_id="",
                message=f"{system_instructions}\n\n{message}"
            )
            content = res.get("content", "")
            if not content:
                content = "AI 暂时没有给出合适的创意，请稍后重试。"
        else:
            # 兜底：如果 AI 没配置，我们本地提供一组富有创意的模版！
            import random
            templates = [
                "嗨，{昵称}！悄悄告诉你一个好消息 🎉。我们家最近上线了全新的智能获客雷达系统！限时开放免费体验名额，回复【体验】即可获取专属通道哦 🚀",
                "Hi {昵称}，好久没联系啦 ✨。最近工作忙吗？我们为老客户准备了一份专属的《行业提效与自动化获客白皮书》，点击下方附件即可预览，觉得有帮助可以随时找我聊聊哦 💡",
                "【xm-bot4特别企划】{昵称}，您有一份专属的新品折扣尚未领取 🎁！全场产品限时 8.5 折起，名额有限，先到先得。点击图片即可查看详情！"
            ]
            content = random.choice(templates)
            
        return ok({"text": content})
    except Exception as e:
        return err(40000, str(e))


@router.post('/ai-generate-image')
async def ai_generate_image(request: Request):
    try:
        data = await request.json()
        prompt = data.get('prompt', '')
        if not prompt:
            return err(40000, '请输入想要生成的图片素材描述')

        # 产生唯一文件名
        unique_name = f'ai_poster_{uuid.uuid4().hex}.png'
        save_path = UPLOAD_DIR / unique_name

        active_ai = get_ai_service()
        has_ai_image = False
        
        # 获取当前用户绑定的行业配置（使用全局 IndustryConfigManager 且结合当前活跃微信专属配置）
        industry_info = ""
        industry_name = ""
        try:
            from src.crm.industry_config.manager import IndustryConfigManager
            from src.crm.account_data import get_active_account
            from src.api.instance_settings_api import load_instance_settings
            active_wxid = data.get("account_id") or data.get("wxid") or get_active_account()
            inst_settings = load_instance_settings(active_wxid)
            inst_profile_id = inst_settings.get("industry_profile_id", "")
            
            global_icm = IndustryConfigManager(account_id="global")
            active_profile = None
            if inst_profile_id:
                active_profile = global_icm.get_profile_by_id(inst_profile_id)
            if not active_profile:
                active_profile = global_icm.get_active_profile()

            if active_profile:
                industry_name = active_profile.product or active_profile.name
                industry_info = (
                    f"\n【用户当前绑定的核心业务背景】"
                    f"\n行业名称: {active_profile.name}"
                    f"\n主营产品/服务: {active_profile.product}"
                    f"\n核心卖点: {active_profile.selling_point}"
                )
        except Exception as e:
            print(f"[CRM] 获取绑定行业配置异常: {e}")

        # 1. 尝试调用 AI 的图像生成 API 获得真实 AI 生成的图片
        if active_ai:
            try:
                # 结合行业背景进行增强提示词，使生成的图片更加贴切
                enhanced_prompt = f"关于【{industry_name}】的创意推广海报。要求：{prompt}" if industry_name else prompt
                ai_image_url = await active_ai.generate_image(enhanced_prompt)
                if ai_image_url:
                    import httpx
                    async with httpx.AsyncClient(timeout=30) as client:
                        img_resp = await client.get(ai_image_url)
                        if img_resp.status_code == 200:
                            with open(save_path, 'wb') as f:
                                f.write(img_resp.content)
                            has_ai_image = True
            except Exception as e:
                print(f"[AI] 图像生成异常，将降级本地模板生成: {e}")

        # 2. 如果 AI 图像生成失败/未配置/不支持，则降级为本地大模型文案提炼 + Pillow 绘制海报
        title_text = f"智能{industry_name or '营销'} · 蓄势发力"
        sub_text = prompt
        
        if not has_ai_image:
            if active_ai:
                try:
                    instruct = (
                        "请将以下用户的海报生成诉求，结合当前绑定的行业背景，提炼成一行非常简短有力的主标题（8字以内，需与绑定的行业/产品高度关联）"
                        "和一行副标题（15字以内），以 JSON 格式返回，例如：{\"title\": \"新品上市\", \"subtitle\": \"限时特惠，低至五折\"}。千万不要有任何多余的引言文字。"
                        f"{industry_info}"
                    )
                    res = await active_ai.start_chat(
                        agent_id="",
                        message=f"{instruct}\n\n诉求：{prompt}"
                    )
                    import json
                    cleaned = res.get("content", "").replace("`", "").strip()
                    start_idx = cleaned.find("{")
                    end_idx = cleaned.rfind("}")
                    if start_idx != -1 and end_idx != -1:
                        json_data = json.loads(cleaned[start_idx:end_idx+1])
                        title_text = json_data.get("title", title_text)
                        sub_text = json_data.get("subtitle", sub_text)
                except Exception:
                    pass
            
            # 调用辅助模块生成渐变海报
            from src.utils.poster_generator import generate_poster
            generate_poster(prompt, title_text, sub_text, save_path)
            
        return ok({
            'file_id': unique_name,
            'file_name': f'AI生成图片-{prompt[:6]}.png' if has_ai_image else f'AI智能海报-{title_text[:6]}.png',
            'file_path': str(save_path),
            'size': os.path.getsize(save_path),
        })
    except Exception as e:
        return err(40000, str(e))
