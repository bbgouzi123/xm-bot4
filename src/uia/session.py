"""
会话名称解析 v3 + xm-bot4 精确解析整合
"""
import re
import time as _time
import hashlib
from datetime import datetime, timedelta
from typing import Optional


# 微信系统账号 / 特殊会话
SYSTEM_ACCOUNTS = {"文件传输助手", "微信团队", "服务通知", "公众号", "服务号"}

def is_group_msg_format(msg: str) -> bool:
    """高精度群聊消息格式特征检测：防止普通私聊发冒号导致误判"""
    if not msg:
        return False
    # 匹配微信群聊消息前缀：非空白、非冒号、非顿号的字符（1-30位） + 英文冒号 + 空格
    return bool(re.match(r'^[^\s:：、]{1,30}:\s', msg))

def clean_session_name(name: str) -> str:
    """清理微信会话名称中的小尾巴标记（置顶、免打扰、未读数、有人@我等），保证会话名称绝对纯净"""
    if not name:
        return ""
    s = name
    
    # 微信 4.1.x 的换行格式直接由 parse_session_name 在外层精确切分处理，不需要在这里进行容易误杀的子串剔除。
    # 这里主要服务于旧版单行拼凑的名字剔除，使用严格的空格边界，避免误杀正常人名（如“消息免打扰群”）
    s = re.sub(r'(?:\s+|^)(已置顶|消息免打扰|\[有人@我\])(?:\s+|$)', ' ', s)
    s = re.sub(r'(?:\s+|^)\d+条未读(?:\s+|$)', ' ', s)
    s = re.sub(r'(?:\s+|^)\[\d+条\](?:\s+|$)', ' ', s)
    s = re.sub(r'(?:\s+|^)\d+条新消息(?:\s+|$)', ' ', s)
    s = re.sub(r'(?:\s+|^)(?:\.\.\.|99\+)(?:\s+|$)', ' ', s)
    
    return s.strip()

# 消息类型标记
MSG_TYPE_MARKS = ('[图片]', '[视频]', '[链接]', '[文件]', '[语音]', '[名片]',
                  '[表情]', '[位置]', '[转账]', '[红包]', '[拍一拍]',
                  '[音乐]', '[小程序]', '[聊天记录]')

import os
import json
from pathlib import Path

from .session_cache import SessionTypeCache, session_type_cache


def _strip_wechat_time(text: str) -> tuple:
    """从末尾剥离微信时间，返回 (清理后的文本, 提取出的时间)"""
    text = text.strip()
    
    # 各种常见微信时间格式的正则
    patterns = [
        # 1. 昨天 15:44, 昨天, 前天 12:00
        r'\s*(昨天|前天)(?:\s+\d{1,2}:\d{2})?$',
        # 2. 星期一 15:44, 星期一, 周一
        r'\s*((?:星期|周)[一二三四五六日天])(?:\s+\d{1,2}:\d{2})?$',
        # 3. 上午 12:34, 下午 2:30 等
        r'\s*((?:上午|下午|中午|凌晨|晚上|半夜)\s*\d{1,2}:\d{2})$',
        # 4. 年/月/日 格式，如 24/12/31, 2024/12/31, 12/31
        r'\s*(\d{2,4}/\d{1,2}/\d{1,2}(?:\s+\d{1,2}:\d{2})?)$',
        # 5. 12月31日
        r'\s*(\d{1,2}月\d{1,2}日(?:\s+\d{1,2}:\d{2})?)$',
        # 6. 单纯的 HH:MM
        r'\s*(\d{1,2}:\d{2})$',
    ]
    
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            time_disp = m.group(1) if m.groups() and m.group(1) else m.group(0).strip()
            cleaned = text[:m.start()].strip()
            return cleaned, time_disp
            
    return text, ""


