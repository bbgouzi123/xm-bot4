import logging
import re

logger = logging.getLogger(__name__)

class CheckMixin:
    """主扫描入口与各子模块协调编排"""

    async def _check(self):
        account_id = self.account_id
        if not account_id or account_id == 'default':
            return

        from src.utils.uia_task_runner import is_uia_maintenance_active
        if is_uia_maintenance_active() or self._paused:
            return

        # 1. 预检当前活跃窗口与无红点新消息
        active_name, active_last_msgs, active_chat_fp, user_active_now = await self._check_active_chat()
        try:
            import app.state as app_state
            if active_name:
                app_state.active_chat_name = active_name
                app_state.active_chat_wxid = app_state.name_to_active_wxid.get(active_name) or app_state.name_to_active_wxid.get(f"{account_id}:{active_name}")
        except Exception:
            pass

        # 2. 消息缓冲区滑窗合并与悬疑消息超时清理
        await self._process_message_buffer()

        # 3. 窄屏退回会话列表以利于接受新消息
        await self._check_narrow_screen_back(user_active_now)

        if is_uia_maintenance_active() or self._paused:
            return

        # 🌟 双引擎智能降级与协同机制：
        # 如果 WCDB 数据库监控引擎处于激活状态（无论是原生 DLL 还是 Python 影子拷贝降级），
        # 说明数据库已正常连接并能够进行消息感知，我们直接跳过 UIA 物理会话列表扫描和跳转。
        # 仅在需要回复发送时才通过 UIA 执行，彻底避免双引擎并行时的冲突与 UIA 冗余扫描。
        if self._wcdb_session_monitor and self._wcdb_session_monitor.is_active():
            # 静默进行每日自动备份等收尾维护动作，直接跳过物理会话列表的拉取与遍历
            try:
                from src.monitor.chat_monitor.auto_backup import trigger_daily_auto_backup
                trigger_daily_auto_backup(account_id=account_id)
            except Exception as e:
                logger.error(f"[自动备份] 执行自动备份发生异常: {e}")
            self._first_scan_cycle_done = True
            return

        # 4. 获取会话列表，并执行隐藏未读跳转巡检
        sessions = await self._fetch_and_navigate_sessions(active_name, active_last_msgs, user_active_now)
        if not sessions:
            return

        # 5. 读取并清洗该账号隔离的黑白名单与配置
        reply_cfg, friend_excludes, group_excludes = self._prepare_reply_filters(account_id)
        # 🛡️ 门卫返回值校验：若 account_id 为 default 等无效状态，三元组均为空，直接退出
        if not reply_cfg and friend_excludes == [] and group_excludes == []:
            logger.debug("[门卫] _prepare_reply_filters 返回空（account_id 未就绪），跳过本轮扫描")
            return

        # 统计有未读消息的非群聊会话数量，以便在启动时对少量的未读好友消息执行放行回复
        unread_private_sessions_count = 0
        from src.utils.contacts_cache import contacts_cache
        all_friends = contacts_cache.get_friends(account_id)
        all_groups = contacts_cache.get_groups(account_id)
        from .utils import build_identity_maps
        friend_name_to_wxid, group_name_to_wxid = build_identity_maps(all_friends, all_groups)

        if sessions:
            for s in sessions:
                s_name = s.get('name', '') or ''
                s_unread = s.get('unread', 0) or 0
                s_is_group = s.get('isGroup', False)
                s_is_official = s.get('isOfficial', False)
                # 兼容性判断：群聊名称含人数括号，或者在已知的群聊列表中，或者包含 、
                is_g = s_is_group or '、' in s_name or bool(re.search(r'[\(（]\d+[\)）]$', s_name))
                is_g = is_g or s_name in group_name_to_wxid or re.sub(r'[\(（]\d+[\)）]$', '', s_name).strip() in group_name_to_wxid
                if s_unread > 0 and not is_g and not s_is_official and s_name not in self.SYSTEM_SESSIONS and not s_name.startswith('折叠的聊天'):
                    unread_private_sessions_count += 1

        if sessions:
            logger.debug(f"[监控] 账号 '{account_id}' 正在扫描 {len(sessions)} 个会话...")

        if is_uia_maintenance_active() or self._paused:
            return

        # 6. 对列表中的每个会话执行匹配决策
        await self._scan_sessions(
            sessions=sessions,
            active_name=active_name,
            active_last_msgs=active_last_msgs,
            active_chat_fp=active_chat_fp,
            user_active_now=user_active_now,
            reply_cfg=reply_cfg,
            friend_excludes=friend_excludes,
            group_excludes=group_excludes,
            unread_private_sessions_count=unread_private_sessions_count
        )

        # 7. 收尾：执行超时监控、自动备份、还原列表位置
        await self._post_check_actions(sessions, active_name, active_last_msgs)

    async def _scan_sessions(self, sessions: list, active_name: str, active_last_msgs: list, active_chat_fp: str, user_active_now: bool, reply_cfg: dict, friend_excludes: list, group_excludes: list, unread_private_sessions_count: int):
        account_id = self.account_id
        from src.utils.contacts_cache import contacts_cache
        all_friends = contacts_cache.get_friends(account_id)
        all_groups = contacts_cache.get_groups(account_id)
        from .utils import build_identity_maps
        friend_name_to_wxid, group_name_to_wxid = build_identity_maps(all_friends, all_groups)

        for session in sessions:
            try:
                await self._evaluate_single_session(session, active_name, active_last_msgs, active_chat_fp, user_active_now, reply_cfg, friend_excludes, group_excludes, friend_name_to_wxid, group_name_to_wxid, unread_private_sessions_count, account_id)
            except Exception as eval_err:
                logger.error(f"[评估] 评估会话 '{session.get('name')}' 异常: {eval_err}", exc_info=True)
