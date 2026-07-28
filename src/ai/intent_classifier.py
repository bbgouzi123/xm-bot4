"""
意图分类器 — 对标并超越 xm-bot4 Coze 工作流中的"意图识别"节点

xm-bot4 在 Coze 工作流里用6个分支做意图识别，我们在 Python 端实现同等效果，
但更快（<1ms vs Coze 工作流的 1-2s）、更灵活（可动态扩展行业关键词）。

分类结果:
    greeting        — 打招呼/问候 → 走固定话术，不调 AI
    price_inquiry   — 问价格/收费 → 走固定话术或行业报价模板
    material_request— 要资料/文件 → 固定回复 + 触发文件发送
    negative        — 负面情绪/投诉 → 安抚话术 + 告警人工
    casual_chat     — 闲聊/生活话题 → DeepSeek 轻量人设（短回复）
    business        — 业务咨询/产品相关 → 完整业务人设 + CRM 画像
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class IntentClassifier:
    """快速意图分类器（纯规则，<1ms）"""

    # ===== 意图关键词库 =====

    # 问候类（仅保留真实的无歧义问候词组，去除名词称呼代词防止子串包含误判）
    GREETING_EXACT = {
        '你好', '在吗', '在不在', '嗨', 'hi', 'hello', '早上好', '中午好',
        '下午好', '晚上好', '早', '哈喽', '您好',
        '嘿', '嗨嗨', '在嘛', '在么', '在不', '你好呀', '你好啊', '嗨呀', '老板好',
    }

    # 价格/费用类
    PRICE_KEYWORDS = [
        '多少钱', '价格', '收费', '费用', '怎么卖', '报价', '优惠',
        '打折', '便宜', '贵不贵', '划算', '性价比', '套餐', '方案',
        '怎么收', '价位', '成本', '预算',
    ]

    # 要资料类（核心关键词：必须是用户「向机器人索取」的强信号，去除高歧义的「资料」单字防误判）
    MATERIAL_KEYWORDS = [
        # ✅ 「发给我」类强索取动作
        '发我', '给我看看', '发一份', '看看效果',
        # ✅ 明确的向机器人要资料的复合词（至少两字）
        '发资料', '要资料', '发下资料', '发份资料', '发个资料',
        '发文件', '发教程', '发文档', '发手册', '发方案', '发案例', '发物料',
        '宣传物料', '宣传册', '宣传材料', '介绍册', '画册',
        '发样板', '发效果图', '效果图发', '实拍图', '发图片给我', '图片发我',
        '发ppt', '发pdf', 'ppt发我', 'pdf发我',
        # ✅ 明确索取链接/官网的表达
        '官网是', '网址是', '主页是', '入口在哪', '网站地址',
    ]

    # 要资料类「弱信号词」：需要联合「没有主体是用户自己」条件才触发，不在此列表中独立触发
    # 注意：「资料」「文件」「图片」单独出现时高歧义，必须结合上下文才能判断方向，
    # 所以它们被移出强匹配词库，改由 classify() 中的语义反向过滤逻辑处理
    MATERIAL_WEAK_KEYWORDS = [
        '资料', '文件', '教程', '文档', '手册', '方案', '案例', '物料',
        '照片', '图片', '样板', '效果图', '截图', 'ppt', 'pdf',
        '官网', '网址', '网页', '链接', '主页', '网站', '网页地址',
    ]

    # 负面情绪类
    NEGATIVE_KEYWORDS = [
        '投诉', '退款', '骗子', '垃圾', '差评', '拉黑', '举报',
        '不满', '太差', '上当', '忽悠', '坑人', '骗人', '不靠谱',
        '删好友', '别烦我', '别发了', '不要了', '取消',
        '不要再发了', '不用了', '不用发了', '可以了', '可以啦', '不要发', '不要',
    ]

    # 转人工类
    TRANSFER_TO_MANUAL_KEYWORDS = [
        '转人工', '人工客服', '人工', '呼叫人工', '找人工', '人工服务',
        '有活人吗', '活人', '人工介入', '切换人工', '人工客服电话'
    ]

    # 闲聊信号词（当消息较短且包含这些词时判定为闲聊）
    CASUAL_SIGNALS = [
        '吃饭', '天气', '周末', '开心', '累', '睡觉', '早安', '晚安',
        '加油', '辛苦', '哈哈', '😂', '🤣', '666', '厉害', '牛',
        '真的吗', '是吗', '然后呢', '呢', '吧', '嘛',
        '好吧', '行吧', '嗯', '哦', '噢', '啊', '呀',
        '你是谁', '你是哪个', '你是哪位', '你哪位', '是谁', '哪个',
        '你是那位', '你那位', '那个',
        '加我干嘛', '有什么事', '有什么事情', '你是那个', '你是那里的', '你是哪里的',
    ]

    # 业务信号词（强制判定为业务意图）
    BUSINESS_SIGNALS = [
        '怎么用', '怎么操作', '怎么设置', '怎么配置', '怎么安装',
        '功能', '效果', '支持', '兼容', '版本', '升级', '更新',
        '合作', '代理', '加盟', '定制', '开发',
        '试用', '体验', '演示', 'demo',
        # 开客/营销行业高频词
        '获客', '引流', '拓客', '精准获客', '私域', '转化', '成交',
        '营销', '推广', '宣传', '客户', '线索', '订单', '销量',
        '商机', '意向', '跟进', '回访', '逻单', '谈单', '报单',
        # 产品了解类
        '了解', '介绍', '看看', '说说', '讲讲', '产品', '服务',
        '方案', '系统', '平台', '软件', '工具', '效率',
        '省钱', '降本', '增效', '自动化', '智能',
        # 行业通用业务词
        '报价', '订购', '购买', '买', '我要', '来一个', '要一个',
        '怎么收费', '流程', '周期', '工期', '交付',
    ]

    # ===== 用户「主动提供/给出」资料的反向过滤词 =====
    # 当消息命中这些前置修饰语时，说明主语是用户自己在给出，而非向机器人索取
    # 例如：「我把我的资料给你」、「把他的文件发给你」
    MATERIAL_SELF_OFFER_SIGNALS = [
        '我把我的', '把我的', '我的资料', '我的文件', '我的图片', '我的照片', '我的材料',
        '我来发', '我发给你', '我把资料', '我把文件', '我把图片',
        '他把', '她把', '他的资料', '她的资料',
        '自己的资料', '自己的文件', '自己的材料',
        '发给你', '给你看', '给你啊', '给你看看',  # 「给你」表示用户是发出方
        '把资料给你', '把材料给你', '把文件给你',
    ]

    @staticmethod
    def classify(message: str, history_context: list = None) -> dict:
        """快速意图分类"""
        msg = message.strip()
        msg_lower = msg.lower()
        msg_len = len(msg)

        # ===== 规则 -2：长文本判定过滤 =====
        # 如果消息长度超过 120 个字符，大概率是长篇说明、列表或复制的文章，不走硬编码关键词判定，直接交由 LLM 处理
        if msg_len > 120:
            return {
                "intent": "business",
                "confidence": 0.50,
                "reason": f"消息长度较长 ({msg_len} 字)，绕过规则短路径以防止误判，交由 AI 智能响应",
            }

        # ===== 规则 -1：过滤系统多模态占位符（如 [图片]、[表情]、[图片本地路径]: 等） =====
        # 如果是纯物理占位符（代表表情包或图片等媒体，没有真实的用户文本内容），直接判定为闲聊意图，
        # 并防止被误判为 material_request（要资料）等强意图。
        is_media_placeholder = False
        if msg in ("[图片]", "[表情]", "[文件]", "[视频]", "[语音]"):
            is_media_placeholder = True
        elif msg.startswith(("[图片本地路径]:", "[文件本地路径]:", "[图片本地路径]：", "[文件本地路径]：")):
            is_media_placeholder = True
        
        if is_media_placeholder:
            return {
                "intent": "casual_chat",
                "confidence": 0.90,
                "reason": "物理多模态占位符（图片/表情/文件/视频/语音），阻断业务强匹配并自动归为闲聊意图",
            }

        # ===== 规则 0：微信系统好友通过/添加提示 =====
        is_sys_add = False
        if "我通过了你的朋友验证请求" in msg:
            is_sys_add = True
        elif "现在可以开始聊天了" in msg and ("你已添加了" in msg or "已同意你的好友" in msg):
            is_sys_add = True
        elif "accepted your friend request" in msg_lower:
            is_sys_add = True

        if is_sys_add:
            return {
                "intent": "friend_accepted",
                "confidence": 1.0,
                "reason": "微信系统好友通过/添加提示",
            }

        # ===== 规则 1：精确匹配问候 =====
        # 去掉标点和 emoji 后做精确匹配
        clean_msg = re.sub(r'[~～！!？?。，,\s]+', '', msg_lower)
        if clean_msg in IntentClassifier.GREETING_EXACT:
            return {
                "intent": "greeting",
                "confidence": 0.95,
                "reason": f"精确匹配问候词: {clean_msg}",
            }

        # ===== 优先判定强业务意图（防止用户说“你好，多少钱”时被问候覆盖） =====
        
        # 强意图 0：转人工
        for kw in IntentClassifier.TRANSFER_TO_MANUAL_KEYWORDS:
            if kw in msg_lower:
                return {
                    "intent": "transfer_to_manual",
                    "confidence": 0.95,
                    "reason": f"转人工关键词: {kw}",
                }

        # 强意图 1：负面情绪
        for kw in IntentClassifier.NEGATIVE_KEYWORDS:
            if kw in msg_lower:
                return {
                    "intent": "negative",
                    "confidence": 0.90,
                    "reason": f"负面关键词: {kw}",
                }

        # 强意图 2：价格咨询
        for kw in IntentClassifier.PRICE_KEYWORDS:
            if kw in msg_lower:
                return {
                    "intent": "price_inquiry",
                    "confidence": 0.88,
                    "reason": f"价格关键词: {kw}",
                }

        # 强意图 3：要资料
        # ⚠️ 步骤一：先检测是否存在「用户主动提供/给出」的反向过滤信号
        # 若命中，说明用户是在「把自己的东西给机器人看」，而非向机器人索取，必须跳过此意图
        is_self_offering = any(sig in msg_lower for sig in IntentClassifier.MATERIAL_SELF_OFFER_SIGNALS)
        if is_self_offering:
            logger.info(f"[意图过滤] 消息包含用户主动提供资料的反向信号，跳过 material_request 判定: {msg}")
        else:
            # 步骤二：强关键词精确匹配（这些词已经是明确的索取动作）
            for kw in IntentClassifier.MATERIAL_KEYWORDS:
                if kw in msg_lower:
                    return {
                        "intent": "material_request",
                        "confidence": 0.85,
                        "reason": f"资料关键词(强匹配): {kw}",
                    }

            # 步骤三：弱关键词需结合「索取」动作词才触发
            # 弱词（如「资料」「文件」「图片」）单独出现高歧义，必须同时存在明确的向机器人要东西的动作词
            REQUEST_ACTION_WORDS = ['要', '想要', '需要', '请发', '能发', '可以发', '帮我发', '发来', '给我发',
                                    '能给', '可以给', '麻烦发', '请给', '发一下', '发下', '有吗', '有没有',
                                    '看看', '介绍一下', '了解一下', '获取', '拿一份', '来一份', '来一个']
            has_request_action = any(act in msg_lower for act in REQUEST_ACTION_WORDS)
            if has_request_action:
                for kw in IntentClassifier.MATERIAL_WEAK_KEYWORDS:
                    if kw in msg_lower:
                        return {
                            "intent": "material_request",
                            "confidence": 0.78,
                            "reason": f"资料关键词(弱匹配+请求动作): {kw}",
                        }

        # 强意图 4：业务信号（如“怎么操作”）
        for kw in IntentClassifier.BUSINESS_SIGNALS:
            if kw in msg_lower:
                return {
                    "intent": "business",
                    "confidence": 0.85,
                    "reason": f"业务关键词: {kw}",
                }

        # ===== 规则 2：降级问候子串匹配（仅限多字问候词） =====
        if msg_len <= 8:
            for word in IntentClassifier.GREETING_EXACT:
                # 必须大于1个字，防止 "早/嗨" 单字误杀普通含有该字的内容（如"这也太早了"）
                if len(word) > 1 and word in msg_lower:
                    return {
                        "intent": "greeting",
                        "confidence": 0.85,
                        "reason": f"短消息包含问候词: {word}",
                    }

        # ===== 规则 3：短消息 + 闲聊信号 =====
        if msg_len <= 15:
            for kw in IntentClassifier.CASUAL_SIGNALS:
                if kw in msg_lower:
                    return {
                        "intent": "casual_chat",
                        "confidence": 0.75,
                        "reason": f"短消息闲聊信号: {kw}",
                    }

        # ===== 规则 4：纯表情/表情包 =====
        # 去掉 emoji 和空白后如果为空，判定为闲聊
        text_only = re.sub(
            r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF'
            r'\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF'
            r'\U00002702-\U000027B0\U0001f900-\U0001f9FF'
            r'\U0000fe0f\s]+', '', msg
        )
        if len(text_only) == 0:
            return {
                "intent": "casual_chat",
                "confidence": 0.70,
                "reason": "纯表情消息",
            }

        # ===== 规则 5：超短消息 + 上文感知 =====
        if msg_len <= 4:
            if history_context:
                recent_text = ' '.join(
                    m.get('content', '') for m in history_context[-3:]
                )
                business_context_kw = [
                    '产品', '介绍', '了解', '获客', '服务', '方案',
                    '合作', '报价', '价格', '系统', '营销',
                ]
                for kw in business_context_kw:
                    if kw in recent_text:
                        return {
                            "intent": "business",
                            "confidence": 0.70,
                            "reason": f"短消息但上文含业务词'{kw}'，延续业务意图",
                        }
            return {
                "intent": "casual_chat",
                "confidence": 0.55,
                "reason": f"极短消息({msg_len}字)无上文业务信号",
            }

        # ===== 默认：走业务链路 =====
        return {
            "intent": "business",
            "confidence": 0.50,
            "reason": "未匹配特定意图，默认业务",
        }

    @staticmethod
    def add_industry_keywords(intent: str, keywords: list):
        """动态扩展某个意图的关键词（用于行业适配）

        例如车险行业可以加入：
            IntentClassifier.add_industry_keywords('business', ['车险', '保费', '理赔'])
        """
        target_map = {
            'price_inquiry': IntentClassifier.PRICE_KEYWORDS,
            'material_request': IntentClassifier.MATERIAL_KEYWORDS,
            'negative': IntentClassifier.NEGATIVE_KEYWORDS,
            'business': IntentClassifier.BUSINESS_SIGNALS,
            'casual_chat': IntentClassifier.CASUAL_SIGNALS,
        }
        target = target_map.get(intent)
        if target is not None:
            for kw in keywords:
                if kw not in target:
                    target.append(kw)
            logger.info(f"[意图分类] 扩展 {intent} 关键词: +{len(keywords)} 个")
