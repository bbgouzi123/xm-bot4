import time
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("TakeoverTimeout")

# 全局字典记录：session_name -> {"start_time": float, "last_alert_time": float}
_takeover_timeouts: Dict[str, Dict[str, float]] = {}

def process_takeover_timeouts(
    account_id: str,
    sessions: List[Dict[str, Any]],
    active_name: str,
    active_last_msgs: List[Any],
    driver_nickname: str
):
    """
    检查人工接管（is_takeover）状态下，员工是否超时未回复高意图或普通客户的消息。
    - sessions: 从微信 UIA 读出来的最近会话列表。
    - active_name: 当前活跃（打开）的聊天会话名称。
    - active_last_msgs: 活跃会话最新的消息气泡列表。
    - driver_nickname: 微信小助手自身的昵称。
    """
    global _takeover_timeouts
    
    from src.utils.contacts_cache import contacts_cache
    from src.api.config_api.base_config import _load_configs
    
    now = time.time()
    configs = _load_configs() or {}
    
    # 提取预警配置：默认 10 分钟 (600秒) 未回复就预警，催单预警间隔 5 分钟 (300秒)
    # 支持自定义超时，也可以从 settings 或 configs 读取
    timeout_limit = float(configs.get("takeover_timeout_minutes", 10)) * 60
    alert_interval = 300.0
    
    # 获取所有属于当前微信的好友缓存
    friends = contacts_cache.get_friends(account_id)
    takeover_friends = {f.get("name"): f for f in friends if f.get("is_takeover")}
    
    # 当前所有处于人工接管的会话
    active_takeover_sessions = set()
    
    # 1. 遍历会话列表中的未读消息（针对非当前窗口的接管会话）
    for s in sessions:
        name = s.get("name", "")
        if not name or name not in takeover_friends:
            continue
            
        unread_count = s.get("unread", 0)
        is_official = s.get("isOfficial", False)
        if is_official or name in ("文件传输助手", "微信团队", "订阅号消息", "服务通知"):
            continue
            
        # 如果有未读消息，说明客户发了消息且没被已读
        if unread_count > 0:
            active_takeover_sessions.add(name)
            if name not in _takeover_timeouts:
                _takeover_timeouts[name] = {"start_time": now, "last_alert_time": 0.0}
                logger.info(f"[人工接管监控] 发现未读接管会话 '{name}'，开始计时回复时间...")
                
    # 2. 检查当前活跃（打开）的会话（无红点，但有可能客户发了消息而员工未回复）
    if active_name and active_name in takeover_friends:
        name = active_name
        # 排除官方和系统会话
        if name not in ("文件传输助手", "微信团队", "订阅号消息", "服务通知"):
            # 如果活跃聊天记录的最后一条是客户发送的，说明正在等待员工回复
            if active_last_msgs:
                last_msg_item = active_last_msgs[-1]
                if isinstance(last_msg_item, (list, tuple)) and len(last_msg_item) >= 2:
                    sender, content = last_msg_item[0], last_msg_item[1]
                else:
                    sender, content = "未知", str(last_msg_item)
                    
                is_friend_sender = sender not in (driver_nickname, "我", "自己", "SYS", "Time", "Recall", "GREET")
                if is_friend_sender:
                    active_takeover_sessions.add(name)
                    if name not in _takeover_timeouts:
                        _takeover_timeouts[name] = {"start_time": now, "last_alert_time": 0.0}
                        logger.info(f"[人工接管监控] 发现打开状态的接管会话 '{name}' 待回复，开始计时回复时间...")
                else:
                    # 最后一条消息是自己发送 of self, 重置/清除计时
                    if name in _takeover_timeouts:
                        _takeover_timeouts.pop(name, None)
                        logger.info(f"[人工接管监控] 会话 '{name}' 已获得员工回复，清除计时。")

    # 3. 检查是否有超时并触发预警
    for name in list(_takeover_timeouts.keys()):
        # 如果会话已不在待回复列表中（比如已读且已回，或者取消了人工接管），清除计时
        if name not in active_takeover_sessions:
            _takeover_timeouts.pop(name, None)
            continue
            
        record = _takeover_timeouts[name]
        elapsed = now - record["start_time"]
        
        # 达到超时限制且距离上一次预警超过设定间隔
        if elapsed >= timeout_limit and (now - record["last_alert_time"] >= alert_interval):
            record["last_alert_time"] = now
            
            # 判断是否为高意向客户 (可以通过备注或历史标签来判断，或者从 lastMessage 匹配)
            friend = takeover_friends.get(name) or {}
            remark = friend.get("remark", "")
            tags = friend.get("tag", "")
            
            # 从 sessions 里找到最后一条消息
            last_msg_text = "客户发送了新消息"
            for s in sessions:
                if s.get("name") == name:
                    last_msg_text = s.get("lastMessage") or last_msg_text
                    break
            
            is_high_intent = False
            # 高意向判定：标签或备注含有“意向-强烈/强烈购买”或消息本身包含高意向词
            if "意向-强烈" in tags or "意向" in tags or any(k in last_msg_text for k in ("买", "多少钱", "价格", "收费", "转账", "定金", "收购", "付款")):
                is_high_intent = True
                
            elapsed_min = int(elapsed / 60)
            alert_title = "🚨 人工接管超时严重警告 🚨" if is_high_intent else "⚠️ 人工接管响应超时提醒 ⚠️"
            
            alert_content = (
                f"**微信账号**: {account_id}\n"
                f"**客户姓名**: {remark or name} ({name})\n"
                f"**等待时长**: {elapsed_min} 分钟\n"
                f"**客户标签**: {tags or '无'}\n"
                f"**最新消息**: {last_msg_text}\n"
                f"**紧急度**: {'🔥 极高（高意向客户，请立刻跟进！）' if is_high_intent else '中等（人工接管会话）'}\n"
                f"请客服人员立即处理，避免客户流失！"
            )
            
            logger.warning(f"[超时预警] 会话 '{name}' 响应超时 ({elapsed_min}分钟)，触发飞书/微信警报。")
            
            # 异步触发外部通知
            import asyncio
            async def _trigger_alerts():
                # 1. 飞书卡片通知
                try:
                    from src.utils.feishu_notifier import feishu_notifier
                    await feishu_notifier.send_alert_card(alert_title, alert_content, level="fatal" if is_high_intent else "warning")
                except Exception as fe:
                    logger.error(f"[超时预警] 发送飞书预警异常: {fe}")
                    
                # 2. 邮箱/微信传输助手兜底通知
                try:
                    from src.utils.alert_notifier import alert_notifier
                    await alert_notifier.send_alert_email(
                        machine_code="XM-BOT4-CLIENT",
                        account_id=account_id,
                        title=alert_title,
                        reason=alert_content.replace("**", "")
                    )
                except Exception as ae:
                    logger.error(f"[超时预警] 发送外部审计异常: {ae}")

                # 3. 消息通知中心与 WebSocket 桌面弹窗实时广播
                try:
                    from src.utils.alert_notifier import alert_notifier
                    from src.utils.websocket_manager import ws_manager

                    # 发送实时系统消息通知
                    await alert_notifier.send_user_notification(
                        title=alert_title,
                        body=f"客户 {remark or name} ({name}) 等待回复已达 {elapsed_min} 分钟！请及时处理。",
                        category="alert"
                    )

                    # 发送 WebSocket 弹窗广播
                    await ws_manager.broadcast_alert(
                        level="error" if is_high_intent else "warning",
                        title=alert_title,
                        content=alert_content.replace("**", "")
                    )
                except Exception as we:
                    logger.error(f"[超时预警] 发送前台实时告警异常: {we}")
                    
            asyncio.create_task(_trigger_alerts())
