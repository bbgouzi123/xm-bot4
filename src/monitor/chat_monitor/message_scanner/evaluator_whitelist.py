import logging
import re
import asyncio
from .utils import check_friend_in_list, check_group_in_list

logger = logging.getLogger(__name__)

class EvaluatorWhitelistMixin:
    """自动回复白黑名单过滤策略与前端联动"""

    async def _validate_whitelist_rules(
        self, name: str, is_group: bool, clean_name: str, friend_excludes: list,
        group_excludes: list, friend_name_to_wxid: dict, group_name_to_wxid: dict,
        reply_cfg: dict, is_friend_accept_notify: bool, fp: str, last_msg: str = None
    ) -> bool:
        if self._whitelist_enabled and self._whitelist:
            if not any(w in name for w in self._whitelist):
                logger.info(f"[监控] 会话 '{name}' 未在白名单列表中，跳过")
                self._fingerprints.setdefault(name, set()).add(fp)
                return False

        if is_group:
            bot_group_auto_start = reply_cfg.get("bot_group_auto_start", False)
            if not bot_group_auto_start:
                logger.info(f"[监控] 群聊 '{name}' 自动回复开关未开启，跳过")
                return False
            g_wxid = group_name_to_wxid.get(clean_name, "") or group_name_to_wxid.get(name, "")
            group_mode = reply_cfg.get("auto_chat_group_mode", "black")
            in_group_list = check_group_in_list(name, g_wxid, group_excludes, account_id=self.account_id) or check_group_in_list(clean_name, g_wxid, group_excludes, account_id=self.account_id)


            if group_mode == "black":
                if in_group_list:
                    logger.info(f"[监控] 群聊 '{name}' 在自动回复黑名单中，跳过回复")
                    return False
            else:
                if not in_group_list:
                    # 🌟 触发群聊白名单实时解密同步微信数据库自愈校验
                    import time
                    is_valid_wxid = self.account_id and self.account_id != "default" and not self.account_id.startswith("wx_")
                    now = time.time()
                    last_sync = getattr(self, "_last_global_whitelist_sync_time", 0.0)
                    if is_valid_wxid and (now - last_sync > 300.0):
                        self._last_global_whitelist_sync_time = now
                        logger.info(f"[白名单热修复] 群聊 '{name}' 未命中白名单，且符合全局同步冷却，触发实时同步通讯录校验...")
                        try:
                            from src.wechat_4x.db_contact_syncer import sync_contacts_from_db
                            from src.wechat_4x.db_match_helper import auto_detect_db_path
                            from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor
                            import os

                            db_path = ""
                            hex_key = ""
                            if hasattr(self, "_wcdb_session_monitor") and self._wcdb_session_monitor:
                                db_path = getattr(self._wcdb_session_monitor, "_db_path", "")
                                hex_key = getattr(self._wcdb_session_monitor, "_hex_key", "")

                            if not hex_key:
                                from src.utils.wechat_key_store import get_persisted_wechat_key
                                hex_key = get_persisted_wechat_key(self.account_id) or os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
                            if not hex_key:
                                hex_key = get_wcdb_key_extractor().get_key(timeout_s=2.0) or ""
                            if not db_path and hex_key:
                                db_path = os.environ.get("WCDB_SESSION_DB_PATH", "") or auto_detect_db_path(hex_key, self.account_id) or ""

                            if db_path and hex_key:
                                db_storage_dir = os.path.dirname(os.path.dirname(db_path))
                                loop = asyncio.get_running_loop()
                                await loop.run_in_executor(None, sync_contacts_from_db, db_storage_dir, hex_key, self.account_id)

                                # 同步成功后，重新获取最新的联系人并查一次 g_wxid 并重新判断白名单
                                from src.utils.contacts_cache import contacts_cache
                                from .utils import build_identity_maps
                                all_groups = contacts_cache.get_groups(self.account_id)
                                _, new_group_name_to_wxid = build_identity_maps([], all_groups)
                                g_wxid = new_group_name_to_wxid.get(clean_name, "") or new_group_name_to_wxid.get(name, "")
                                in_group_list = check_group_in_list(name, g_wxid, group_excludes, account_id=self.account_id) or check_group_in_list(clean_name, g_wxid, group_excludes, account_id=self.account_id)
                                logger.info(f"[白名单热修复] 实时同步后重新计算群聊: g_wxid={g_wxid}, in_group_list={in_group_list}")
                        except Exception as e_sync:
                            logger.error(f"[白名单热修复] 触发实时群聊同步异常: {e_sync}")

                if not in_group_list:
                    wl_task_id = f"whitelist_{g_wxid or name}"
                    broadcasted_ids = getattr(self, '_broadcasted_whitelist_ids', set())
                    if wl_task_id not in broadcasted_ids:
                        logger.warning(
                            f"[监控白名单诊断] 群聊 '{name}' 未命中白名单！"
                            f"name_repr={repr(name)}, clean_name_repr={repr(clean_name)}, "
                            f"g_wxid={repr(g_wxid)}, group_excludes={repr(group_excludes)}, "
                            f"group_mode={repr(group_mode)}"
                        )
                    else:
                        logger.debug(
                            f"[监控白名单诊断] 群聊 '{name}' 未命中白名单！"
                            f"name_repr={repr(name)}, clean_name_repr={repr(clean_name)}, "
                            f"g_wxid={repr(g_wxid)}, group_excludes={repr(group_excludes)}, "
                            f"group_mode={repr(group_mode)}"
                        )
                    logger.debug(f"[监控] 群聊 '{name}' 不在自动回复白名单中，跳过回复")

                    # ⚠️ [修复] 将未命中的群聊消息指纹正确写入 fingerprints 缓存，防止重复扫描
                    self._fingerprints.setdefault(name, set()).add(fp)
                    if g_wxid:
                        self._fingerprints.setdefault(g_wxid, set()).add(fp)
                    
                    # ⚠️ [修复] 以 task_id（whitelist_{wxid or name}）为去重 key，
                    # 防止跨会话的 fp MD5 碰撞导致其他好友的白名单拦截广播被错误阻断
                    if wl_task_id not in broadcasted_ids:
                        self._update_overlay_and_broadcast_whitelist(name, is_group=True, wxid=g_wxid, incoming_msg=last_msg)
                        if not hasattr(self, '_broadcasted_whitelist_ids'):
                            self._broadcasted_whitelist_ids = set()
                        self._broadcasted_whitelist_ids.add(wl_task_id)
                        if not hasattr(self, '_session_broadcasted_fps'):
                            self._session_broadcasted_fps = {}
                        self._session_broadcasted_fps.setdefault(g_wxid or name, set()).add(fp)
                    return False
        else:
            f_wxid = friend_name_to_wxid.get(name, "")
            friend_mode = reply_cfg.get("auto_chat_friend_mode", "black")
            logger.debug(
                f"[白名单诊断] 好友='{name}', f_wxid={repr(f_wxid)}, "
                f"friend_mode={friend_mode}, account_id={self.account_id}, "
                f"friend_excludes={repr(friend_excludes)}"
            )
            in_friend_list = check_friend_in_list(name, f_wxid, friend_excludes, account_id=self.account_id)
            if friend_mode == "black":
                if in_friend_list:
                    logger.debug(f"[监控] 好友 '{name}' 在自动回复黑名单中，跳过回复")
                    self._fingerprints.setdefault(name, set()).add(fp)
                    return False
            else:
                if not is_friend_accept_notify and not in_friend_list:
                    # 🌟 触发好友白名单实时解密同步微信数据库自愈校验
                    import time
                    is_valid_wxid = self.account_id and self.account_id != "default" and not self.account_id.startswith("wx_")
                    now = time.time()
                    last_sync = getattr(self, "_last_global_whitelist_sync_time", 0.0)
                    if is_valid_wxid and (now - last_sync > 300.0):
                        self._last_global_whitelist_sync_time = now
                        logger.info(f"[白名单热修复] 好友 '{name}' (wxid={f_wxid or '未匹配'}) 未命中白名单，且符合全局同步冷却，触发实时同步通讯录校验...")
                        try:
                            from src.wechat_4x.db_contact_syncer import sync_contacts_from_db
                            from src.wechat_4x.db_match_helper import auto_detect_db_path
                            from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor
                            import os

                            db_path = ""
                            hex_key = ""
                            if hasattr(self, "_wcdb_session_monitor") and self._wcdb_session_monitor:
                                db_path = getattr(self._wcdb_session_monitor, "_db_path", "")
                                hex_key = getattr(self._wcdb_session_monitor, "_hex_key", "")

                            if not hex_key:
                                from src.utils.wechat_key_store import get_persisted_wechat_key
                                hex_key = get_persisted_wechat_key(self.account_id) or os.environ.get("WCDB_HEX_KEY", "") or os.environ.get("WECHAT_4X_KEY_HEX", "")
                            if not hex_key:
                                hex_key = get_wcdb_key_extractor().get_key(timeout_s=2.0) or ""
                            if not db_path and hex_key:
                                db_path = os.environ.get("WCDB_SESSION_DB_PATH", "") or auto_detect_db_path(hex_key, self.account_id) or ""

                            if db_path and hex_key:
                                db_storage_dir = os.path.dirname(os.path.dirname(db_path))
                                loop = asyncio.get_running_loop()
                                await loop.run_in_executor(None, sync_contacts_from_db, db_storage_dir, hex_key, self.account_id)

                                # 同步成功后，重新获取最新的联系人并查一次 f_wxid 并重新判断白名单
                                from src.utils.contacts_cache import contacts_cache
                                from .utils import build_identity_maps
                                all_friends = contacts_cache.get_friends(self.account_id)
                                new_friend_name_to_wxid, _ = build_identity_maps(all_friends, [])
                                f_wxid = new_friend_name_to_wxid.get(name, "")
                                in_friend_list = check_friend_in_list(name, f_wxid, friend_excludes, account_id=self.account_id)
                                logger.info(f"[白名单热修复] 实时同步后重新计算好友: f_wxid={f_wxid}, in_friend_list={in_friend_list}")
                        except Exception as e_sync:
                            logger.error(f"[白名单热修复] 触发实时好友同步异常: {e_sync}")

                if not is_friend_accept_notify and not in_friend_list:
                    logger.debug(f"[监控] 好友 '{name}' 不在自动回复白名单中，跳过回复")
                    self._fingerprints.setdefault(name, set()).add(fp)
                    
                    # ⚠️ [修复] 以 task_id（whitelist_{wxid or name}）为去重 key
                    wl_task_id = f"whitelist_{f_wxid or name}"
                    broadcasted_ids = getattr(self, '_broadcasted_whitelist_ids', set())
                    if wl_task_id not in broadcasted_ids:
                        self._update_overlay_and_broadcast_whitelist(name, is_group=False, wxid=f_wxid, incoming_msg=last_msg)
                        if not hasattr(self, '_broadcasted_whitelist_ids'):
                            self._broadcasted_whitelist_ids = set()
                        self._broadcasted_whitelist_ids.add(wl_task_id)
                        if not hasattr(self, '_session_broadcasted_fps'):
                            self._session_broadcasted_fps = {}
                        self._session_broadcasted_fps.setdefault(f_wxid or name, set()).add(fp)
                    return False
        return True

    def _update_overlay_and_broadcast_whitelist(self, name: str, is_group: bool, wxid: str = None, incoming_msg: str = None):
        try:
            from src.utils.status_overlay import status_overlay
            status_overlay.pending_whitelist_friend = name
            status_overlay.pending_is_group = is_group
            msg = f"群聊不在白名单" if is_group else "好友不在白名单"
            status_overlay.update("未回复", f"{msg} (按 F9 快捷加入)", name, 0x00A5FF)
        except Exception:
            pass

        # 默认文案
        if not incoming_msg:
            incoming_msg = "未在自动回复白名单列表中，请点击右侧按钮一键加入白名单"

        try:
            from src.utils.websocket_manager import ws_manager
            loop = asyncio.get_running_loop()
            if loop and loop.is_running():
                asyncio.ensure_future(ws_manager.broadcast_task_update(
                    task_id=f"whitelist_{wxid or name}",
                    task_type="自动回复",
                    status="error",
                    progress=100,
                    total=100,
                    message=f"{'群聊' if is_group else '好友'} '{name}' 不在自动回复白名单中，已跳过回复",
                    friend_name=name,
                    friend_wxid=wxid,
                    bot_wxid=self.account_id,
                    incoming_msg=incoming_msg,
                    error_type="whitelist_blocked",
                    is_group=is_group
                ))
        except Exception:
            pass
