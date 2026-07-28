"""
知识提取器 — 从原始微信聊天记录中提取 Q&A 知识对

核心逻辑：
1. 过滤无文本价值的消息（图片/语音/系统等）
2. 将客户消息配对为 Question，紧跟的销售回复配对为 Answer
3. 连续多条己方消息合并为完整 Answer
4. 对每对 Q&A 进行质量评分
5. 自动打标签（价格咨询/产品对比/售后等）
"""
import re
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# 无文本价值的消息类型，直接跳过
SKIP_TYPES = {'system', 'greet', 'recall', 'time', 'image', 'voice', 'video', 'file', 'card'}

# 无价值消息内容正则（空壳/占位）
SKIP_CONTENT_PATTERNS = [
    re.compile(r'^\[图片\]$'),
    re.compile(r'^\[语音\]$'),
    re.compile(r'^\[视频\]$'),
    re.compile(r'^\[文件\]$'),
    re.compile(r'^\[名片\]$'),
    re.compile(r'^\[表情\]$'),
    re.compile(r'^\[链接\]$'),
    re.compile(r'^\[小程序\]$'),
    re.compile(r'^\[聊天记录\]$'),
    re.compile(r'^\[红包\]'),
    re.compile(r'^\[转账\]'),
    re.compile(r'^\[撤回\]$'),
    re.compile(r'^\[打招呼\]$'),
    re.compile(r'^你已添加了'),
    re.compile(r'^以上是打招呼的消息'),
]

# 标签识别关键词映射
TAG_KEYWORDS = {
    "价格咨询": ["多少钱", "价格", "报价", "费用", "收费", "优惠", "折扣", "划算", "贵不贵", "便宜"],
    "产品对比": ["对比", "比较", "区别", "差别", "哪个好", "推荐", "选择", "建议"],
    "售后问题": ["售后", "保修", "退款", "退货", "投诉", "问题", "坏了", "维修"],
    "功能咨询": ["功能", "能不能", "可以", "支持", "怎么用", "操作", "使用"],
    "成交意向": ["下单", "购买", "买", "开通", "怎么付", "支付", "订单"],
    "信任建立": ["靠谱", "正规", "资质", "案例", "效果", "评价", "口碑"],
    "异议处理": ["不需要", "再看看", "考虑", "算了", "太贵", "不合适"],
}

# 质量评分参数
MIN_ANSWER_LENGTH = 4      # 答案最低字数
IDEAL_ANSWER_MIN = 20      # 理想答案长度下限
IDEAL_ANSWER_MAX = 300     # 理想答案长度上限
MIN_QUESTION_LENGTH = 2    # 问题最低字数


