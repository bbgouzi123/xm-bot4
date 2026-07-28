import logging
import asyncio
import re
import hashlib
import app.state as app_state

logger = logging.getLogger("WeChatDriver.ActiveChatHelper")

def scan_and_enqueue_active_chat_before_switch(driver, target_session_name: str):
    """
    [切换前扫尾防御]
    在微信物理切换会话前，如果当前正在展示另外一个活跃聊天窗口，且其有对方发送的最新未读消息，
    立刻用 UIA 捕获该消息并异步投递到主监控事件循环中，防止切换窗口后导致该消息被 WeChat
    置为已读但未被机器人感知/回复的“漏回”Race Condition。
    """
    try:
        monitor = getattr(driver, "_chat_monitor", None)
        if not monitor:
            # 尝试从全局 app_state 获取
            monitor = getattr(app_state, "monitor", None)
        if not monitor:
            return

        # 1. 找到当前活跃的聊天详情容器
        root = driver.root
        if not root:
            return
        chat_container = root.GroupControl(ClassName='mmui::ChatDetailView')
        if not chat_container.Exists(0.05):
            return

        edit_ctrl = chat_container.EditControl(ClassName="mmui::ChatInputField", searchDepth=8)
        if not edit_ctrl or not edit_ctrl.Exists(0.05):
            return

        active_name = edit_ctrl.Name
        if not active_name:
            return

        # 清理后缀与空格
        active_name = re.sub(r'\s+按住.*$', '', active_name)
        active_name = re.sub(r'\(\d+\)$', '', active_name)
        active_name = active_name.strip()

        from src.uia.session import clean_session_name
        if clean_session_name(active_name) == clean_session_name(target_session_name):
            # 目标就是当前会话，无需扫尾
            return

        # 2. 读取当前會话的最新消息
        active_last_msgs = driver.get_all_messages(context_count=5, session_name=active_name)
        if not active_last_msgs:
            return

        # 3. 校验最新消息是否来自好友（非自己发送，非公众号系统消息等）
        sender, content = monitor._extract_bubble_info(active_last_msgs[-1])
        driver_nickname = getattr(driver, '_nickname', '') or '我'
        if sender in (driver_nickname, "我", "自己", "SYS", "Time", "Recall", "GREET"):
            return

        # 4. 提交给主事件循环异步运行评估与入队
        main_loop = getattr(app_state, "main_loop", None)
        if main_loop and main_loop.is_running():
            logger.info(f"[扫尾防御] 物理切换前探测到活跃聊天 '{active_name}' 的新消息 '{content}'，正在提交异步评估...")
            asyncio.run_coroutine_threadsafe(
                monitor._scan_and_enqueue_active_chat_async(active_name, active_last_msgs),
                main_loop
            )
    except Exception as e:
        logger.debug(f"[扫尾防御] 执行切换前活跃窗口扫尾过滤异常: {e}")


async def scan_and_enqueue_active_chat_async_impl(monitor, active_name: str, active_last_msgs: list):
    """
    异步单条会话扫尾评估与注入实现。
    """
    try:
        logger.info(f"[扫尾防御] 正在异步处理活跃会话 '{active_name}' 的扫尾检测...")
        
        chat_fp_str = "||".join(f"{s}:{c}" for s, c in active_last_msgs)
        active_chat_fp = hashlib.md5(f"{active_name}:{chat_fp_str}".encode()).hexdigest()
        
        # 如果指纹已经被记录/处理过，跳过
        if active_name in monitor._fingerprints and active_chat_fp in monitor._fingerprints[active_name]:
            return
            
        from src.utils.contacts_cache import contacts_cache
        account_id = monitor.account_id
        all_friends = contacts_cache.get_friends(account_id) or []
        all_groups = contacts_cache.get_groups(account_id) or []
        
        is_group = any(g.get('name') == active_name for g in all_groups)
        
        target_wxid = ""
        if is_group:
            for g in all_groups:
                if g.get('name') == active_name:
                    target_wxid = g.get('wxid', '')
                    break
        else:
            for f in all_friends:
                if f.get('name') == active_name or f.get('remark') == active_name:
                    target_wxid = f.get('wxid', '')
                    break
        
        last_msg = active_last_msgs[-1][1] if active_last_msgs else ""
        
        session = {
            "name": active_name,
            "lastMessage": last_msg,
            "unread": 1,  # 强制设为 1 绕过 UIA 的 unread 校验
            "isGroup": is_group,
            "isAt": False,
            "isOfficial": False
        }
        
        friend_name_to_wxid = {f['name']: f['wxid'] for f in all_friends if f.get('name') and f.get('wxid')}
        group_name_to_wxid = {g['name']: g['wxid'] for g in all_groups if g.get('name') and g.get('wxid')}
        
        reply_cfg, friend_excludes, group_excludes = monitor._prepare_reply_filters(account_id)

        # 调用原本的单条会话评估逻辑，100% 规则复用！
        await monitor._evaluate_single_session(
            session, active_name, active_last_msgs, active_chat_fp,
            user_active_now=False, reply_cfg=reply_cfg,
            friend_excludes=friend_excludes,
            group_excludes=group_excludes,
            friend_name_to_wxid=friend_name_to_wxid, group_name_to_wxid=group_name_to_wxid,
            unread_private_sessions_count=0, account_id=account_id
        )
    except Exception as e:
        logger.error(f"[扫尾防御] 评估活跃窗口失败: {e}", exc_info=True)
