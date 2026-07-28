import os
import json
import hashlib
import logging
from typing import Optional, List

logger = logging.getLogger("WcdbMonitorHelpers")

def make_fingerprint(name: str, content: str) -> str:
    return hashlib.md5(f"{name}:WCDB:{content}".encode()).hexdigest()

def resolve_display_name(account_id: str, session_id: str, is_group: bool) -> Optional[str]:
    try:
        from src.utils.contacts_cache import contacts_cache
        account_id = account_id or "default"
        if is_group:
            groups = contacts_cache.get_groups(account_id)
            for g in groups:
                if g.get("wxid") == session_id:
                    return g.get("name", "")
        else:
            friends = contacts_cache.get_friends(account_id)
            for f in friends:
                if f.get("wxid") == session_id:
                    return f.get("name") or f.get("remark") or f.get("nickname") or f.get("alias") or ""

        # 冷启动降级物理读取
        from src.crm.account_data import get_contacts_path
        local_path = get_contacts_path(account_id)
        if local_path and os.path.exists(local_path):
            with open(local_path, "r", encoding="utf-8") as f_contacts:
                data = json.load(f_contacts)
            if isinstance(data, list):
                for item in data:
                    if item.get("wxid") == session_id:
                        return item.get("name") or item.get("remark") or item.get("nickname") or item.get("alias") or ""
    except Exception as e:
        logger.debug(f"[WCDB协调器] 联系人解析失败: {e}")
    return session_id

async def broadcast_blocked_session(scanner, name: str, is_group: bool, wxid: str, content: str):
    """将拦截状态实时广播给控制中心"""
    scanner._update_overlay_and_broadcast_whitelist(name, is_group=is_group, wxid=wxid, incoming_msg=content)
    from src.utils.websocket_manager import ws_manager
    task_key = f"whitelist_{wxid}"
    if hasattr(ws_manager, "task_cache") and task_key in ws_manager.task_cache:
        ws_manager.task_cache[task_key]["data"]["incoming_msg"] = content

async def inject_to_reply_queue(scanner, name: str, content: str, is_group: bool, wxid: str):
    """在主事件循环中将消息注入回复队列"""
    import time
    if not scanner.is_running():
        logger.info(f"[WCDB双引擎] 消息注入已跳过: 扫描器已停止运行 (name={name})")
        return

    cooldown = getattr(scanner, '_cooldown', 10)
    last_reply = scanner._last_reply_time.get(name, 0) if hasattr(scanner, '_last_reply_time') else 0
    diff = time.time() - last_reply
    if diff < cooldown:
        logger.info(f"[WCDB双引擎] 消息注入已跳过: 会话仍在冷却中 (已过 {diff:.1f}s, 冷却 {cooldown}s) (name={name})")
        return

    if hasattr(scanner, '_human_takeover_sessions') and name in scanner._human_takeover_sessions:
        logger.info(f"[WCDB双引擎] 消息注入已跳过: 该会话处于人工托管接管状态 (name={name})")
        return
    if hasattr(scanner, '_is_session_processing') and scanner._is_session_processing(name, wxid):
        logger.info(f"[WCDB双引擎] 消息注入已避让: 会话正在回复处理中 (name={name}, wxid={wxid})，不重复触发，由缓冲区排队兜底")
        return
    elif hasattr(scanner, '_processing') and (name in scanner._processing or (wxid and wxid in scanner._processing)):
        logger.info(f"[WCDB双引擎] 消息注入已避让: 会话处理标志活跃 (name={name}, wxid={wxid})，由缓冲区排队兜底")
        return

    fp = make_fingerprint(name, content)
    
    # 🌟 同时校验 wxid 和 name 下的指纹缓存，杜绝由于键名不一致导致的重复注入
    existing_fps = set()
    if hasattr(scanner, '_fingerprints'):
        if wxid:
            existing_fps.update(scanner._fingerprints.get(wxid, set()))
        if name:
            existing_fps.update(scanner._fingerprints.get(name, set()))
            
    if fp in existing_fps:
        logger.debug(f"[WCDB双引擎] 消息注入已跳过: 指纹重复 (name={name}, wxid={wxid}, fp={fp})")
        return

    # 🐛 [双发修复] 检查缓冲区互斥：若轮询通道（db_unread_syncer）已将此会话入队，
    # DLL 实时通道不重复注入，防止两个通道并发触发两次 AI 回复。
    buf_key = wxid or name
    if hasattr(scanner, '_message_buffer') and buf_key in scanner._message_buffer:
        logger.debug(f"[WCDB双引擎] 消息注入已跳过: 轮询通道已将会话 '{name}' (wxid={wxid}) 入队，DLL 通道跳过")
        return

    is_at_flag = False
    if is_group:
        try:
            from src.monitor.chat_monitor.check_utils import check_is_at_message
            from src.api.config_api import _load_configs
            reply_cfg = _load_configs().get("reply", {})
            # 剥离可能存在的 "sender_wxid:\n" 前缀
            msg_body = content
            import re as _re
            m_sender = _re.match(r"^([a-zA-Z0-9_\-]+):\s*\n(.*)$", content, _re.DOTALL)
            if m_sender:
                msg_body = m_sender.group(2)
            
            is_valid_receipt = False
            if hasattr(scanner, "_check_group_receipt"):
                is_valid_receipt = scanner._check_group_receipt(msg_body, is_group, reply_cfg)
            if is_valid_receipt or check_is_at_message(msg_body, scanner.driver, scanner.account_id, reply_cfg):
                is_at_flag = True

            # 🌟 群聊仅艾特前置过滤：若开启了仅在被艾特时自动回复，且未判定为艾特，直接跳过不入回复队列
            auto_chat_group_at_only = reply_cfg.get("auto_chat_group_at_only", True)
            if auto_chat_group_at_only and not is_at_flag:
                # 写入指纹以防重复触发
                if hasattr(scanner, '_fingerprints'):
                    scanner._fingerprints.setdefault(wxid or name, set()).add(fp)
                    scanner._fingerprints.setdefault(name, set()).add(fp)
                logger.info(f"[WCDB双引擎] 群聊 '{name}' (wxid={wxid}) 开启了仅@模式，且该最新消息未被@，跳过注入")
                return
        except Exception as filter_ex:
            logger.warning(f"[WCDB双引擎] 实时消息判断是否为 @ 消息异常: {filter_ex}")

    scanner._enqueue_to_reply_buffer(
        name=name, last_msg=content, is_group=is_group,
        user_name=name, is_at=is_at_flag, fp=fp, wxid=wxid
    )
    logger.info(f"[WCDB双引擎] ✅ 已将 '{name}' 的消息注入回复队列 (is_at={is_at_flag}). 指纹: {fp}")


