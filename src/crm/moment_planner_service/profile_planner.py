"""
画像专属 30 天发圈规划生成器
"""
import logging
import asyncio
from typing import Optional, Any
from src.crm.profile_manager import ProfileManager

logger = logging.getLogger(__name__)

class ProfileMomentPlanner:
    """基于客户画像的专属 30天发圈规划器"""
    
    def __init__(self, account_id: str = "main"):
        self.account_id = account_id

    async def generate_plan_for_customer(self, wxid: str, ai_service: Any, image_model_id: str = "") -> Optional[list]:
        """为特定客户生成 30 天专属营销朋友圈发圈规划"""
        try:
            mgr = ProfileManager(account_id=self.account_id)
            profile = mgr.get_profile(wxid)
            if not profile:
                logger.warning(f"[ProfilePlanner] 未找到客户画像: {wxid}")
                return None
                
            tags_str = ", ".join(f"[{t.subcategory}]{t.value}" for t in profile.tags) if profile.tags else "暂无"
            summary = profile.conversation_summary or "暂无详细对话摘要"
            
            prompt = (
                f"你是一个拥有 10 年丰富经验的高情商销售与微信运营专家。\n"
                f"我们现在有一位目标成交客户，其画像特征如下：\n"
                f"- 微信昵称: {profile.nickname}\n"
                f"- 画像标签: {tags_str}\n"
                f"- 客户心理与对话背景: {summary}\n\n"
                f"请你针对该客户的偏好、痛点和购买意向，为销售人员量身定制一份【30 天朋友圈侧面营销种草与信任建立计划】。\n"
                f"这套朋友圈计划是发布在销售员自己的微信朋友圈中，目的是让这位目标客户在刷朋友圈时看到，产生润物细无声的影响，从而引导成交。\n\n"
                f"要求：\n"
                f"1. 规划 30 天的排期，每隔 2-3 天发布一次（共生成 10 条高质量朋友圈朋友圈）。\n"
                f"2. 每次朋友圈应包含：【发布天数/时机】、【营销文案（高情商、接地气、利他、不生硬）】、【配图建议（画面感与可操作性）】、【心机解析（销售员写这条朋友圈背后的心理暗示与策略）】。\n"
                f"3. 请以 JSON 数组格式返回，每个对象包含字段：'day_offset' (int), 'text' (文案), 'image_suggestion' (配图建议), 'strategy_analysis' (心机策略)。\n"
                f"不要返回 markdown 格式包装，仅返回标准的 JSON 格式。"
            )
            
            response = await ai_service.start_chat(
                agent_id=image_model_id,
                message=prompt,
                session_id=f"profile_planner_{wxid}",
                cache_session=False
            )
            
            if response.get("success") and response.get("content"):
                import re
                import json as _json
                content = response["content"].strip()
                content = re.sub(r'```json\s*', '', content)
                content = re.sub(r'```\s*', '', content)
                arr_start = content.find('[')
                if arr_start >= 0:
                    content = content[arr_start:]
                parsed = _json.loads(content, strict=False)
                if isinstance(parsed, list):
                    return parsed
        except Exception as e:
            logger.error(f"[ProfilePlanner] 生成专属 30天朋友圈规划异常: {e}")
        return None
