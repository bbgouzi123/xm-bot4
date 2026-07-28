import logging
import re
from typing import Any
from src.utils.license_validator import LicenseValidator
from src.utils.websocket_manager import ws_manager
from src.utils.uia_task_runner import is_uia_maintenance_active
from .message_scanner import check_friend_in_list, check_group_in_list
from .base import _chat_daily_counter

logger = logging.getLogger(__name__)

async def _skip_and_notify(engine: Any, task_id: str, name: str, incoming_msg: str, message: str) -> bool:
    engine._stats["skipped"] = engine._stats.get("skipped", 0) + 1
    await ws_manager.broadcast_task_update(
        task_id=task_id,
        task_type="自动回复",
        status="completed",
        progress=100,
        total=100,
        message=message,
        friend_name=name,
        incoming_msg=incoming_msg
    )
    return False

async def check_reply_preconditions(engine: Any, name: str, message: str, is_group: bool = False, is_at: bool = False, wxid: str = None) -> tuple[bool, str]:
    """检查自动回复的前置条件，返回 (should_reply, actual_message)"""
    from src.uia.session import session_type_cache, SYSTEM_ACCOUNTS
    task_id = f"auto_reply_{wxid or name}"

    if "[名片]" in message or "[个人名片]" in message or "个人名片" in message:
        await _skip_and_notify(engine, task_id, name, message, "名片消息，已跳过回复")
        return False, message
    if name in SYSTEM_ACCOUNTS or name.startswith("折叠的聊天"):
        await _skip_and_notify(engine, task_id, name, message, "微信公众号/服务号，已跳过回复")
        return False, message
    if session_type_cache.get_type(name) == "official_account":
        from src.utils.contacts_cache import contacts_cache as _cc
        _aid0 = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
        _ns = name.strip()
        _known = (any((_f.get('name') or '').strip() == _ns or (_f.get('remark') or '').strip() == _ns for _f in (_cc.get_friends(_aid0) or []))
                  or any((_g.get('name') or '').strip() == _ns for _g in (_cc.get_groups(_aid0) or [])))
        if _known:
            session_type_cache.cache.pop(name, None); session_type_cache.save()
        else:
            await _skip_and_notify(engine, task_id, name, message, "微信公众号/服务号，已跳过回复")
            return False, message

    account_id = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None)
    if not account_id or account_id == 'default':
        try:
            from src.crm.account_data import get_active_account
            _active = get_active_account()
            if _active and _active != 'default':
                account_id = _active
        except Exception:
            pass
    account_id = account_id or 'default'
    sub_info = LicenseValidator.check_subscription()
    
    # 🛡️ 拦截未登录状态
    user_id = LicenseValidator._get_sso_user_id()
    if not user_id:
        logger.warning(f"[前置拦截] 账号 '{account_id}' 未登录 RPA，已拦截自动回复。")
        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100,
            message="软件未登录，请先在控制中心登录账号", friend_name=name, incoming_msg=message
        )
        return False, message

    # 🛡️ 拦截授权过期状态
    if sub_info.get("status") in ("trial_expired", "expired"):
        logger.warning(f"[前置拦截] 账号 '{account_id}' 授权已到期，已拦截自动回复。")
        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100,
            message="软件授权已过期，请在控制中心升级续费套餐", friend_name=name, incoming_msg=message
        )
        return False, message

    ai_limit = sub_info.get("ai_daily_limit", 30)

    if ai_limit > 0:
        try:
            status_res = LicenseValidator._http_request(
                "POST",
                "/api/ai/quota/status",
                {"user_id": LicenseValidator._get_sso_user_id(), "wechat_id": account_id}
            )
            if status_res and status_res.get("success"):
                data = status_res.get("data", {})
                total_available = data.get("daily_remaining", 0) + data.get("extra_balance", 0)
                if total_available <= 0:
                    engine._stats.update({"skipped": engine._stats.get("skipped", 0) + 1, "quota_exhausted": True})
                    logger.warning(f"[前置拦截] 账号 '{account_id}' 已无可用额度。")
                    await ws_manager.broadcast_task_update(
                        task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100,
                        message="自动回复额度及增值包均已用尽，请联系管理员充值", friend_name=name, incoming_msg=message
                    )
                    return False, message
            else:
                used_today = _chat_daily_counter.get_count("auto_reply", account_id)
                if used_today >= ai_limit:
                    engine._stats.update({"skipped": engine._stats.get("skipped", 0) + 1, "quota_exhausted": True})
                    await ws_manager.broadcast_task_update(task_id=task_id, task_type="自动回复", status="completed", progress=100, total=100, message="自动回复额度已满，请联系管理员充值", friend_name=name, incoming_msg=message)
                    return False, message
        except Exception as ext_err:
            logger.warning(f"[配额校验] 云端额度查询异常: {ext_err}，降级到本地判定")
            used_today = _chat_daily_counter.get_count("auto_reply", account_id)
            if used_today >= ai_limit:
                engine._stats.update({"skipped": engine._stats.get("skipped", 0) + 1, "quota_exhausted": True})
                return False, message

    from src.utils.contacts_cache import contacts_cache
    if is_uia_maintenance_active():
        await _skip_and_notify(engine, task_id, name, message, "系统正在维护，已跳过回复")
        return False, message
    if any(f.get("is_takeover", False) for f in contacts_cache.get_friends(account_id) if f.get("name") == name or f.get("wxid") == name or (wxid and f.get("wxid") == wxid)):
        await _skip_and_notify(engine, task_id, name, message, "人工已接管该会话，已跳过回复")
        return False, message

    from src.api.config_api import _load_configs
    from src.api.instance_settings_api import load_instance_settings
    configs = _load_configs() or {}
    inst_settings = load_instance_settings(account_id)
    if not configs.get("auto_reply_enabled", True) or not inst_settings.get("auto_reply_enabled", True):
        await _skip_and_notify(engine, task_id, name, message, "自动回复开关已关闭，已跳过回复")
        return False, message

    from src.utils.uia_task_runner import is_engine_suspended, is_session_fused
    if is_engine_suspended():
        await _skip_and_notify(engine, task_id, name, message, "全局引擎已挂起，已跳过回复")
        return False, message

    if is_session_fused(name) or (wxid and is_session_fused(wxid)):
        await _skip_and_notify(engine, task_id, name, message, "该会话已熔断，已跳过回复")
        return False, message

    from src.task.auto_follow_daemon import is_session_locked
    if is_session_locked(name) or (wxid and is_session_locked(wxid)):
        await _skip_and_notify(engine, task_id, name, message, "SDR 销售助手跟单中，已跳过回复")
        return False, message

    # ── 第二道防线：白黑名单 + 开关 ──────────────────────────────────────────
    try:
        from src.api.config_api.privacy_shield import _get_reply_config_isolated
        reply_cfg = _get_reply_config_isolated(account_id)

        # ── 暂停值守状态拦截 ──
        from .group_invite_handler import is_bot_paused
        if is_bot_paused(account_id):
            admin_wxid = reply_cfg.get("delegated_admin_wxid", "")
            if not (admin_wxid and (wxid == admin_wxid or name == admin_wxid)):
                await _skip_and_notify(engine, task_id, name, message, "系统处于暂停回复状态")
                return False, message

        if is_group:
            bot_group_auto_start = reply_cfg.get("bot_group_auto_start", False)
            if not bot_group_auto_start:
                logger.info(f"[前置拦截·二次] 群聊 '{name}' 自动回复总开关未开启，已跳过")
                await _skip_and_notify(engine, task_id, name, message, "群聊自动回复未开启，已跳过")
                return False, message

            group_mode = reply_cfg.get("auto_chat_group_mode", "black")
            raw_groups = reply_cfg.get("auto_chat_group_whitelist" if group_mode == "white" else "auto_chat_group_excludes", []) or []
            group_list = [x for x in (str(x).strip() for x in raw_groups if x) if x and x != "uid_"]
            clean_name = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
            
            g_wxid = wxid.strip() if wxid else ""
            if not g_wxid:
                all_groups = contacts_cache.get_groups(account_id)
                for g in all_groups:
                    g_w = (g.get("wxid") or "").strip()
                    g_n = (g.get("name") or "").strip()
                    if g_w == name.strip() or g_w == clean_name:
                        g_wxid = g_w
                        break
                    if g_n in (name.strip(), clean_name):
                        g_wxid = g_w
                        break

            in_list = check_group_in_list(name, g_wxid, group_list, account_id=account_id) or check_group_in_list(clean_name, g_wxid, group_list, account_id=account_id)

            if group_mode == "white" and not in_list:
                from .whitelist_sync_helper import try_sync_group_whitelist
                in_list = await try_sync_group_whitelist(engine, name, clean_name, group_list, account_id)

            if group_mode == "white" and not in_list:
                logger.warning(f"[前置拦截·二次·未匹配] 群聊名: '{name}', 目标白名单: {group_list}")
                await _skip_and_notify(engine, task_id, name, message, "群聊不在白名单中，已跳过")
                return False, message
            elif group_mode == "black" and in_list:
                logger.info(f"[前置拦截·二次·拦截] 群聊 '{name}' 在黑名单中，已跳过")
                await _skip_and_notify(engine, task_id, name, message, "群聊在黑名单中，已跳过")
                return False, message
        else:
            friend_mode = reply_cfg.get("auto_chat_friend_mode", "black")
            raw_friends = reply_cfg.get("auto_chat_friend_whitelist" if friend_mode == "white" else "auto_chat_friend_excludes", []) or []
            friend_list = [x for x in (str(x).strip() for x in raw_friends if x) if x and x != "uid_"]

            f_wxid = wxid.strip() if wxid else ""
            if not f_wxid:
                _n = name.strip()
                for f in contacts_cache.get_friends(account_id):
                    f_w = (f.get("wxid") or "").strip()
                    if f_w == _n or (f.get("name") or "").strip() == _n or (f.get("remark") or "").strip() == _n or (f.get("alias") or "").strip() == _n:
                        f_wxid = f_w
                        break

            in_list = check_friend_in_list(name, f_wxid, friend_list, account_id=account_id)

            # 🌟 [本地磁盘兜底] 若 in_list=False，云端配置可能覆盖了本地最新保存的白名单
            # (account_settings_store owner_uid 不匹配时会直接 return 云端配置, 丢弃本地修改)
            # 此处直接读取磁盘 settings.json 做二次验证，确保刚保存的白名单立即生效
            if not in_list and friend_mode == "white" and account_id and account_id != "default":
                try:
                    from src.api.config_api.privacy_shield import _read_reply_from_disk_direct
                    disk_reply = _read_reply_from_disk_direct(account_id) or {}
                    disk_friends = disk_reply.get("auto_chat_friend_whitelist", []) or []
                    if disk_friends:
                        disk_list = [x for x in (str(x).strip() for x in disk_friends if x) if x and x != "uid_"]
                        in_list = check_friend_in_list(name, f_wxid, disk_list, account_id=account_id)
                        if in_list:
                            logger.info(f"[前置拦截·磁盘兜底] 好友 '{name}' 在本地磁盘白名单中命中，放行（云端配置可能未同步）")
                except Exception as _de:
                    logger.debug(f"[前置拦截·磁盘兜底] 读取磁盘白名单异常: {_de}")

                if not in_list:
                    from .whitelist_sync_helper import try_sync_friend_whitelist
                    in_list = await try_sync_friend_whitelist(engine, name, friend_list, account_id)

            if friend_mode == "white" and not in_list:
                logger.warning(f"[前置拦截·白名单诊断] 好友 '{name}' 未命中白名单! account_id={repr(account_id)}, f_wxid={repr(f_wxid)}, friend_list={repr(friend_list)}")
                await _skip_and_notify(engine, task_id, name, message, "好友不在白名单中，已跳过")
                return False, message
            elif friend_mode == "black" and in_list:
                logger.info(f"[前置拦截·二次·拦截] 好友 '{name}' 在黑名单中，已跳过")
                await _skip_and_notify(engine, task_id, name, message, "好友在黑名单中，已跳过")
                return False, message
    except Exception as cfg_ex:
        logger.warning(f"[前置拦截·二次] 读取隔离配置失败，跳过白黑名单二次校验: {cfg_ex}")

    # ── 第三道防线：管理员决策口令 + 决策网关回调处理 ──
    try:
        admin_wxid = reply_cfg.get("delegated_admin_wxid", "")
        is_admin_sender = bool(admin_wxid and (wxid == admin_wxid or name == admin_wxid))

        from .group_invite_handler import check_admin_delegated_command
        if await check_admin_delegated_command(engine, name, message, wxid, task_id, reply_cfg):
            return False, message

        if is_admin_sender and admin_wxid:
            from .decision_gateway import dispatch_admin_reply
            if await dispatch_admin_reply(engine, admin_wxid, message):
                await _skip_and_notify(engine, task_id, name, message, "决策网关：管理员指令已处理")
                return False, message
            logger.info(f"[前置拦截] 屏蔽对绑定管理员微信号 {admin_wxid} 的日常自动回复")
            await _skip_and_notify(engine, task_id, name, message, "管理员日常消息，已自动忽略回复")
            return False, message
    except Exception as e_admin:
        logger.warning(f"[前置拦截] 管理员指令与常规消息校验异常: {e_admin}")

    # ── 第四道防线：通用意图分类网关（顾客消息） ──
    try:
        from .reply_gateway_guard import run_gateway_guard
        gateway_admin = reply_cfg.get("delegated_admin_wxid", "")
        if await run_gateway_guard(engine, name, message, wxid, account_id,
                                   task_id, gateway_admin, is_group, _skip_and_notify):
            return False, message
    except Exception as e_gw:
        logger.warning(f"[前置拦截] 网关守卫调用异常（已降级跳过）: {e_gw}")

    return True, message

