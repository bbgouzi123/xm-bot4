import re
import time
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# 强业务核心词以及数据指标正则
_HIGH_VALUE_PATTERNS = [
    re.compile(r'\d+(?:\.\d+)?\s*(?:斤|kg|公斤|g|克|元|块|%|折)', re.IGNORECASE), # 数字+单位
    re.compile(r'(?:食谱|减肥|减脂|减重|躺瘦|配方|食谱|价格|多少钱|怎么卖|联系方式|电话|手机|地址|微信|wxid)', re.IGNORECASE) # 强业务词
]

def extract_long_term_background_memories(
    db_msgs: List[dict], 
    nicknames_to_check: List[str], 
    short_term_count: int = 15,
    max_seconds: float = 7200.0
) -> List[dict]:
    """
    【两小时长周期记忆召回】
    从 WCDB 历史记录中检索 2 小时（7200秒）内的所有群消息，
    筛选出被冲刷掉的高价值核心信息（@我、数据指标、强业务词），
    将其格式化为 Long-term memory 背景记忆，插入至 AI 上下文前部。
    """
    if not db_msgs or len(db_msgs) <= short_term_count:
        return []

    # 1. 分离短期记忆（最后 15 条不参与长期记忆的召回去重）
    # db_msgs 是从新到旧的顺序（最新在 db_msgs[0]）
    short_term = db_msgs[:short_term_count]
    longer_term = db_msgs[short_term_count:]

    now_ts = time.time()
    recalled_mems = []
    
    # 2. 遍历较久的历史消息（在 2 小时限制以内）
    for m in longer_term:
        created_at = m.get("created_at")
        ts_val = m.get("timestamp")
        if not created_at and ts_val:
            try:
                created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts_val)))
            except Exception:
                pass
        if not created_at:
            continue
            
        # 计算时间差
        try:
            from src.utils.chat_history import parse_time_to_ts
            ts = parse_time_to_ts(created_at)
            if ts > 0 and (now_ts - ts) > max_seconds:
                # 超过两小时，直接截断不再往前找
                break
        except Exception:
            pass

        content = m.get("content", "").strip()
        if not content:
            continue

        # 3. 提取实际消息正文（去除群聊 wxid 前缀）
        actual_body = content
        sender_wxid = ""
        m_sender = re.match(r"^([a-zA-Z0-9_\-]+):\s*\n(.*)$", content, re.DOTALL)
        if m_sender:
            sender_wxid = m_sender.group(1)
            actual_body = m_sender.group(2).strip()

        # 4. 高价值特征判定
        is_high_value = False
        
        # 特征一：消息是 @机器人 / @我
        for n in nicknames_to_check:
            if n and re.search(rf'@[\s\u2005]*{re.escape(n)}', actual_body, re.IGNORECASE):
                is_high_value = True
                break
                
        # 特征二：匹配指标数据或核心业务词
        if not is_high_value:
            for pattern in _HIGH_VALUE_PATTERNS:
                if pattern.search(actual_body):
                    is_high_value = True
                    break

        if is_high_value:
            # 找到发送人名字，如果是数据库格式则解析
            sender_name = m.get("sender") or sender_wxid or "群成员"
            if sender_name.startswith("wxid_"):
                try:
                    from src.utils.contacts_cache import contacts_cache
                    friends = contacts_cache.get_friends(m.get("account_id") or "default") or []
                    for f in friends:
                        if f.get("wxid") == sender_name:
                            sender_name = f.get("name") or f.get("remark") or f.get("nickname") or sender_name
                            break
                except Exception:
                    pass

            # 剔除消息内部的 @ 字符，保持语义纯净
            clean_body = re.sub(r'@[\s\u2005\xa0]*[^\s\u2005\xa0]+', '', actual_body).strip()
            
            # 转为 AI 友好的长效记忆对象，role 为 system 以免 AI 误认为是最新一轮对话
            recalled_mems.insert(0, {
                "role": "system",
                "content": f"[时间前置记忆 (大约在 {created_at})] 【{sender_name}】提到了关键信息: \"{clean_body}\"",
                "sender": "System_Memory",
                "time": created_at
            })

    # 最多保留 5 条最长期的核心记忆，防止撑大 token
    return recalled_mems[:5]

async def get_long_term_context_msgs(engine: Any, name: str, wxid: str, is_group: bool, account_id: str, context_msgs: list) -> list:
    """
    《封装》从 WCDB 中检索 2 小时内的历史聊天背景，并将其合并注入进当前的 context_msgs
    公劤和私聊均支持：公劤最多周期 120 条、私聊 30 条；注入条数公劤至多 5 条、私聊至多 3 条。
    """
    # 参数按类型分层：公劤用大窗口，私聊用小窗口（防止 token 过长）
    limit = 120 if is_group else 30
    short_term_count = 20 if is_group else 12
    max_recalled = 5 if is_group else 3
        
    try:
        session_monitor = getattr(engine, "_wcdb_session_monitor", None)
        target_wxid = wxid or name
        db_msgs = []
        if session_monitor and session_monitor.is_active():
            db_msgs = session_monitor.get_latest_messages(target_wxid, limit=limit)
        else:
            from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
            monitor = get_wcdb_monitor(account_id or 'default')
            if monitor and monitor.is_active():
                db_msgs = monitor.get_latest_messages(target_wxid, limit=limit)
        
        if db_msgs:
            from src.api.config_api import _load_configs
            configs = _load_configs() or {}
            bot_wxid = account_id or getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
            nicknames_to_check = [n for n in [
                getattr(engine.driver, '_nickname', ''),
                bot_wxid,
                configs.get("bot_name", "")
            ] if n]
            if is_group:
                try:
                    from src.utils.contacts_cache import contacts_cache
                    members = contacts_cache.get_group_members(bot_wxid, name) or []
                    for m in members:
                        if m.get("wxid") == bot_wxid:
                            group_card = (m.get("display_name") or m.get("nickname") or "").strip()
                            if group_card and group_card not in nicknames_to_check:
                                nicknames_to_check.append(group_card)
                            break
                except Exception:
                    pass
            
            long_mems = extract_long_term_background_memories(
                db_msgs, nicknames_to_check,
                short_term_count=short_term_count
            )
            # 按类型限制注入最大数量
            long_mems = long_mems[:max_recalled]
            if long_mems:
                context_msgs = long_mems + context_msgs
                scope = '公劤' if is_group else '私聊'
                logger.info(f"[长记忆] 成功为{scope}会话 '{name}' 召回并注入 {len(long_mems)} 条 2 小时内的核心历史背景")

            # 【履历冷启动销销】如果是私聊且 context_msgs 为空（初次接管）且 WCDB 有历史数据
            # 则异步分析接管前历史，让 AI 不一条回复起就拥有前任的记忆
            if not is_group and not context_msgs and db_msgs and len(db_msgs) >= 8:
                try:
                    from src.crm.auto_analyser import trigger_history_bootstrap_from_wcdb
                    trigger_history_bootstrap_from_wcdb(target_wxid, name, account_id, db_msgs)
                    logger.info(f"[冷启动] 触发好友 '{name}' 的历史接管分析")
                except Exception as boot_ex:
                    logger.debug(f"[冷启动] 触发历史分析异常: {boot_ex}")

    except Exception as e_mem:
        logger.warning(f"[工作流] 提取长周期记忆异常: {e_mem}")
        
    return context_msgs
