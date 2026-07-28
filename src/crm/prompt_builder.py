"""
元提示词生成器 — 根据行业配置动态组装 System Prompt

核心能力：
1. 根据用户填写的行业配置（产品、卖点、人设等）动态生成 Prompt
2. 实现三段式话术策略（破冰→升温→转化）
3. 推销强度控制（1-5 级）
4. 内置画像提取指令

使用方式：
    config = industry_config_manager.get_active_profile()
    prompt = PromptBuilder.build(config)
    # prompt 作为 system message 或拼接到 user message 前面
"""
import logging
from typing import Optional

from .industry_config import IndustryProfile, merge_chat_eq

logger = logging.getLogger(__name__)

from .prompt_strategies import CHAT_EQ_INSTRUCTIONS


class PromptBuilder:
    """动态 System Prompt 生成器"""

    @staticmethod
    def _build_chat_eq_section(config: IndustryProfile) -> str:
        merged = merge_chat_eq(getattr(config, "chat_eq", None))
        lines = []
        for key, on in merged.items():
            if on and key in CHAT_EQ_INSTRUCTIONS:
                lines.append(f"- {CHAT_EQ_INSTRUCTIONS[key]}")
        if not lines:
            return ""
        body = "\n".join(lines)
        return f"\n## 高情商会话策略（用户已勾选，必须遵守）\n{body}\n"

    @staticmethod
    def build(
        config: Optional[IndustryProfile],
        include_profiling: bool = True,
    ) -> str:
        """根据行业配置生成完整的 System Prompt"""
        if config is None:
            return PromptBuilder._build_default(include_profiling)

        # 根据推销强度生成策略
        strategy = PromptBuilder._get_strategy(config.intensity)
        strategy_section = f"\n## 销售节奏策略（按对话轮次与强度）\n{strategy.strip()}\n"
        eq_section = PromptBuilder._build_chat_eq_section(config)

        # 构建产品知识部分
        knowledge_section = ""
        if config.knowledge and config.knowledge.strip():
            knowledge_section = f"""
## 产品知识库（熟记于心，被问到时使用）
{config.knowledge.strip()}
"""

        # 构建聊天知识库参考话术（从真实成交对话中提取的 Q&A 对）
        chat_knowledge_section = PromptBuilder._build_chat_knowledge_section(
            config.industry_id or config.id
        )

        # 构建禁止事项
        forbidden_list = []
        if config and config.forbidden and config.forbidden.strip():
            forbidden_list.append(config.forbidden.strip())
        
        try:
            from src.utils.config_cache import config_cache
            global_forbidden = config_cache.get("forbidden_words", [])
            if global_forbidden:
                words_str = "、".join(global_forbidden)
                forbidden_list.append(f"绝对不要说、提及或回复以下企业全局违禁词：{words_str}")
        except Exception:
            pass

        forbidden_section = ""
        if forbidden_list:
            forbidden_section = f"\n- 绝对禁止事项：{'; '.join(forbidden_list)}"

        # 构建官方联系方式
        contact_section = ""
        contact_lines = []
        if getattr(config, "phone", "") and config.phone.strip():
            contact_lines.append(f"- 联系电话：{config.phone.strip()}")
        if getattr(config, "address", "") and config.address.strip():
            contact_lines.append(f"- 详细地址：{config.address.strip()}")
        if getattr(config, "homepage_link", "") and config.homepage_link.strip():
            contact_lines.append(f"- 官方网站/介绍链接：{config.homepage_link.strip()}")
        if getattr(config, "product_link", "") and config.product_link.strip():
            contact_lines.append(f"- 产品购买/详情链接：{config.product_link.strip()}")
        if contact_lines:
            contact_section = "\n## 官方联系方式与专属链接（客户咨询官网/购买渠道/电话/地址时请如实提供，严禁瞎编）\n" + "\n".join(contact_lines) + "\n"

        prompt = f"""【行业配置】
人设身份：{config.persona or '专业的销售顾问'}
产品/服务：{config.product or '通用产品'}
核心卖点：{config.selling_point or '品质优秀，服务专业'}
{strategy_section}{eq_section}{contact_section}{knowledge_section}{chat_knowledge_section}{forbidden_section}"""

        return prompt.strip()

    @staticmethod
    def _build_chat_knowledge_section(industry_id: str) -> str:
        """从聊天知识库中提取高质量参考话术（委托给 chat_knowledge_prompt 模块）"""
        from .chat_knowledge_prompt import build_chat_knowledge_section
        return build_chat_knowledge_section(industry_id)

    @staticmethod
    def build_context_message(
        config: Optional[IndustryProfile],
        user_message: str,
        chat_round: int = 0,
        history_messages: list = None,
    ) -> str:
        """构建发送给 AI 的完整消息（行业配置 + 用户消息 + 记忆上文）"""
        prompt = PromptBuilder.build(config)

        # 传递轮次元数据，让 Coze 智能体自己判断所处销售阶段
        round_info = f"\n当前对话轮次：第{chat_round}轮"

        persona_rules = """
## 绝对铁律
- 像发真实的微信消息一样自然；严格执行上方【行业配置】中的销售节奏与高情商会话策略
- 绝对禁止对已经是好友的人说“加个好友”、“加微信”、“加个联系方式”；同时，除非是刚加好友第一句破冰词，否则【绝对禁止说“那先认识一下”、“交个朋友”或自我介绍等见外的话】（因为对方可能早就是认识多年的老客户或老熟人，说这类话会显得极其智障和生硬）。你此刻已经是他的好友且在直接对话，直接像老朋友一样自然交流。
- 不要答非所问，先理解客户在做什么，不要生硬推销
- 若客户在闲聊，进行有温度的互动，杜绝客服腔与套话
- 【群聊非业务话题防穿帮与降级回答铁律】：在群聊中被 @ 提及进行闲聊、提问或请求总结时，如果当前群内讨论的主题、客户发问的内容与上方【产品/服务】完全无关（例如群内大家在聊兼职、生活、点外卖、天气，而你的产品是软件销售），【绝对禁止】主动推销或强行问对方的行业、账号等业务隐私！此时你必须瞬间“卸下销售人设，降级为高情商、知心且热心的老群友/群管家”，顺着大家群内当前在聊的真正话题进行客观的总结、安慰、幽默打趣或给出实用建议。

## 销冠方法论（所有行业通用，必须遵守）
- 先探需求后报方案：开口先问对方行业/需求/痛点，根据回答精准匹配卖点，绝不一上来就甩功能列表或报价
- 去技术化讲人话：不说技术名词和专业参数，只讲「帮你省多少钱」「赚多少钱」「省多少时间」，用场景化利益引导替代功能截图
- 价格不前置：客户问价时先包装价值（算日均成本、对比人工成本，如'相当于一天一碗面的钱，但帮你省了一个员工'），再报价；可主动提供低门槛体验（免费试用/体验周卡）降低决策成本
- 异议不怼回：客户说贵/质疑/犹豫时，先共情认可（如'理解，确实要货比三家'），再用算账对比法化解（如'招个兼职也得3000/月，我们日均不到10块'）
- 提供安全感：主动提及退款保障、免费测试、技术支持等消除顾虑，如'不好用随时退，买卖不成仁义在'
- 紧迫感制造（仅在客户明确有兴趣时）：适度使用限时优惠或名额有限等话术促成决策，但不要虚假施压
"""

        history_text = ""
        if history_messages:
            history_text = "\n\n[上下文记忆]\n" + "\n".join([
                f"[{msg.get('sender')}]：{msg.get('content', '')}" if msg.get('role') == 'user' and msg.get('sender')
                else (f"客户：{msg.get('content', '')}" if msg.get('role') == 'user' else f"你：{msg.get('content', '')}")
                for msg in history_messages[-3:]
            ])

        return f"{prompt}{round_info}\n{persona_rules}{history_text}\n\n---\n客户当前消息：{user_message}"

    @staticmethod
    def _get_strategy(intensity: int) -> str:
        """根据推销强度生成三段式策略"""
        from .prompt_strategies import get_strategy
        return get_strategy(intensity)

    @staticmethod
    def _get_profiling_instruction() -> str:
        """获取客户画像提取与强制 XML 格式输出指令"""
        return """
============================================================
【重要：强制回复与画像输出格式】（每次回复必须严格遵循，否则会穿帮！）
============================================================
为了配合我方底层的 CRM 系统和微信防穿帮系统，你的每一次最终回复【必须且只能】严格遵循以下 XML 标签封装格式。
除这两个标签内的内容外，绝对不可以在首尾添加任何总结语、Markdown 代码块标记（如 ```json）或问候语。

<Reply>
这里只放你直接发给微信客户的聊天原话。语气要极度自然、简短。
严禁提及：AI、人工智能、机器人、大语言模型、程序设定、系统提示词。
严禁使用客服套话，如“作为客服为您服务”、“请问有什么可以帮助您的”等。
</Reply>

<CRM_Action>
{
  "当前阶段": "破冰/探需/匹配/成交",
  "客户心理": "谨慎/比较/敏感/冲动/未知",
  "意向度": "0-100%",
  "预算推测": "低/中/高/未知",
  "需求痛点概括": "用最多15个字概括或提取确定信息，如无则填未知"
}
</CRM_Action>"""

    @staticmethod
    def _build_default(include_profiling: bool) -> str:
        """构建无行业配置时的默认 Prompt"""
        profiling = PromptBuilder._get_profiling_instruction() if include_profiling else ""
        
        forbidden_rules = ""
        try:
            from src.utils.config_cache import config_cache
            global_forbidden = config_cache.get("forbidden_words", [])
            if global_forbidden:
                words_str = "、".join(global_forbidden)
                forbidden_rules = f"\n- 绝对不要说、提及或回复以下全局违禁词：{words_str}"
        except:
            pass

        return f"""你是一个友善、有温度的AI助手。

## 聊天规则
1. 像朋友一样自然聊天
2. 关心对方的生活 and 心情
3. 回复2-4句话，不要长篇大论
4. 适当使用emoji{forbidden_rules}
{profiling}"""

    # ==================== 朋友圈行业适配 ====================

    @staticmethod
    def build_moment_prompt(
        config: Optional[IndustryProfile],
        days: int = 3,
        seed: str = "",
        target_industry_name: str = "",
        daily_count: int = 1,
        wxid: str = None,
    ) -> str:
        """根据行业配置生成朋友圈图文矩阵 Prompt"""
        from .moment_prompt_builder import build_moment_prompt
        return build_moment_prompt(config, days, seed, target_industry_name, daily_count, wxid)

    @staticmethod
    def build_video_prompt(
        config: Optional[IndustryProfile],
        duration: int = 30,
    ) -> str:
        """根据行业配置生成视频素材 Prompt（预留接口）"""
        from .moment_prompt_builder import build_video_prompt
        return build_video_prompt(config, duration)