def parse_session_name(raw: str, real_name: Optional[str] = None) -> Optional[dict]:
    """
    解析微信会话列表项 UIA Name 属性的顶级核心入口
    支持双轨解析：
    - 轨道一：换行结构化拆分（微信 4.1.x 的现代排版，精准度 100%，杜绝泰文或带空格名字截断）
    - 轨道二：单行模糊正则剥离（兼容旧版本或辅助场景的单行拼凑，使用安全边界正则防误杀）
    """
    if not raw or not raw.strip():
        return None

    # ========== 轨道一：换行结构化拆分 ==========
    if "\n" in raw:
        raw_lines = [l.strip() for l in raw.split("\n") if l.strip()]
        if not raw_lines:
            return None

        # 名字必定是第一行，完全干净原始
        session_name = raw_lines[0]

        is_pinned = False
        is_muted = False
        is_at = False
        unread = 0

        # 识别及提取状态标记行
        status_indices = set()
        for idx in range(1, len(raw_lines)):
            line = raw_lines[idx]
            if line == "已置顶":
                is_pinned = True
                status_indices.add(idx)
                continue
            if line == "消息免打扰":
                is_muted = True
                status_indices.add(idx)
                continue
            if "有人@我" in line:
                is_at = True
                # 只有当这一行单纯是状态标签时，才移出消息内容，否则保留作为消息文本
                if line.strip() in ("[有人@我]", "有人@我", "有人 @我"):
                    status_indices.add(idx)
                continue

            # 未读条数匹配
            if line in ("...", "99+"):
                unread = 99
                status_indices.add(idx)
                continue

            m1 = re.search(r'(\d+)\+?条未读', line)
            m2 = re.search(r'\[(\d+)\+?条\]', line)
            m3 = re.search(r'(\d+)\+?条新消息', line)
            if m1:
                unread = int(m1.group(1))
                status_indices.add(idx)
            elif m2:
                unread = int(m2.group(1))
                status_indices.add(idx)
            elif m3:
                unread = int(m3.group(1))
                status_indices.add(idx)

        # 收集剩余非状态非标志的消息/时间行
        content_lines = []
        for idx in range(1, len(raw_lines)):
            if idx not in status_indices:
                content_lines.append(raw_lines[idx])

        last_time = ""
        last_message = ""

        if content_lines:
            # 判断最后一行是否是时间描述
            last_item = content_lines[-1]
            is_time = False
            if re.search(r'\d{1,2}:\d{2}', last_item):
                is_time = True
            elif any(kw in last_item for kw in ["昨天", "前天", "星期", "周", "下午", "上午"]):
                is_time = True
            elif re.match(r'^\d{4}年\d{1,2}月\d{1,2}日$', last_item) or re.match(r'^\d{1,2}-\d{1,2}$', last_item):
                is_time = True

            if is_time:
                last_time = last_item
                last_message = " ".join(content_lines[:-1])
            else:
                last_message = " ".join(content_lines)

        session_name = clean_session_name(session_name)
        last_message = last_message.strip()

        if not session_name:
            return None

        # 判定是否群聊与公众号
        cached_type = session_type_cache.get_type(session_name)
        if cached_type:
            is_group = (cached_type == "group")
            is_official = (cached_type == "official_account")
        else:
            is_group = (
                ('群' in session_name and len(session_name) > 2) or
                session_name in ('公众号', '服务号') or
                is_group_msg_format(last_message) or
                '、' in session_name
            )
            is_official = session_name in ('公众号', '服务号')
            if session_name in SYSTEM_ACCOUNTS:
                is_official = True

        session_id = int(hashlib.md5(session_name.encode()).hexdigest()[:8], 16)

        return {
            "id": session_id,
            "name": session_name,
            "lastTime": last_time,
            "lastMessage": last_message,
            "unread": unread,
            "isGroup": is_group,
            "isPinned": is_pinned,
            "isMuted": is_muted,
            "isAt": is_at,
            "isOfficial": is_official,
            "avatar": "",
        }

    # ========== 轨道二：单行模糊正则剥离 (Legacy) ==========
    text = raw.strip()

    is_pinned = "已置顶" in text
    is_muted = "消息免打扰" in text
    is_at = "[有人@我]" in text
    
    # 状态剔除时使用安全空格/边界限制，杜绝误杀名字
    text = re.sub(r'(?:\s+|^)(已置顶|消息免打扰|\[有人@我\])(?:\s+|$)', ' ', text)

    unread = 0
    m_dots = re.search(r'(?:\s+|^)(\.\.\.|99\+)(?:\s+|$)', text)
    if m_dots:
        unread = 99
        text = text[:m_dots.start()] + " " + text[m_dots.end():]

    m1 = re.search(r'(\d+)条未读', text)
    m2 = re.search(r'\[(\d+)条\]', text)
    m3 = re.search(r'(\d+)条新消息', text)
    
    if m1:
        unread = int(m1.group(1))
        text = text[:m1.start()] + text[m1.end():]
    elif m2:
        unread = int(m2.group(1))
        text = text[:m2.start()] + text[m2.end():]
    elif m3:
        unread = int(m3.group(1))
        text = text[:m3.start()] + text[m3.end():]

    text = re.sub(r'\s*\[\d+条\]\s*', ' ', text)
    text = re.sub(r'\s*\d+条未读\s*', ' ', text)
    text = re.sub(r'\s*\d+条新消息\s*', ' ', text)
    text = re.sub(r'\s*(?:\.\.\.|99\+)\s*', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    if not text:
        return None

    # 3. 提取时间（从末尾搜索，使用增强的剥离函数）
    text, last_time = _strip_wechat_time(text)

    # 4. 分离出会话名称 and 消息摘要
    session_name = ""
    last_message = ""

    # 4a. 如果指定了 real_name，优先进行直接匹配以处理空格名字
    if real_name:
        real_name_clean = real_name.strip()
        if text == real_name_clean:
            session_name = real_name_clean
            last_message = ""
        else:
            # 💡 【高鲁棒性匹配】只要 text 以期望的 real_name 开头，且后续紧跟空白字符、冒号、中括号标记或直接结束，即视为成功匹配
            pat = r'^' + re.escape(real_name_clean) + r'(?:\s+|\[|：|:|$)'
            if re.match(pat, text):
                session_name = real_name_clean
                last_message = text[len(real_name_clean):].strip()

    # 4b. 若未匹配，尝试使用 contacts_cache 中的已知好友/群聊名匹配
    if not session_name:
        try:
            from src.crm.account_data import get_active_account
            from src.utils.contacts_cache import contacts_cache
            active_aid = get_active_account()
            known_names = []
            if active_aid:
                friends = contacts_cache.get_friends(active_aid)
                groups = contacts_cache.get_groups(active_aid)
                for f in friends:
                    n = (f.get("name") or "").strip()
                    r = (f.get("remark") or "").strip()
                    if n: known_names.append(n)
                    if r: known_names.append(r)
                for g in groups:
                    n = (g.get("name") or "").strip()
                    if n: known_names.append(n)
            
            # 去重并按长度降序，保证最长前缀优先匹配
            known_names = sorted(list(set(known_names) - {""}), key=len, reverse=True)
            for kn in known_names:
                if text == kn:
                    session_name = kn
                    last_message = ""
                    break
                elif text.startswith(kn + " "):
                    session_name = kn
                    last_message = text[len(kn):].strip()
                    break
        except Exception:
            pass

    # 4c. 前面均未匹配时，回退到原有逻辑进行拆分
    if not session_name:
        earliest_mark = len(text)
        for mark in MSG_TYPE_MARKS:
            idx = text.find(mark)
            if idx != -1 and idx < earliest_mark:
                earliest_mark = idx

        if earliest_mark < len(text):
            # 消息标记前面是名字
            session_name = text[:earliest_mark].strip()
            last_message = text[earliest_mark:].strip()
        else:
            # 没有消息标记，检查 "发送者: 消息" 模式（群聊）
            colon_match = re.search(r'\s(\S+):\s', text)
            if colon_match:
                pos = colon_match.start()
                before = text[:pos].rstrip()
                space_idx = before.rfind(' ')
                if space_idx > 0:
                    session_name = before[:space_idx].strip()
                    last_message = text[space_idx:].strip()
                else:
                    session_name = before
                    last_message = text[pos:].strip()
            else:
                # 纯文本消息：名字 消息内容
                parts = text.split(' ', 1)
                if len(parts) == 2:
                    session_name = parts[0]
                    last_message = parts[1]
                else:
                    session_name = text
                    last_message = ""

    # 清理
    session_name = clean_session_name(session_name)
    last_message = last_message.strip()

    if not session_name:
        return None

    # 判断是否群聊与公众号
    cached_type = session_type_cache.get_type(session_name)
    if cached_type:
        is_group = (cached_type == "group")
        is_official = (cached_type == "official_account")
    else:
        is_group = (
            ('群' in session_name and len(session_name) > 2) or
            session_name in ('公众号', '服务号') or
            is_group_msg_format(last_message) or
            '、' in session_name
        )
        is_official = session_name in ('公众号', '服务号')
        # NOTE: Do NOT infer official_account from contacts-cache absence here.
        # Cold-start causes false positives (friends not loaded yet -> wrongly flagged).
        # Only detect_and_cache_session_type (UIA 'gongzhonghao zhuye' button check)
        # should authoritatively mark official_account and write to cache.
        if session_name in SYSTEM_ACCOUNTS:
            is_official = True

    # 6. 生成稳定 ID
    session_id = int(hashlib.md5(session_name.encode()).hexdigest()[:8], 16)

    return {
        "id": session_id,
        "name": session_name,
        "lastTime": last_time,
        "lastMessage": last_message,
        "unread": unread,
        "isGroup": is_group,
        "isPinned": is_pinned,
        "isMuted": is_muted,
        "isAt": is_at,
        "isOfficial": is_official,
        "avatar": "",
    }

