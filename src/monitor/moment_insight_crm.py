"""
朋友圈 Coze 双输出解析 + CRM 画像写入

Coze bot 设计了「评论文字 + JSON 画像」双输出格式，本模块负责：
- 分离评论文字与画像 JSON
- 将画像 JSON 字段异步写入 CRM（ProfileManager）
"""
import re
import json as _json
import logging
import threading

logger = logging.getLogger(__name__)


def parse_resilient_json(s: str) -> dict:
    s = s.strip()
    if not s:
        return {}
    
    # 1. 尝试标准解析
    try:
        return _json.loads(s)
    except Exception:
        pass
        
    # 2. 尝试使用括号平衡栈进行修复
    try:
        in_string = False
        escape = False
        stack = []
        repaired = []
        for i, char in enumerate(s):
            repaired.append(char)
            if escape:
                escape = False
                continue
            if char == '\\':
                if in_string:
                    escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if not in_string:
                if char == '{':
                    stack.append('}')
                elif char == '[':
                    stack.append(']')
                elif char == '}':
                    if stack and stack[-1] == '}':
                        stack.pop()
                elif char == ']':
                    if stack and stack[-1] == ']':
                        stack.pop()
        if in_string:
            repaired.append('"')
        while stack:
            repaired.append(stack.pop())
        repaired_str = "".join(repaired)
        try:
            return _json.loads(repaired_str)
        except Exception:
            pass
    except Exception:
        pass
        
    # 3. 兜底策略：使用正则表达式提取键值对
    try:
        out = {}
        # 匹配 "key": "value" 结构（可能没有闭合的引号）
        pairs = re.findall(r'"([^"]+)"\s*:\s*"([^"]*)"?', s)
        for k, v in pairs:
            out[k] = v
        return out
    except Exception:
        return {}


def parse_comment_and_insight(text: str):
    """从 Coze 双输出中分离「评论文字」和「画像 JSON」。

    Coze bot 输出格式示例：
      太辛苦了，整个省力方法不好吗

      {
        "当前阶段": "破冰",
        "客户心理": "...",
        "建议策略": "..."
      }

    Returns:
        (comment_text: str, insight: dict)
    """
    if not text:
        return "", {}

    text = text.strip()
    
    # 寻找 JSON 的起始位置
    start_idx = text.find('{')
    if start_idx == -1:
        # 没有 JSON 数据，全部作为评论
        comment_raw = text
        insight = {}
    else:
        # 认为 '{' 之后的所有内容都是 JSON block
        comment_raw = text[:start_idx].strip()
        json_block = text[start_idx:]
        insight = parse_resilient_json(json_block)

    # 清理评论文字
    comment_raw = re.sub(r'```[\s\S]*?```', '', comment_raw).strip()
    comment_raw = re.sub(r'`[^`]+`', '', comment_raw).strip()
    comment_raw = re.sub(r'^[-=]{3,}$', '', comment_raw, flags=re.MULTILINE).strip()
    comment_raw = comment_raw.strip('"\'\u300c\u300d\u201c\u201d\u2018\u2019')

    if insight:
        logger.info(f"[智能评论] 解析到画像 JSON: {list(insight.keys())}")

    return comment_raw.strip(), insight


def update_crm_from_insight(manager, author_name: str, account_id: str, insight: dict):
    """将 Coze 输出的朋友圈画像 JSON 异步写入 CRM。

    字段映射（Coze bot 输出 → CRM 标签体系）：
      "当前阶段"       → sales_stage 标签
      "购买意向/意向"  → intent 标签
      "兴趣点/痛点"    → interest 标签
      "客户心理/心态"  → profile.notes 追加
      "建议策略/下一步"→ profile.notes 追加
    """
    def _do():
        try:
            from src.crm.profile_manager import ProfileManager
            from src.crm.account_data import get_active_account
            bot_wxid = (getattr(getattr(manager, 'driver', None), 'bot_wxid', None)
                        or get_active_account())
            pm = ProfileManager(account_id=bot_wxid)

            # 用昵称查找画像（朋友圈只有昵称）
            profile = next(
                (p for p in pm.get_all_profiles() if p.nickname == author_name),
                None
            )
            if profile is None:
                profile = pm.get_profile(f"nick_{author_name}", nickname=author_name)

            updated = False
            ai_tags = {}

            for key in ("当前阶段", "阶段", "stage"):
                if v := insight.get(key):
                    ai_tags["sales_stage"] = str(v)
                    updated = True
                    break

            for key in ("购买意向", "意向", "intent"):
                if v := insight.get(key):
                    ai_tags["intent"] = str(v)
                    updated = True
                    break

            for key in ("兴趣点", "痛点", "interest"):
                if v := insight.get(key):
                    ai_tags["interest"] = str(v)
                    updated = True
                    break

            if ai_tags:
                pm.update_from_ai_tags(profile.wxid, ai_tags, source="moment")

            # 追加心理/策略到备注
            mindset = insight.get("客户心理") or insight.get("客户心态") or insight.get("mindset")
            strategy = insight.get("建议策略") or insight.get("下一步") or insight.get("strategy")
            parts = []
            if mindset:
                parts.append(f"心理: {mindset}")
            if strategy:
                parts.append(f"策略: {strategy}")
            if parts:
                from datetime import datetime
                note = f"[朋友圈·{datetime.now().strftime('%m-%d')}] " + " | ".join(parts)
                profile.notes.append(note)
                pm.save_profile(profile)
                updated = True

            if updated:
                logger.info(f"[CRM·朋友圈] ✅ {author_name} 画像已更新: {list(insight.keys())}")

        except Exception as e:
            logger.debug(f"[CRM·朋友圈] 画像更新失败 ({author_name}): {e}")

    threading.Thread(target=_do, daemon=True,
                     name=f"crm-insight-{author_name[:8]}").start()
