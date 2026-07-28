"""
Prompt 路由器 — 对标并超越 xm-bot4 工作流中的多 LLM 节点分支
"""
import random, logging, os
from typing import Optional, Tuple
from src.crm.industry_config import IndustryProfile
from . import prompt_builder_helpers as pb_helpers
from . import prompt_templates

logger = logging.getLogger(__name__)

class PromptRouter:
    """Prompt 路由器 + 固定话术库"""

    @staticmethod
    def _has_pricing_data(config: Optional[IndustryProfile]) -> bool:
        if not config: return False
        if getattr(config, 'price_list', None): return True
        knowledge = getattr(config, 'knowledge', '') or ''
        return any(m in knowledge for m in ['元', '月付', '年付', '套餐', '价格', '收费', '¥', '￥', '免费试用', '报价'])

    @staticmethod
    def route(
        intent: str,
        message: str,
        industry_config: Optional[IndustryProfile] = None,
        chat_round: int = 0,
        history_messages: list = None,
        session_id: str = None,
        account_id: str = "main",
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        profile_tags_str = "暂无"
        if session_id:
            try:
                from src.crm.profile_manager import ProfileManager
                prof = ProfileManager(account_id=account_id).get_profile(session_id)
                if prof and prof.tags:
                    profile_tags_str = ", ".join(f"[{t.subcategory}]{t.value}" for t in prof.tags)
                # 🌟 将历史对话摘要前置注入，让 AI 拥有跨会话的长期记忆
                if prof and prof.conversation_summary:
                    profile_tags_str = f"📋历史摘要: {prof.conversation_summary} | 当前标签: {profile_tags_str}"
            except Exception as e:
                logger.error(f"[PromptRouter] 获取画像标签失败: {e}")

        sdr_context_str = "未启用自动跟单"
        if session_id:
            try:
                from src.utils.db_manager import WeChatDBManager
                db = WeChatDBManager()
                for task in db.get_auto_follow_tasks():
                    if task.get("status") == "active" and session_id in (task.get("targets") or []):
                        t_state = (task.get("execution_state") or {}).get(session_id) or {}
                        follow_count = t_state.get("follow_count", 0)
                        follow_days = task.get("follow_days", 7)
                        sdr_context_str = f"已挂载活跃自动跟单任务(任务ID:{task.get('task_id')})，当前处于第 {follow_count}/{follow_days} 天的触达阶段"
                        break
            except Exception as e:
                logger.error(f"[PromptRouter] 获取 SDR 跟单状态失败: {e}")

        try:
            from src.utils.message_processor import MessageProcessor
            parsed = MessageProcessor().parse_message(message)
            if parsed.type == 'wxpay_qr':
                return "收到您的付款二维码，我们正在系统后台为您验证或核销服务，请稍候哈 😊", None, None
        except Exception as pe:
            logger.error(f"[路由] 消息多模态解析失败: {pe}")

        allow_emoji = random.random() < 0.05
        msg_lower = message.lower()

        if intent == 'greeting':
            prompt = pb_helpers.build_casual_prompt(message, industry_config, history_messages, allow_emoji, profile_tags_str, sdr_context_str)
            return None, prompt, None

        if intent == 'negative':
            prompt = pb_helpers.build_casual_prompt(message, industry_config, history_messages, allow_emoji, profile_tags_str, sdr_context_str)
            return None, prompt, None

        if intent == 'transfer_to_manual':
            prompt = pb_helpers.build_business_prompt(message, industry_config, chat_round, history_messages, allow_emoji, profile_tags_str, sdr_context_str)
            return None, prompt, None

        if intent == 'material_request':
            is_xm_bot4 = False
            if industry_config:
                is_xm_bot4 = (
                    getattr(industry_config, 'name', '') == 'xm-bot4系统' or 
                    getattr(industry_config, 'id', '') == 'sys_001'
                )

            enable_live = getattr(industry_config, 'enable_live_record', False) or is_xm_bot4

            # 0. 分离 materials 中的图片/视频物料与其它文档物料
            image_materials = []
            doc_materials = []
            if industry_config and getattr(industry_config, 'materials', None):
                raw_mats = industry_config.materials
                if isinstance(raw_mats, list):
                    media_extensions = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".mp4", ".mov", ".avi")
                    for m in raw_mats:
                        if isinstance(m, str):
                            lowercase = m.lower()
                            ext_val = None
                            for key in ["ext=", "path=", "file=", "name="]:
                                if key in lowercase:
                                    try:
                                        part = lowercase.split(key)[1].split('&')[0].split('#')[0]
                                        if part:
                                            ext_val = part
                                            break
                                    except Exception:
                                        pass
                            
                            is_media = False
                            if ext_val:
                                clean_ext = ext_val if ext_val.startswith('.') else '.' + ext_val.split('.')[-1]
                                is_media = clean_ext in media_extensions
                            else:
                                is_media = lowercase.split('?')[0].endswith(media_extensions)
                                if not is_media:
                                    is_media = any(ext in lowercase for ext in media_extensions)
                            
                            if is_media:
                                image_materials.append(m)
                            else:
                                doc_materials.append(m)

            wants_image_explicit = any(kw in msg_lower for kw in ["照片", "图片", "样板", "效果图", "实拍", "截图"])
            wants_video_explicit = any(kw in msg_lower for kw in ["视频", "演示", "操作视频", "怎么跑", "实操", "录制"])
            wants_doc_explicit = any(kw in msg_lower for kw in ["手册", "说明书", "教程", "文档", "文件", "pdf", "word", "docx", "excel", "xlsx", "ppt", "pptx", "使用说明", "报价表", "表格"])

            # 收集可用的物料清单描述，传给 AI 做意图决策
            available_materials_desc = []
            if is_xm_bot4:
                available_materials_desc.append("- 【系统操作拓客演示视频】：系统可以为其进行 10 秒电脑全自动拓客实时演示录屏。若客户需要视频、想看演示效果，你可以直接回复承诺录屏发给他。回复中必须包含 '实时录制'、'演示视频' 或 '操作视频'、'录个视频'、'视频发给' 等字样以触发底层物理发送逻辑。")
            
            if image_materials:
                available_materials_desc.append(f"- 【图片物料清单】：{', '.join(image_materials)}")
            if doc_materials:
                available_materials_desc.append(f"- 【文档手册资料】：{', '.join(doc_materials)}")
            if industry_config and getattr(industry_config, 'knowledge_files', None):
                files = [f.get("name", "文档") + f" ({f.get('url')})" for f in industry_config.knowledge_files if f.get("url")]
                if files:
                    available_materials_desc.append(f"- 【参考资料/白皮书】：{', '.join(files)}")
            
            materials_context = "\n".join(available_materials_desc) if available_materials_desc else "暂无专属物料"
            
            base_prompt = pb_helpers.build_business_prompt(message, industry_config, chat_round, history_messages, allow_emoji, profile_tags_str, sdr_context_str)
            
            material_prompt = f"""{base_prompt}

============================================================
【重要：物料与视频发送决策规则】（必须严格执行，由 AI 判定用户意图）
============================================================
当前系统拥有的可用物料/视频库：
{materials_context}

请认真分析客户当前发送的消息和聊天上下文历史，按如下规则进行回复：
1. **意图深度过滤**：只有当客户在**真诚地向你索要**上述某项产品视频、操作演示、图片或说明文档时（例如："发我看看视频"、"发个操作手册"），你才应该在回复中承诺发送。
2. **拒绝生硬与误判**：如果对方发送的是一段很长的业务说明、他自己整理的流程、或者是其他的日常陈述，他**绝非**是在向你索要资料。请像正常业务交流一样解答或认可他的内容，【绝对禁止】回复说要给他录视频或发文档，否则会极其生硬和穿帮！
3. **物理承诺发送机制**：
   - 如果客户确实想看操作视频或演示：请在最终回复中**明确承诺**您将为其发送/录制演示视频。你的回复中**必须且只能**包含以下触发词之一：【实时录制】、【演示视频】、【操作视频】、【录个视频】、【视频发给你】、【录屏】。底层系统将自动识别该承诺，并在你发完消息后自动调用电脑录屏并发送 10 秒操作演示。
   - 如果客户确实想要查看说明文档或图片：请在最终回复中**明确承诺**会发资料给他。你的回复中**必须且只能**包含以下触发词之一：【发资料】、【发白皮书】、【发文档】、【发送文档】、【发送文件】、【发给你】、【资料发给】。系统会自动调取最匹配的文件直接发送给对方。
   - 如果用户只是在进行常规咨询，未明确索要物料，只需做普通的专业文本解答即可，无需提及要发资料/视频。
"""
            # 预备最优候选物料，如果 AI 确实回复了承诺，底层物理引擎将使用该候选路径发送
            best_candidate_file = None
            if wants_video_explicit and enable_live:
                best_candidate_file = "__live_record__"
            else:
                # 智能物料过滤与筛选：如果包含文档、图片或者其它倾向，我们优先根据匹配词挑选
                all_mats = doc_materials + image_materials
                matched_mats = []
                
                # 尝试精准搜索匹配用户意图的文件名
                for mat in all_mats:
                    mat_lower = str(mat).lower()
                    mat_name = os.path.basename(mat_lower).split('?')[0]
                    
                    # 智能解析中文或常见拼音/英文简称
                    if "教程" in msg_lower and ("教程" in mat_name or "jc" in mat_name or "course" in mat_name or "guide" in mat_name):
                        matched_mats.append(mat)
                    elif "报价" in msg_lower and ("报价" in mat_name or "价格" in mat_name or "bj" in mat_name or "price" in mat_name):
                        matched_mats.append(mat)
                    elif "手册" in msg_lower and ("手册" in mat_name or "说明" in mat_name or "manual" in mat_name):
                        matched_mats.append(mat)
                    elif "演示" in msg_lower and ("演示" in mat_name or "demo" in mat_name):
                        matched_mats.append(mat)
                
                if matched_mats:
                    best_candidate_file = ",".join(matched_mats)
                else:
                    # 如果没有精准的文件名关键词匹配，则按照明确类型倾向分流，最后默认仅发第一个主要物料防刷屏
                    if wants_doc_explicit and doc_materials:
                        best_candidate_file = ",".join(doc_materials)
                    elif wants_image_explicit and image_materials:
                        best_candidate_file = ",".join(image_materials)
                    elif all_mats:
                        # 兜底：仅发送列表中的第一个核心物料，绝不一次性多发
                        best_candidate_file = all_mats[0]
                    elif is_xm_bot4:
                        best_candidate_file = "__live_record__"

            return None, material_prompt, best_candidate_file


        if intent == 'price_inquiry':
            extra_pricing_text = ""
            if not PromptRouter._has_pricing_data(industry_config):
                if industry_config and getattr(industry_config, 'id', '') == 'sys_001':
                    try:
                        from src.utils.pricing_sync import inject_pricing_to_sys001
                        inject_pricing_to_sys001()
                        import src.utils.pricing_sync as _ps
                        if _ps._cached_pricing_text:
                            extra_pricing_text = _ps._cached_pricing_text
                    except Exception as _pe:
                        logger.warning(f"[路由] sys_001 实时定价拉取失败: {_pe}")
            
            prompt = pb_helpers.build_price_prompt(
                message=message, industry_config=industry_config, chat_round=chat_round,
                history_messages=history_messages, profile_tags=profile_tags_str,
                sdr_context=sdr_context_str, extra_pricing_text=extra_pricing_text
            )
            return None, prompt, None

        if intent == 'friend_accepted':
            prompt = pb_helpers.build_friend_accepted_prompt(message, industry_config, allow_emoji)
            return None, prompt, None

        if intent == 'casual_chat':
            prompt = pb_helpers.build_casual_prompt(message, industry_config, history_messages, allow_emoji, profile_tags_str, sdr_context_str)
            return None, prompt, None

        prompt = pb_helpers.build_business_prompt(message, industry_config, chat_round, history_messages, allow_emoji, profile_tags_str, sdr_context_str)
        return None, prompt, None