def resolve_target_pid(wxid: str) -> Optional[int]:
    try:
        from src.utils.instance_manager import InstanceManagerV2
        import win32process
        import re
        import psutil

        manager = InstanceManagerV2.get_instance()
        instances = manager.get_all_instances()
        matched_inst_id = None
        hwnd = None

        for inst_id, inst in instances.items():
            if inst_id == wxid or inst.get("wxid") == wxid:
                matched_inst_id = inst_id
                hwnd = inst.get("window_handle")
                break

        if hwnd:
            try:
                _, wnd_pid = win32process.GetWindowThreadProcessId(int(hwnd))
                if wnd_pid > 0:
                    return wnd_pid
            except Exception:
                pass

        if matched_inst_id:
            m = re.search(r"\d+", matched_inst_id)
            if m:
                inst_idx = m.group(0)
                for p in psutil.process_iter(['pid', 'name', 'environ']):
                    try:
                        name = p.info.get('name') or ''
                        if name.lower() in ('wechat.exe', 'weixin.exe'):
                            env_vars = p.info.get('environ') or {}
                            if env_vars.get("XM_WECHAT_INSTANCE") == str(inst_idx):
                                return p.info['pid']
                    except Exception:
                        pass
    except Exception:
        pass
    return None


def check_is_blocked(scanner, account_id: str, name: str, session_id: str, is_group: bool, content: str, loop) -> tuple:
    """白名单/黑名单拦截判定，返回 (is_blocked, reply_cfg)。
    提取自 _on_wcdb_message，避免主文件超过 300 行限制。"""
    from src.monitor.chat_monitor.message_scanner.utils import check_friend_in_list, check_group_in_list
    from .redpacket_helper import try_trigger_redpacket
    from src.api.config_api import _load_configs
    from src.api.instance_settings_api import load_instance_settings

    reply_cfg, friend_excludes, group_excludes = scanner._prepare_reply_filters(account_id)
    try_trigger_redpacket(scanner, loop, name, session_id, content, is_group, reply_cfg)

    configs = _load_configs() or {}
    inst_settings = load_instance_settings(account_id) or {}
    auto_reply_enabled = configs.get("auto_reply_enabled", True) and inst_settings.get("auto_reply_enabled", True)

    is_blocked = False
    if auto_reply_enabled:
        if is_group:
            bot_group_auto_start = reply_cfg.get("bot_group_auto_start", False)
            if bot_group_auto_start:
                group_mode = reply_cfg.get("auto_chat_group_mode", "black")
                in_list = check_group_in_list(name, session_id, group_excludes, account_id=account_id)
                is_blocked = (group_mode == "white" and not in_list) or (group_mode == "black" and in_list)
            else:
                is_blocked = True
        else:
            friend_mode = reply_cfg.get("auto_chat_friend_mode", "black")
            in_list = check_friend_in_list(name, session_id, friend_excludes, account_id=account_id)
            is_blocked = (friend_mode == "white" and not in_list) or (friend_mode == "black" and in_list)

    return is_blocked, reply_cfg


def _persist_to_instance_settings(wxid: str, db_path: str, hex_key: str):
    try:
        from src.api.instance_settings_api import load_instance_settings, save_instance_settings
        _s = load_instance_settings(wxid)
        _dir = ""
        if db_path:
            _p = os.path.abspath(db_path)
            if os.path.isfile(_p):
                _p = os.path.dirname(_p)
            if os.path.basename(_p).lower() in ("session", "contact", "message", "head_image", "general", "sns", "favorite", "emoticon"):
                _dir = os.path.dirname(_p)
            else:
                _dir = _p
        _updates = {}
        if not _s.get("wechat_data_dir") and _dir: _updates["wechat_data_dir"] = _dir
        if not _s.get("wechat_hex_key") and hex_key and len(hex_key) == 64: _updates["wechat_hex_key"] = hex_key
        if _updates:
            _s.update(_updates)
            save_instance_settings(wxid, _s)
            logger.info(f"[WCDB协调器] 已将 db_path 和密钥自动回写至 instance_settings (wxid={wxid})")
    except Exception as _e:
        logger.debug(f"[WCDB协调器] 自动回写 instance_settings 失败（非严重）: {_e}")


def _is_multi_open_mode() -> bool:
    try:
        from src.utils.instance_manager import InstanceManagerV2
        _all = InstanceManagerV2.get_instance().get_all_instances()
        _dead = {"stopped", "error", "idle"}
        return sum(1 for _i in _all.values() if _i.get("status") not in _dead and _i.get("wxid")) > 1
    except Exception:
        return False

