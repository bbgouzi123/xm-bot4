"""
AI 回复画像提取器 — 从 AI 回复中解析客户画像标签

解析格式：
  AI 回复正文...
  【画像】{"intent": "意向-强烈", "province": "广东", "vehicle": "私家车"}

职责：
1. 从 AI 回复文本中分离出正文和画像 JSON
2. 解析画像 JSON
3. 调用 ProfileManager 合并到客户档案
"""
import re
import json
import logging
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# 匹配 【画像】{...} 的正则 (支持有无 ```json 包裹)
PROFILE_PATTERN = re.compile(
    r'【画像】\s*(?:```json\n?)?\s*(\{[^}]+\})\s*(?:```)?',
    re.DOTALL,
)

# 备选：匹配 [画像]{...} 或 (画像){...} 或 <CRM_Action> (兼容 xm-bot4)
PROFILE_PATTERN_ALT = re.compile(
    r'(?:[\[【\(<]画像|CRM_Action)[\]】\)>]\s*(?:```json\n?)?\s*(\{[^}]+\})\s*(?:```)?',
    re.DOTALL,
)

# 兜底1：最激进的匹配。即使 AI 忘记输出【画像】，只要它在末尾输出了 ```json {...} ``` 就强行捕获并剥离
AGGRESSIVE_JSON_BLOCK = re.compile(
    r'```json\n?\s*(\{[^}]+\})\s*```',
    re.DOTALL,
)

# 兜底2：匹配末尾的 raw JSON block (例如没有 ```json 的 CRM JSON)
RAW_CRM_JSON_BLOCK = re.compile(
    r'(\{\s*"(?:当前阶段|意向度|intent|sales_stage)"[\s\S]*?\})\s*$',
    re.DOTALL | re.IGNORECASE,
)

def extract_profile_from_reply(reply_text: str) -> Tuple[str, Optional[Dict]]:
    """从 AI 回复中提取画像标签

    Args:
        reply_text: AI 的完整回复文本

    Returns:
        (clean_reply, profile_tags):
        - clean_reply: 去掉画像标记后的纯文本回复
        - profile_tags: 解析出的标签字典，如 {"intent": "意向-强烈"}
                        如果没有画像标记则为 None
    """
    if not reply_text:
        return "", None

    # 0. 优先级：如果整个 reply_text 本身就是一个 JSON 对象
    stripped = reply_text.strip()
    json_clean = stripped
    if json_clean.startswith('```'):
        start_idx = json_clean.find('{')
        end_idx = json_clean.rfind('}')
        if start_idx != -1 and end_idx != -1:
            json_clean = json_clean[start_idx:end_idx+1]
            
    if json_clean.startswith('{') and json_clean.endswith('}'):
        try:
            parsed = json.loads(json_clean)
            if isinstance(parsed, dict):
                reply_val = None
                for k in ["reply", "Reply", "回答", "回复", "回复内容", "content", "content_reply", "聊天原话"]:
                    if k in parsed:
                        reply_val = str(parsed[k])
                        break
                if reply_val is None:
                    for k in parsed.keys():
                        if k.lower() == 'reply':
                            reply_val = str(parsed[k])
                            break
                            
                tags = {}
                for k, v in parsed.items():
                    if k.lower() not in ["reply", "回答", "回复", "回复内容", "content", "content_reply", "聊天原话"]:
                        if isinstance(v, dict):
                            tags.update(v)
                        else:
                            tags[k] = v
                
                if reply_val is not None:
                    logger.info(f"[CRM] 检测到完整 JSON 回复，成功提取 reply: {reply_val} 和 tags: {tags}")
                    return reply_val.strip(), tags
        except Exception as e:
            logger.warning(f"[CRM] 尝试将整个文本解析为 JSON 失败: {e}")

    # 第一优先级：匹配主模式
    match = PROFILE_PATTERN.search(reply_text)
    if not match:
        match = PROFILE_PATTERN_ALT.search(reply_text)
        
    # 第二优先级：尝试兼容老的 CRM_Action XML 格式
    if not match:
        old_crm_match = re.search(r'<CRM_Action>(.*?)</CRM_Action>', reply_text, re.DOTALL)
        if old_crm_match:
            json_str = old_crm_match.group(1).strip()
            if json_str.startswith('{') and json_str.endswith('}'):
                clean_reply = reply_text[:old_crm_match.start()].strip()
                try:
                    profile_tags = json.loads(json_str)
                    return clean_reply, profile_tags
                except Exception:
                    pass

    # 第三优先级：匹配末尾的 raw JSON 块 (例如没有 ```json 的 CRM JSON)
    if not match:
        raw_json_match = RAW_CRM_JSON_BLOCK.search(reply_text)
        if raw_json_match:
            json_str = raw_json_match.group(1)
            clean_reply = reply_text[:raw_json_match.start()].strip()
            try:
                profile_tags = json.loads(json_str)
                logger.info(f"[CRM] 提取到末尾 raw JSON 画像: {profile_tags}")
                return clean_reply, profile_tags
            except Exception:
                pass

    # 第四优先级：最激进的匹配。AI 可能完全漏写了【画像】标识
    if not match:
        agg_match = AGGRESSIVE_JSON_BLOCK.search(reply_text)
        if agg_match:
            json_str = agg_match.group(1)
            # 只有当这个块出现在整个文本的相对后半部分才能认为是画像，防止误伤
            clean_reply = reply_text[:agg_match.start()].strip()
            try:
                profile_tags = json.loads(json_str)
                logger.warning("[CRM] AI 发送的画像缺失标准前缀！已触发强行隔离兜底...")
                return clean_reply, profile_tags
            except Exception:
                pass
        return reply_text.strip(), None

    # 提取 JSON 字符串
    json_str = match.group(1)

    # 清理回复文本（去掉画像标记部分）
    clean_reply = reply_text[:match.start()].strip()

    # 解析 JSON
    try:
        profile_tags = json.loads(json_str)
        if not isinstance(profile_tags, dict):
            logger.warning(f"[CRM] 画像标签不是字典: {json_str}")
            return clean_reply, None

        logger.info(f"[CRM] 提取到画像标签: {profile_tags}")
        return clean_reply, profile_tags

    except json.JSONDecodeError as e:
        logger.warning(f"[CRM] 画像 JSON 解析失败: {e}, raw={json_str}")

        # 尝试修复常见的 JSON 问题
        fixed = _try_fix_json(json_str)
        if fixed:
            logger.info(f"[CRM] JSON 修复成功: {fixed}")
            return clean_reply, fixed

        return clean_reply, None


