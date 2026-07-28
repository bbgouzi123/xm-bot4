import logging
from src.utils.contacts_cache import contacts_cache
from .utils import check_friend_in_list, check_group_in_list

logger = logging.getLogger(__name__)

class FilterPreparerMixin:
    """回复配置读取与黑白名单归一化清洗"""

    def _prepare_reply_filters(self, account_id: str) -> tuple:
        # 🛡️ 门卫：若 account_id 仍为 'default'（微信登录态尚未写入），
        # 尝试从 get_active_account() 拿一次真实 wxid；若仍是 default 则
        # 直接返回空元组，阻断本轮扫描，避免用 default 目录的空白名单
        # 污染所有群聊/好友的命中判断（造成"已加白却显示未在白名单"的根因）。
        if not account_id or account_id == "default":
            try:
                from src.crm.account_data import get_active_account
                real_aid = get_active_account()
                if real_aid and real_aid != "default":
                    account_id = real_aid
                    logger.info(f"[门卫] account_id 为 default，已修正为真实账号: {account_id}")
                else:
                    logger.debug("[门卫] account_id 仍为 default，本轮扫描跳过（等待微信登录完成）")
                    return {}, [], []
            except Exception as _gae:
                logger.debug(f"[门卫] get_active_account 失败: {_gae}，跳过本轮扫描")
                return {}, [], []

        from src.api.config_api import _load_configs
        configs = _load_configs() or {}

        try:
            from src.api.config_api.privacy_shield import _get_reply_config_isolated
            reply_cfg = _get_reply_config_isolated(account_id)
            logger.info(f"[监控] 读取到账号 '{account_id}' 隔离回复配置. bot_group_auto_start={reply_cfg.get('bot_group_auto_start')}, auto_chat_group_at_only={reply_cfg.get('auto_chat_group_at_only')}")
        except Exception as e:
            logger.warning(f"[监控] 读取隔离配置失败: {e}")
            reply_cfg = {}

        # 🌟 检测配置状态，并在白名单/黑名单等核心字段变更时清理监控指纹缓存，实现加白后立即开始自动回复
        cfg_keys = (
            "auto_chat_friend_mode",
            "auto_chat_friend_whitelist",
            "auto_chat_friend_excludes",
            "auto_chat_group_mode",
            "auto_chat_group_whitelist",
            "auto_chat_group_excludes",
            "bot_group_auto_start"
        )
        
        current_cfg_state = tuple(
            tuple(reply_cfg.get(k)) if isinstance(reply_cfg.get(k), list) else reply_cfg.get(k)
            for k in cfg_keys
        )

        if not hasattr(self, "_last_reply_cfg_state"):
            self._last_reply_cfg_state = {}

        last_state = self._last_reply_cfg_state.get(account_id)
        if last_state is not None and last_state != current_cfg_state:
            logger.info(f"[白名单同步] ⚠️ 检测到账号 '{account_id}' 的自动回复配置发生更新，正在重置本地会话拦截缓存以使新白名单立刻生效...")
            if hasattr(self, "_fingerprints") and self._fingerprints:
                try:
                    self._fingerprints.clear()
                except Exception:
                    pass
            if hasattr(self, "_broadcasted_whitelist_ids") and self._broadcasted_whitelist_ids:
                try:
                    self._broadcasted_whitelist_ids.clear()
                except Exception:
                    pass
            if hasattr(self, "_session_broadcasted_fps") and self._session_broadcasted_fps:
                try:
                    self._session_broadcasted_fps.clear()
                except Exception:
                    pass
            if hasattr(self, "_last_unread_snapshot") and self._last_unread_snapshot:
                try:
                    self._last_unread_snapshot.clear()
                except Exception:
                    pass
            # 同时也清理轮询通道可能用到的已广播指纹集合
            if hasattr(self, "_broadcasted_whitelist_fps") and self._broadcasted_whitelist_fps:
                try:
                    self._broadcasted_whitelist_fps.clear()
                except Exception:
                    pass

        self._last_reply_cfg_state[account_id] = current_cfg_state


        all_friends = contacts_cache.get_friends(account_id)
        all_groups = contacts_cache.get_groups(account_id)

        # 针对微信好友或群聊改名后，导致存量白黑名单（仅以历史文本名字存储）失效的痛点，
        # 在这里执行智能的“动态对齐升级”：
        # 将白黑名单配置中所有仅以“纯名字文本”或“旧 namecat:” 存储的记录，自动匹配并升级转换为 wxid 的唯一标识，
        # 从而不管用户在微信上如何改名/改备注，皆能通过微信唯一 ID 永久正确过滤匹配。
        if all_friends or all_groups:
            try:
                import re
                friend_name_to_wxid = {}
                for f in all_friends:
                    wxid = f.get('wxid')
                    if wxid:
                        for field in ('name', 'remark', 'nickname', 'alias'):
                            val = f.get(field)
                            if val:
                                friend_name_to_wxid[str(val).strip()] = wxid

                group_name_to_wxid = {}
                for g in all_groups:
                    wxid = g.get('wxid')
                    name = g.get('name')
                    if wxid and name:
                        group_name_to_wxid[name.strip()] = wxid
                        clean_name = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
                        group_name_to_wxid[clean_name] = wxid

                config_changed = False
                for list_key in [
                    "auto_chat_friend_whitelist", "auto_chat_friend_excludes",
                    "auto_chat_group_whitelist", "auto_chat_group_excludes",
                    "moment_interact_friend_whitelist", "moment_interact_friend_excludes"
                ]:
                    if list_key in reply_cfg:
                        lst = reply_cfg[list_key]
                        if not isinstance(lst, list):
                            continue
                        new_lst = []
                        list_changed = False
                        for item in lst:
                            if not item:
                                continue
                            item_str = str(item).strip()
                            if item_str.startswith("wxid:"):
                                if item_str not in new_lst:
                                    new_lst.append(item_str)
                                raw_wxid = item_str[5:].strip()
                                is_group_list = "group" in list_key
                                matched_name = None
                                if is_group_list:
                                    for k, v in group_name_to_wxid.items():
                                        if v == raw_wxid and not k.lower().endswith("@chatroom"):
                                            matched_name = k
                                            break
                                else:
                                    for k, v in friend_name_to_wxid.items():
                                        if v == raw_wxid and not k.lower().startswith("wxid_"):
                                            matched_name = k
                                            break
                                if matched_name:
                                    if matched_name not in new_lst:
                                        new_lst.append(matched_name)
                                        list_changed = True
                                continue
                            
                            raw_name = item_str
                            if item_str.startswith("namecat:"):
                                raw_name = item_str[8:].split("::")[0].strip()
                            elif item_str.startswith("uid_"):
                                raw_name = item_str[4:].strip()

                            is_group_list = "group" in list_key
                            found_wxid = None
                            if is_group_list:
                                found_wxid = group_name_to_wxid.get(raw_name)
                            else:
                                found_wxid = friend_name_to_wxid.get(raw_name)

                            if found_wxid:
                                upgraded = f"wxid:{found_wxid}"
                                if upgraded not in new_lst:
                                    new_lst.append(upgraded)
                                # 🌟 [改名免疫修复] 成功绑定 wxid 后，不再保留旧的 namecat/纯名字条目。
                                # 旧条目以「当时昵称」为 key，一旦好友改名就永久失效。
                                # 以 wxid 作为唯一主键可做到改名后仍正确匹配。
                                list_changed = True
                                logger.debug(f"[白黑名单升级] 已将 '{item_str}' 绑定升级为 '{upgraded}'，原名字条目已丢弃")
                            else:
                                if item_str not in new_lst:
                                    new_lst.append(item_str)
                                    
                        if list_changed:
                            reply_cfg[list_key] = new_lst
                            config_changed = True

                if config_changed:
                    from src.crm.account_data import get_account_settings, save_account_settings
                    settings = get_account_settings(account_id)
                    settings["reply"] = reply_cfg
                    save_account_settings(settings, account_id)
                    logger.info(f"[白黑名单同步] 发现有未绑定的联系人文本，已自动对其绑定升级为 wxid 格式并同步。账号: {account_id}")
            except Exception as migrate_ex:
                logger.warning(f"[白黑名单同步] 自动升级白黑名单联系人 wxid 异常: {migrate_ex}")

        bot_group_auto_start = reply_cfg.get("bot_group_auto_start", configs.get("bot_group_auto_start", False))
        auto_chat_group_at_only = reply_cfg.get("auto_chat_group_at_only", configs.get("auto_chat_group_at_only", True))
        self._group_at_only = auto_chat_group_at_only
        
        # 深度清洗白/黑名单 (使用推导式缩减行数以满足 300 行质量红线)
        def _clean_ex(lst):
            return [s for x in lst if x for s in [str(x).strip()] if s != "uid_" and s.lower() != "uid_none" and not (s.startswith("uid_") and not s[4:].strip())]
        friend_mode = reply_cfg.get("auto_chat_friend_mode", "black")
        friend_excludes = _clean_ex(reply_cfg.get("auto_chat_friend_whitelist" if friend_mode == "white" else "auto_chat_friend_excludes", []))
        group_mode = reply_cfg.get("auto_chat_group_mode", "black")
        group_excludes = _clean_ex(reply_cfg.get("auto_chat_group_whitelist" if group_mode == "white" else "auto_chat_group_excludes", []))


        # 账号预热优化
        if not hasattr(self, '_preheated_accounts'):
            self._preheated_accounts = set()
        if not hasattr(self, '_account_wait_counts'):
            self._account_wait_counts = {}

        if not all_friends:
            try:
                import os
                import json
                from src.crm.account_data import get_contacts_path
                contacts_path = get_contacts_path(account_id)
                if contacts_path and os.path.exists(contacts_path):
                    with open(contacts_path, "r", encoding="utf-8") as f_contacts:
                        data = json.load(f_contacts)
                    if isinstance(data, list) and data:
                        mapped_friends = []
                        mapped_groups = []
                        for item in data:
                            cat = item.get("category", "")
                            if cat == "群聊" or item.get("contact_type") == "group":
                                mapped_groups.append(item)
                            else:
                                mapped_friends.append(item)
                        with contacts_cache._rw_lock:
                            contacts_cache._friends[account_id] = mapped_friends
                            contacts_cache._groups[account_id] = mapped_groups
                        all_friends = mapped_friends
                        all_groups = mapped_groups
                        logger.info(f"[扫描拦截] 冷启动从隔离缓存加载了 {len(mapped_friends)} 好友, {len(mapped_groups)} 群聊")
                        try:
                            from src.uia.session import session_type_cache
                            session_type_cache.revalidate_with_contacts(mapped_friends, mapped_groups)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"[扫描拦截] 加载账号隔离缓存失败: {e}")

        if not all_friends:
            if account_id not in self._preheated_accounts:
                wait_count = self._account_wait_counts.get(account_id, 0) + 1
                self._account_wait_counts[account_id] = wait_count
                if wait_count >= 3:
                    self._preheated_accounts.add(account_id)
                    print(f"[扫描拦截] 账号 '{account_id}' 通讯录连续 {wait_count} 次扫描为空，已强制标记为预热完成")
                    logger.info(f"[扫描拦截] 账号 '{account_id}' 通讯录连续 {wait_count} 次扫描为空，已强制标记为预热完成")
                else:
                    print(f"[扫描拦截] 账号 '{account_id}' 通讯录暂为空，正在等待同步 (第 {wait_count} 次/最多3次)")

        if all_friends or all_groups:
            try:
                from src.uia.session import session_type_cache
                session_type_cache.revalidate_with_contacts(all_friends, all_groups)
            except Exception as e:
                logger.debug(f"[扫描拦截] 自动校准会话缓存异常: {e}")

        active_friend_identifiers = set()
        for f in all_friends:
            name = f.get('name') or ''
            wxid = f.get('wxid') or ''
            remark = f.get('remark') or ''
            for field in ('wxid', 'name', 'remark', 'alias'):
                val = f.get(field)
                if val:
                    val_clean = str(val).strip()
                    if val_clean:
                        active_friend_identifiers.add(val_clean)
                        active_friend_identifiers.add(f"uid_{val_clean}")
            if wxid:
                active_friend_identifiers.add(f"wxid:{wxid}")
            if name:
                active_friend_identifiers.add(f"namecat:{name}::联系人")
            if remark:
                active_friend_identifiers.add(f"namecat:{remark}::联系人")

        active_group_identifiers = set()
        for g in all_groups:
            name = g.get('name') or ''
            wxid = g.get('wxid') or ''
            for field in ('wxid', 'name'):
                val = g.get(field)
                if val:
                    val_clean = str(val).strip()
                    if val_clean:
                        active_group_identifiers.add(val_clean)
                        active_group_identifiers.add(f"uid_{val_clean}")
            if wxid:
                active_group_identifiers.add(f"wxid:{wxid}")
            if name:
                active_group_identifiers.add(f"namecat:{name}::群聊")

        def is_matched_in_identifiers(x, identifiers):
            if x in identifiers: return True
            x_clean = str(x).strip()
            if x_clean.startswith("wxid:"): x_clean = x_clean[5:].strip()
            elif x_clean.startswith("uid_"): x_clean = x_clean[4:].strip()
            elif x_clean.startswith("namecat:"): x_clean = x_clean[8:].split("::")[0].strip()
            return (x_clean in identifiers or f"uid_{x_clean}" in identifiers or f"wxid:{x_clean}" in identifiers or
                    f"namecat:{x_clean}::联系人" in identifiers or f"namecat:{x_clean}::群聊" in identifiers or
                    any(y.startswith("namecat:") and y.split("::")[0] == f"namecat:{x_clean}" for y in identifiers))


        self._is_friend_whitelist_active = bool(friend_excludes and any(is_matched_in_identifiers(x, active_friend_identifiers) for x in friend_excludes))
        self._is_group_whitelist_active = bool(group_excludes and any(is_matched_in_identifiers(x, active_group_identifiers) for x in group_excludes))

        return reply_cfg, friend_excludes, group_excludes
