import time
import logging
import asyncio
import re
from src.utils.contacts_cache import contacts_cache
from src.utils.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

def handle_self_sent_and_takeover(self, name, last_msg, is_group, active_name, active_last_msgs, account_id, unread_count, target_wxid, session, friend_name_to_wxid, group_name_to_wxid, fp):
    is_self_sent = self._check_is_self_sent(name, last_msg, is_group, active_name, active_last_msgs, account_id, unread_count=unread_count, target_wxid=target_wxid)
    if is_self_sent:
        partition = self.get_account_partition(account_id)
        is_auto = self._is_auto_reply_message(name, last_msg, partition)
        if not is_auto:
            logger.info(f"[人工介入探测] 检测到操作员手动发送消息: '{last_msg}'，自动挂起并接管该会话")
            is_group = self._resolve_is_group(session, name, last_msg, friend_name_to_wxid, group_name_to_wxid)
            target_wxid = group_name_to_wxid.get(name, "") if is_group else friend_name_to_wxid.get(name, "")
            target_key = target_wxid or name
            
            if target_key:
                contacts_cache.update_friend(account_id, target_key, is_takeover=True)
                contacts_cache.merge_friend_detail_by_name(account_id, name, "群聊" if is_group else "联系人", is_takeover=True)
            
            self._human_takeover_sessions.add(name)
            if target_wxid:
                self._human_takeover_sessions.add(target_wxid)
            
            try:
                ws_content = f"检测到操作员手动回复消息，已自动切换为【人工接管】状态"
                asyncio.create_task(ws_manager.broadcast_alert(level="info", title="👤 会话已人工接管", content=ws_content))
            except Exception:
                pass

        logger.info(f"[自回复拦截] 检测到最后一条消息为自己发送，跳过回复: '{last_msg}'")
        self._fingerprints.setdefault(name, set()).add(fp)
        return True
    return False

def handle_unread_fingerprint_check(self, name, fp, session, reply_cfg, is_group, friend_excludes, group_excludes, friend_name_to_wxid, group_name_to_wxid, account_id, is_friend_accept_notify, target_wxid) -> bool:
    u_now = int(session.get('unread', 0) or 0)
    prev = self._last_unread_snapshot.get(name)
    if u_now > 0 and prev is not None and int(prev) == 0:
        logger.info(f"[监控] 会话 '{name}' 未读数从 0 跳到 {u_now}，强制放行")
        self._fingerprints[name].discard(fp)
        self._manual_interventions.pop(name, None)
        if hasattr(self, '_broadcasted_whitelist_ids'):
            getattr(self, '_session_broadcasted_fps', {}).pop(target_wxid or name, None)
            self._broadcasted_whitelist_ids.discard(f"whitelist_{target_wxid or name}")
        return False
    elif u_now > 0 and (
        name not in self._last_reply_time
        or time.time() - self._last_reply_time.get(name, 0) > self._cooldown
    ):
        from .utils import check_friend_in_list, check_group_in_list
        is_allowed = True
        if is_group:
            bot_group_auto_start = reply_cfg.get("bot_group_auto_start", False)
            if bot_group_auto_start:
                group_mode = reply_cfg.get("auto_chat_group_mode", "black")
                clean_n = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
                g_wxid = group_name_to_wxid.get(clean_n, "") or group_name_to_wxid.get(name, "")
                in_group_list = check_group_in_list(name, g_wxid, group_excludes, account_id=account_id) or check_group_in_list(clean_n, g_wxid, group_excludes, account_id=account_id)
                if group_mode == "white" and not in_group_list:
                    wl_task_id = f"whitelist_{g_wxid or name}"
                    broadcasted_ids = getattr(self, '_broadcasted_whitelist_ids', set())
                    if wl_task_id not in broadcasted_ids:
                        logger.warning(
                            f"[评估白名单诊断] 群聊 '{name}' 未通过放行判定！"
                            f"name_repr={repr(name)}, clean_n_repr={repr(clean_n)}, "
                            f"g_wxid={repr(g_wxid)}, group_excludes={repr(group_excludes)}, "
                            f"group_mode={repr(group_mode)}"
                        )
                    else:
                        logger.debug(
                            f"[评估白名单诊断] 群聊 '{name}' 未通过放行判定！"
                            f"name_repr={repr(name)}, clean_n_repr={repr(clean_n)}, "
                            f"g_wxid={repr(g_wxid)}, group_excludes={repr(group_excludes)}, "
                            f"group_mode={repr(group_mode)}"
                        )
                    is_allowed = False
            else:
                is_allowed = False
        else:
            f_wxid = friend_name_to_wxid.get(name, "")
            friend_mode = reply_cfg.get("auto_chat_friend_mode", "black")
            in_friend_list = check_friend_in_list(name, f_wxid, friend_excludes, account_id=account_id)
            if friend_mode == "white" and not in_friend_list and not is_friend_accept_notify:
                is_allowed = False
        
        if is_allowed:
            if hasattr(self, "_replied_fingerprints") and fp in self._replied_fingerprints:
                logger.info(f"[防假未读死循环] 会话 '{name}' 的指纹 {fp[:8]}... 已经成功自动回复过，拦截二次 discard 放行")
                return True
            print(f"[{name}] 持久未读红点且未回复/已过冷却，强制重新放行")
            self._fingerprints[name].discard(fp)
        else:
            logger.debug(f"[评估] 会话 '{name}' 不在自动回复放行名单，维持指纹拦截，防止无限刷新")
            return True
    else:
        logger.debug(f"[监控] 会话 '{name}' 指纹已存在，未读数={u_now}，频控跳过")
        return True
    return False