def _try_fix_json(json_str: str) -> Optional[Dict]:
    """尝试修复常见的 JSON 格式问题

    AI 有时会输出不标准的 JSON：
    - 使用单引号
    - key 没有引号
    - 尾部逗号
    """
    # 修复1: 单引号 → 双引号
    try:
        fixed = json_str.replace("'", '"')
        return json.loads(fixed)
    except Exception:
        pass

    # 修复2: 尾部逗号
    try:
        fixed = re.sub(r',\s*}', '}', json_str)
        fixed = fixed.replace("'", '"')
        return json.loads(fixed)
    except Exception:
        pass

    # 修复3: 提取 key:value 对
    try:
        pairs = re.findall(r'"?(\w+)"?\s*[:：]\s*"([^"]*)"', json_str)
        if pairs:
            return {k: v for k, v in pairs}
    except Exception:
        pass

    return None


def build_chat_context_for_analysis(
    messages: list,
    max_messages: int = 20,
) -> str:
    """构建用于画像分析的聊天上下文

    Args:
        messages: 消息列表 [{"role": "user/assistant", "content": "..."}]
        max_messages: 最多取最近几条

    Returns:
        格式化的聊天记录文本
    """
    recent = messages[-max_messages:] if len(messages) > max_messages else messages
    lines = []
    for msg in recent:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            lines.append(f"客户: {content}")
        else:
            lines.append(f"我方: {content}")
    return "\n".join(lines)


def extract_contact_info_from_message(text: str) -> dict:
    """
    自研启发式正则与规则结合的引擎：
    在聊天收到好友消息时，实时同步提取包含的电话、微信、QQ、邮箱、收货地址等关键 CRM 线索。
    """
    if not text:
        return {}

    results = {}

    # 1. 手机/电话号码匹配 (防汉字与数字相邻的 \b 词边界失效)
    phone_match = re.search(r'(?<!\d)(1[3-9]\d{9})(?!\d)', text)
    if phone_match:
        results["phone"] = phone_match.group(1)

    # 2. QQ号匹配 (支持各种连接符，如“QQ号是”、“QQ号：”、“QQ为”等)
    qq_match = re.search(r'(?i)qq(?:号|是|为|：|:|的为|的|\s)*([1-9]\d{4,10})(?!\d)', text)
    if qq_match:
        results["qq"] = qq_match.group(1)

    # 3. 邮箱匹配 (防 \b 在中文标点与英文连接处失效)
    email_match = re.search(r'(?opt)(?<![a-zA-Z0-9._%+-])([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?![a-zA-Z])', text) if '(?opt)' in text else re.search(r'(?<![a-zA-Z0-9._%+-])([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?![a-zA-Z])', text)
    if email_match:
        results["email"] = email_match.group(1)

    # 4. 微信号匹配 (支持各种连接符，如“微信是”、“微信：”、“加我wx:”等)
    wx_match = re.search(r'(?i)(?:微信|wx|v信|加我)(?:号|是|为|：|:|的为|的|\s)*([a-zA-Z][a-zA-Z0-9_-]{5,19})(?![a-zA-Z0-9_-])', text)
    if wx_match:
        results["wechat"] = wx_match.group(1)

    # 5. 地址提取 (匹配显式地址指示词或明显的省市区三级结构)
    addr_match = None
    # 场景 A: 显式指示词
    addr_prefix_match = re.search(r'(?:地址|住址|寄到|送货到|发货到|收货地址)\s*[:：\s]*([^\s,，。！!]{5,80})', text)
    if addr_prefix_match:
        addr_match = addr_prefix_match.group(1).strip()
    else:
        # 场景 B: 包含省/市/区/县三级词汇 (限制行政区划字数，如 1-3 字省、1-4 字市，彻底切断前面贪婪捕获“我住在”的漏洞)
        geo_structure_match = re.search(r'((?:[\u4e00-\u9fa5]{1,3}省)?(?:[\u4e00-\u9fa5]{1,4}市)(?:[\u4e00-\u9fa5]{1,4}[区县市])(?:[\u4e00-\u9fa50-9A-Za-z#_-]{3,60}))', text)
        if geo_structure_match:
            addr_match = geo_structure_match.group(1).strip()

    if addr_match:
        # 清洗头部杂质字词，如 “在浙江省...” 或 “是浙江省...”
        addr_match = re.sub(r'^[在是于：:，,\s]+', '', addr_match)
        # 过滤过短套话
        if len(addr_match) >= 5:
            results["address"] = addr_match

    return results
