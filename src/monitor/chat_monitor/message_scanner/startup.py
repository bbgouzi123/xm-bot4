import asyncio
import hashlib
import re
import logging
from src.utils.uia_task_runner import run_uia_with_timeout

logger = logging.getLogger(__name__)

class StartupMixin:
    """启动冷热检查逻辑"""

    def _check_startup_friend_allowed(self, opened_name: str) -> bool:
        """校验启动检查命中的会话是否在白名单/黑名单允许范围内，与主循环 reply_preconditions 对齐。"""
        try:
            from src.crm.account_data import get_account_settings
            _settings = get_account_settings(self.account_id)
            _reply_cfg = _settings.get("reply", {})
            _friend_mode = _reply_cfg.get("auto_chat_friend_mode", "black")
            if _friend_mode == "white":
                _raw = _reply_cfg.get("auto_chat_friend_whitelist", []) or []
                _allowed = {e.strip() for e in _raw if not e.startswith("wxid:")}
                return opened_name in _allowed
            elif _friend_mode == "black":
                _raw = _reply_cfg.get("auto_chat_friend_excludes", []) or []
                _excluded = {e.strip() for e in _raw if not e.startswith("wxid:")}
                return opened_name not in _excluded
        except Exception as _ex:
            logger.warning(f"[监控] 启动检查好友模式过滤异常（降级放行）: {_ex}")
        return True  # 异常时降级放行，避免误屏蔽

    async def _run_startup_chat_check(self):
        """启动监控时执行的首次聊天检查：
        1. 检查当前是否打开了某个好友/群聊的对话框
        2. 如果打开了，并且开启了自动聊天，并且最后一条消息是好友发来的，则立即触发自动回复
        3. 否则，直接点击通讯录以关闭聊天窗口，防新消息被默默消费
        """
        account_id = self.account_id
        if not account_id or account_id == 'default':
            return

        print("[监控] 正在执行启动聊天状态安全检查...")
        try:
            import os
            hex_key = os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
            if hex_key:
                wait_rounds = 16  # 16 * 0.5s = 8 seconds
                while wait_rounds > 0:
                    if hasattr(self, '_wcdb_session_monitor') and self._wcdb_session_monitor and self._wcdb_session_monitor.is_active():
                        logger.info("[监控] 启动检查：检测到 WCDB 已就绪，开始执行高精度数据库优先检查。")
                        break
                    await asyncio.sleep(0.5)
                    wait_rounds -= 1
                if wait_rounds <= 0:
                    logger.warning("[监控] 启动检查：等待 WCDB 激活超时，将降级执行物理 UI 启动检查。")
        except Exception as wcdb_wait_ex:
            logger.debug(f"[监控] 启动检查等待 WCDB 异常: {wcdb_wait_ex}")

        try:
            from .contacts_preheater import preheat_contacts_cache
            preheat_contacts_cache(self.account_id)


            def _find_active_input_name_startup():
                try:
                    from src.utils.safe_uia import find_active_input_control_safely
                    hwnd = getattr(self.driver, "hwnd", 0) if getattr(self, "driver", None) else 0
                    return find_active_input_control_safely(None, hwnd=hwnd)
                except Exception as ex:
                    logger.debug(f"[启动检查] 寻找活跃输入框异常: {ex}")
                return ""

            opened_name_raw = await run_uia_with_timeout(_find_active_input_name_startup, 10.0)
            if not opened_name_raw:
                print("[监控] 启动检查：未检测到当前打开了任何聊天对话框，安全。")
                return

            opened_name = re.sub(r'\s+按住.*$', '', opened_name_raw)
            opened_name = re.sub(r'\(\d+\)$', '', opened_name)
            opened_name = opened_name.strip()

            try:
                import app.state as app_state
                app_state.active_chat_name = opened_name
            except Exception:
                pass

            if not opened_name or opened_name in ("文件传输助手", "微信团队", "订阅号消息", "服务通知"):
                print(f"[监控] 启动检查：当前活跃会话为系统或辅助会话 '{opened_name}'，无需处理。")
                return

            print(f"[监控] 启动检查：检测到当前打开了会话 '{opened_name}'")

            from src.api.config_api import _load_configs
            from src.api.instance_settings_api import load_instance_settings
            from src.utils.license_validator import LicenseValidator
            configs = _load_configs() or {}
            inst_settings = load_instance_settings(self.account_id)
            features = LicenseValidator.check_features()
            auto_chat_enabled = features.get("auto_chat", False)

            if not auto_chat_enabled or not configs.get("auto_reply_enabled", True) or not inst_settings.get("auto_reply_enabled", True):
                print(f"[监控] 启动检查：自动回复或自动聊天被禁用，关闭当前活跃的聊天窗口")
                await run_uia_with_timeout(self.driver.CloseActiveChat, 10.0, False)
                return

            # 🛡️ 核心修复：补充白名单/黑名单过滤，与主循环 reply_preconditions 对齐
            if not self._check_startup_friend_allowed(opened_name):
                print(f"[监控] 启动检查：会话 '{opened_name}' 不符合好友过滤规则，跳过自动回复")
                await run_uia_with_timeout(self.driver.CloseActiveChat, 10.0, False)
                return

            try:
                from src.uia.retry.window_ops import ensure_wechat_foreground
                if self.driver.hwnd:
                    ensure_wechat_foreground(self.driver.hwnd)
            except Exception as e:
                logger.warning(f"[监控] 启动检查置顶微信主窗口异常: {e}")

            has_unread = False
            used_db_check = False
            content = ""

            # 🚀 WCDB 已就绪时优先从数据库判定未读数，免除物理 UI 扫描
            if hasattr(self, '_wcdb_session_monitor') and self._wcdb_session_monitor and self._wcdb_session_monitor.is_active():
                try:
                    sessions = self._wcdb_session_monitor.get_latest_sessions_from_db(limit=80) or []
                    clean_opened_name = re.sub(r'\(\d+\)$', '', opened_name).strip()
                    target_session = None
                    for s in sessions:
                        s_name = (s.get("name") or "").strip()
                        s_wxid = (s.get("wxid") or "").strip()
                        if s_name == opened_name or s_name == clean_opened_name or s_wxid == opened_name:
                            target_session = s
                            break
                    if target_session is not None:
                        unread_count = int(target_session.get("unread") or 0)
                        used_db_check = True
                        if unread_count == 0:
                            print(f"[数据库优化检查] 经安全通道确认，会话 '{opened_name}' 真实未读消息数为 0，跳过物理 UI 气泡扫描。")
                            has_unread = False
                        else:
                            print(f"[数据库优化检查] 经安全通道确认，会话 '{opened_name}' 存在 {unread_count} 条真实未读消息，进入智能回复决策。")
                            has_unread = True
                except Exception as db_err:
                    logger.warning(f"[数据库优化检查] 执行优选未读数检查异常，将降级到 UI 兜底: {db_err}")
            if not used_db_check:
                # UIA 物理兜底
                last_msgs = await run_uia_with_timeout(
                    self.driver.get_all_messages, 15.0, False, 3, opened_name, False
                )
                if last_msgs:
                    last_msg_item = last_msgs[-1]
                    if isinstance(last_msg_item, (list, tuple)) and len(last_msg_item) >= 2:
                        sender, content = last_msg_item[0], last_msg_item[1]
                    else:
                        sender, content = "未知", str(last_msg_item)
                    is_friend_sender = sender not in (self.driver._nickname, "我", "自己", "SYS", "Time", "Recall", "GREET")
                    if is_friend_sender:
                        from src.utils.contacts_cache import contacts_cache
                        all_groups = contacts_cache.get_groups(self.account_id)
                        is_group_eval = any(g.get('name') == opened_name for g in all_groups)
                        if not is_group_eval and ('、' in opened_name or ":" in content or "：" in content):
                            is_group_eval = True
                        if self._check_is_self_sent(opened_name, content, is_group_eval, opened_name, last_msgs, self.account_id):
                            logger.info(f"[监控] 启动检查二次复核：最新消息虽标记为 '{sender}'，但复核判定为机器人自己发送的自回复消息，主动忽略自动回复。内容: '{content}'")
                            is_friend_sender = False
                        else:
                            has_unread = True
                        if is_friend_sender:
                            print(f"[监控] 启动检查：发现该会话最新消息由好友 '{sender}' 发送且尚未回复，内容: '{content}'")
            else:
                if has_unread:
                    last_msgs = await run_uia_with_timeout(
                        self.driver.get_all_messages, 15.0, False, 3, opened_name, False
                    )
                    if last_msgs:
                        last_msg_item = last_msgs[-1]
                        if isinstance(last_msg_item, (list, tuple)) and len(last_msg_item) >= 2:
                            sender, content = last_msg_item[0], last_msg_item[1]
                        else:
                            sender, content = "未知", str(last_msg_item)
                        print(f"[数据库优化检查] UIA 已成功对齐获取会话 '{opened_name}' 最新消息: '{content}'")
                    
                    from src.utils.contacts_cache import contacts_cache
                    from src.uia.session import session_type_cache
                    all_groups = contacts_cache.get_groups(self.account_id)
                    is_group = any(g.get('name') == opened_name for g in all_groups)
                    user_name = opened_name
                    all_friends = contacts_cache.get_friends(self.account_id)
                    from .utils import build_identity_maps
                    _, group_name_to_wxid = build_identity_maps(all_friends, all_groups)
                    clean_opened_name = re.sub(r'[\(（]\d+[\)）]$', '', opened_name).strip()
                    friend_names = {(f.get('name') or '').strip() for f in all_friends} | {(f.get('remark') or '').strip() for f in all_friends}
                    friend_names.discard('')
                    if clean_opened_name in friend_names or opened_name in friend_names:
                        is_group = False
                    elif clean_opened_name in group_name_to_wxid or opened_name in group_name_to_wxid:
                        is_group = True
                    if not is_group:
                        if '、' in opened_name or '、' in clean_opened_name:
                            is_group = True
                        elif session_type_cache.get_type(opened_name) == "group" or session_type_cache.get_type(clean_opened_name) == "group":
                            is_group = True
                        elif ":" in content or "：" in content:
                            parts = content.split(":", 1) if ":" in content else content.split("：", 1)
                            prefix = parts[0].strip()
                            if 0 < len(prefix) <= 35 and not prefix.lower() in ("http", "https", "ftp", "file", "ws", "wss") and prefix not in (self.driver._nickname, "我", "自己"):
                                is_group = True
                    if is_group:
                        session_type_cache.set_type(opened_name, "group")
                        if clean_opened_name != opened_name:
                            session_type_cache.set_type(clean_opened_name, "group")
                    else:
                        session_type_cache.set_type(opened_name, "friend")
                        if clean_opened_name != opened_name:
                            session_type_cache.set_type(clean_opened_name, "friend")
                    opened_wxid = None
                    try:
                        import app.state as app_state
                        opened_wxid = app_state.name_to_active_wxid.get(opened_name)
                    except Exception:
                        pass
                    if not opened_wxid:
                        matching_wxids = []
                        if is_group:
                            for g in all_groups:
                                if g.get('name') == opened_name:
                                    matching_wxids.append(g.get('wxid'))
                        else:
                            for f in all_friends:
                                if f.get('name') == opened_name or f.get('remark') == opened_name:
                                    matching_wxids.append(f.get('wxid'))
                        if len(matching_wxids) == 1:
                            opened_wxid = matching_wxids[0]
                    
                    if opened_wxid:
                        try:
                            import app.state as app_state
                            app_state.active_chat_wxid = opened_wxid
                        except Exception:
                            pass

                    key = opened_wxid or opened_name
                    last_time = ""
                    last_msg_val = content
                    unread_count = 1
                    target_session = None

                    fp = hashlib.md5(f"{opened_name}:{last_time}:{last_msg_val}:{unread_count}".encode()).hexdigest()
                    self._mark_session_processing(opened_name, opened_wxid)
                    for k in (opened_name, opened_wxid):
                        if not k:
                            continue
                        self._initialized.add(k)
                        self._last_seen_msg[k] = fp
                        self._fingerprints.setdefault(k, set()).add(fp)
                        self._last_unread_snapshot[k] = unread_count
                    is_at = False
                    if is_group:
                        if 'target_session' in locals() and target_session:
                            is_at = target_session.get('isAt', False)
                        
                        if not is_at:
                            last_msg_clean = content.replace('\u2005', ' ').replace('\u200b', '').strip()
                            nicknames_to_check = [n for n in [getattr(self.driver, '_nickname', ''), getattr(self.driver, 'bot_wxid', '') or getattr(self.driver, '_wxid', '')] if n]
                            try:
                                from src.api.config_api import _load_configs
                                bot_name_cfg = _load_configs().get("bot_name", "")
                                if bot_name_cfg:
                                    nicknames_to_check.append(bot_name_cfg)
                            except Exception:
                                pass
                            for n in nicknames_to_check:
                                pattern = re.compile(rf'@[\s\u2005]*{re.escape(n)}', re.IGNORECASE)
                                if pattern.search(last_msg_clean) or pattern.search(content):
                                    is_at = True
                                    break
                            if not is_at:
                                for all_tag in ("所有人", "all", "All"):
                                    pattern = re.compile(rf'@[\s\u2005]*{re.escape(all_tag)}', re.IGNORECASE)
                                    if pattern.search(last_msg_clean) or pattern.search(content):
                                        is_at = True
                                        break

                    should_dispatch = True
                    if is_group:
                        try:
                            from src.crm.account_data import get_account_settings
                            from src.api.config_api import _load_configs
                            settings = get_account_settings(self.account_id)
                            reply_cfg = settings.get("reply", {})
                            configs = _load_configs()
                            bot_group_auto_start = reply_cfg.get("bot_group_auto_start", configs.get("bot_group_auto_start", False))
                            auto_chat_group_at_only = reply_cfg.get("auto_chat_group_at_only", configs.get("auto_chat_group_at_only", True))
                            logger.info(f"[监控] Startup check: account_id={self.account_id}, bot_group_auto_start={bot_group_auto_start}, at_only={auto_chat_group_at_only}")
                        except Exception as e:
                            logger.error(f"[监控] Startup check error loading config: {e}")
                            bot_group_auto_start = False
                            auto_chat_group_at_only = True
                        
                        if not bot_group_auto_start:
                            logger.info(f"[监控] Startup check: group reply disabled, skipping for {opened_name}.")
                            should_dispatch = False
                        elif auto_chat_group_at_only and not is_at:
                            logger.info(f"[监控] Startup check: group reply at_only is active but not mentioned, skipping for {opened_name}.")
                            should_dispatch = False
                    
                    if should_dispatch:
                        asyncio.create_task(self._safe_reply(opened_name, content, is_group, user_name, is_at, wxid=opened_wxid))
                    else:
                        self._clear_session_processing(opened_name, opened_wxid)
                        await run_uia_with_timeout(self.driver.CloseActiveChat, 10.0, False)
                        return

            if not has_unread:
                print(f"[监控] 启动检查：会话 '{opened_name}' 无未回复的消息，关闭当前活跃的聊天窗口")
                await run_uia_with_timeout(self.driver.CloseActiveChat, 10.0, False)

        except Exception as e:
            print(f"[监控] 启动聊天检查发生异常: {e}")
            logger.error(f"[监控] 启动聊天检查发生异常: {e}", exc_info=True)
            try:
                self._clear_session_processing(opened_name, opened_wxid)
            except Exception:
                pass