class KnowledgeExtractor:
    """从微信聊天记录中提取 Q&A 知识对"""

    def __init__(self, dedup: bool = True):
        """
        Args:
            dedup: 是否对 Q&A 对去重（基于 question 文本去重）
        """
        self._dedup = dedup
        self._seen_questions = set()

    def extract_qa_pairs(self, messages: List[Dict]) -> List[Dict]:
        """将消息列表转换为 Q&A 知识对

        Args:
            messages: 聊天消息列表，每条需包含:
                - content: str  消息内容
                - isSelf: bool  是否自己发的
                - type: str     消息类型 (可选)

        Returns:
            Q&A 知识对列表 [{ question, answer, context, tags, quality_score }]
        """
        # 第 1 步：过滤无价值消息
        filtered = self._filter_messages(messages)

        if len(filtered) < 2:
            return []

        # 第 2 步：构建 Q&A 配对
        qa_pairs = self._pair_qa(filtered)

        # 第 3 步：质量评分 + 打标签
        for qa in qa_pairs:
            qa["quality_score"] = self.score_quality(qa["question"], qa["answer"])
            qa["tags"] = self.detect_tags(qa["question"], qa["answer"])

        # 第 4 步：过滤低质量
        qa_pairs = [qa for qa in qa_pairs if qa["quality_score"] >= 0.2]

        # 第 5 步：去重
        if self._dedup:
            qa_pairs = self._deduplicate(qa_pairs)

        # 按质量分排序
        qa_pairs.sort(key=lambda x: x["quality_score"], reverse=True)

        logger.info(
            f"[知识提取] 原始 {len(messages)} 条消息 → "
            f"过滤后 {len(filtered)} 条 → "
            f"提取 {len(qa_pairs)} 对 Q&A"
        )

        return qa_pairs

    def _filter_messages(self, messages: List[Dict]) -> List[Dict]:
        """过滤无价值的消息"""
        result = []
        for msg in messages:
            msg_type = msg.get("type", "text")
            content = (msg.get("content") or "").strip()

            # 跳过特殊类型
            if msg_type in SKIP_TYPES:
                continue

            # 跳过空消息
            if not content:
                continue

            # 跳过模式匹配到的无价值消息
            if any(pat.match(content) for pat in SKIP_CONTENT_PATTERNS):
                continue

            result.append(msg)

        return result

    def _pair_qa(self, messages: List[Dict]) -> List[Dict]:
        """将消息流配对为 Q&A 对

        规则：
        - 非自己发的消息 → Question 候选
        - 紧跟着自己发的消息 → Answer 候选
        - 多条连续的己方消息合并为一个 Answer
        - 记录 context（问题前一条 + 答案后一条）
        """
        pairs = []
        i = 0
        n = len(messages)

        while i < n:
            # 寻找客户消息（非自己发的）
            if messages[i].get("isSelf"):
                i += 1
                continue

            # 收集连续的客户消息作为完整 Question
            question_parts = []
            q_start = i
            while i < n and not messages[i].get("isSelf"):
                question_parts.append(messages[i].get("content", "").strip())
                i += 1

            if not question_parts:
                continue

            question = "\n".join(question_parts)

            # 收集紧跟的己方消息作为 Answer
            answer_parts = []
            while i < n and messages[i].get("isSelf"):
                answer_parts.append(messages[i].get("content", "").strip())
                i += 1

            if not answer_parts:
                continue

            answer = "\n".join(answer_parts)

            # 构建上下文（前后各一条消息）
            context_parts = []
            if q_start > 0:
                prev = messages[q_start - 1].get("content", "")
                who = "我" if messages[q_start - 1].get("isSelf") else "对方"
                context_parts.append(f"[前]{who}：{prev}")
            if i < n:
                nxt = messages[i].get("content", "")
                who = "我" if messages[i].get("isSelf") else "对方"
                context_parts.append(f"[后]{who}：{nxt}")

            pairs.append({
                "question": question,
                "answer": answer,
                "context": " | ".join(context_parts),
            })

        return pairs

    @staticmethod
    def score_quality(question: str, answer: str) -> float:
        """评估 Q&A 对质量，返回 0-1 分数

        高分因素：
        - 答案长度适中 (20-300 字)
        - 问题有足够长度
        - 答案包含专业术语/数字/价格
        - 不是纯表情/纯标点
        """
        score = 0.0
        q_len = len(question.strip())
        a_len = len(answer.strip())

        # 基础分：问题和答案都有实质内容
        if q_len >= MIN_QUESTION_LENGTH and a_len >= MIN_ANSWER_LENGTH:
            score += 0.3

        # 答案长度评分
        if IDEAL_ANSWER_MIN <= a_len <= IDEAL_ANSWER_MAX:
            score += 0.3  # 理想长度
        elif a_len > IDEAL_ANSWER_MAX:
            score += 0.15  # 太长，可能是复制粘贴
        elif a_len >= MIN_ANSWER_LENGTH:
            score += 0.1  # 有但偏短

        # 专业度加分：包含数字/价格/专业术语
        if re.search(r'\d+', answer):
            score += 0.1  # 含数字
        if re.search(r'[元¥￥%]', answer):
            score += 0.1  # 含价格/百分比
        professional_terms = ["方案", "定制", "服务", "保障", "流程", "效果", "案例", "优势"]
        if any(t in answer for t in professional_terms):
            score += 0.1

        # 减分项
        # 纯表情/标点
        clean = re.sub(r'[\s\W]', '', answer)
        if not clean or len(clean) < 2:
            score -= 0.5

        # 太短的问题（可能是"嗯""哦"之类）
        clean_q = re.sub(r'[\s\W]', '', question)
        if len(clean_q) < 2:
            score -= 0.3

        return max(0.0, min(1.0, score))

    @staticmethod
    def detect_tags(question: str, answer: str) -> List[str]:
        """自动检测对话标签"""
        tags = []
        combined = question + " " + answer

        for tag_name, keywords in TAG_KEYWORDS.items():
            if any(kw in combined for kw in keywords):
                tags.append(tag_name)

        return tags[:5]  # 最多 5 个标签

    def _deduplicate(self, pairs: List[Dict]) -> List[Dict]:
        """基于问题内容去重"""
        result = []
        for qa in pairs:
            q_key = re.sub(r'\s+', '', qa["question"])[:50]  # 取前50字做去重键
            if q_key not in self._seen_questions:
                self._seen_questions.add(q_key)
                result.append(qa)
        return result

    @staticmethod
    def desensitize(text: str) -> str:
        """脱敏处理：屏蔽手机号、微信号等敏感信息"""
        # 手机号脱敏
        text = re.sub(r'1[3-9]\d{9}', lambda m: m.group()[:3] + '****' + m.group()[-4:], text)
        # 身份证号脱敏
        text = re.sub(r'\d{17}[\dXx]', lambda m: m.group()[:6] + '********' + m.group()[-4:], text)
        # 银行卡号脱敏
        text = re.sub(r'\d{16,19}', lambda m: m.group()[:4] + '****' + m.group()[-4:] if len(m.group()) >= 16 else m.group(), text)
        return text
