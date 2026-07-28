import asyncio
import hashlib
import logging
import time
from typing import Any
import uiautomation as auto

logger = logging.getLogger(__name__)

# 内存级待审批队列，生命期 30 分钟。格式: { admin_wxid: [ { friend_wxid, friend_name, message, timestamp, task_id } ] }
_PENDING_INVITES = {}

# 从独立执行器导入核心处理及控制逻辑
from .admin_command_executor import is_bot_paused, set_bot_paused, try_handle_group_invite



async def check_and_execute_group_invite(engine: Any, name: str, message: str, wxid: str, task_id: str) -> bool:
    """自动加入群聊检测包，供自动回复工作流单行调用以实现 300 行限额解耦"""
    try:
        from src.api.config_api.privacy_shield import _get_reply_config_isolated
        account_id = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
        reply_cfg = _get_reply_config_isolated(account_id)
        
        # 1. 校验自动入群开关，默认关闭
        if not reply_cfg.get("auto_accept_group_enabled", False):
            return False

        # 2. 检查消息文本是否为群邀请卡片
        if "邀请你加入群聊" not in message:
            return False

        # 3. 校验是否绑定了委托管理员微信
        admin_wxid = reply_cfg.get("delegated_admin_wxid", "")
        if not admin_wxid:
            # 未绑定管理员 -> 降级为直接自动入群
            if await try_handle_group_invite(engine, name, message, wxid=wxid):
                from src.utils.websocket_manager import ws_manager
                from .reply_workflow_helpers import get_originally_hidden_state, finalize_workflow_cleanup
                
                await ws_manager.broadcast_task_update(
                    task_id=task_id, task_type="自动回复", status="completed",
                    progress=100, total=100,
                    message="检测到入群邀请，已自动加入群聊并关闭窗口",
                    friend_name=name, friend_wxid=wxid, incoming_msg=message
                )
                try:
                    keys_to_set = [name]
                    if wxid and wxid != name:
                        keys_to_set.append(wxid)
                    for k in keys_to_set:
                        engine._fingerprints.setdefault(k, set()).add(hashlib.md5(f"{k}:{message}".encode()).hexdigest())
                except Exception:
                    pass
                
                originally_hidden = get_originally_hidden_state(engine)
                await finalize_workflow_cleanup(engine, name, originally_hidden)
                return True
            return False

        # 4. 绑定了管理员 -> 执行挂起委托
        logger.info(f"[委托加群] 检测到加群邀请，已挂起并向管理员 {admin_wxid} 发送决策请示")
        
        # 清理过期请示并存入队列
        now = time.time()
        for k in list(_PENDING_INVITES.keys()):
            _PENDING_INVITES[k] = [x for x in _PENDING_INVITES[k] if now - x.get("timestamp", 0) < 1800.0]
            if not _PENDING_INVITES[k]:
                _PENDING_INVITES.pop(k, None)

        _PENDING_INVITES.setdefault(admin_wxid, []).append({
            "friend_wxid": wxid or name,
            "friend_name": name,
            "message": message,
            "timestamp": now,
            "task_id": task_id
        })
        
        # 给管理员发送决策请示卡片
        request_msg = (
            f"🔔 【自动回复系统决策请示】\n"
            f"收到好友 “{name}” 发来的群聊邀请。\n"
            f"群聊卡片: {message}\n"
            f"-----------------------\n"
            f"回复【同意加群】授权机器人自动点击加入该群。"
        )

        from src.utils.websocket_manager import ws_manager
        from .reply_workflow_helpers import get_originally_hidden_state, finalize_workflow_cleanup

        await ws_manager.broadcast_task_update(
            task_id=task_id, task_type="自动回复", status="completed",
            progress=100, total=100,
            message="检测到入群邀请，已向管理员发送委托审批，当前挂起中...",
            friend_name=name, friend_wxid=wxid, incoming_msg=message
        )
        
        # 切窗口给管理员发消息
        await engine.driver.SendMsg(admin_wxid, request_msg)
        
        # 记录消息指纹防止机器人重复扫描触发请示
        try:
            keys_to_set = [name]
            if wxid and wxid != name:
                keys_to_set.append(wxid)
            for k in keys_to_set:
                engine._fingerprints.setdefault(k, set()).add(hashlib.md5(f"{k}:{message}".encode()).hexdigest())
        except Exception:
            pass

        originally_hidden = get_originally_hidden_state(engine)
        await finalize_workflow_cleanup(engine, name, originally_hidden)
        return True
    except Exception as e_invite:
        logger.error(f"[工作流] 自动加群拦截执行失败: {e_invite}")
    return False


async def execute_pending_join(engine: Any, admin_wxid: str) -> bool:
    """由管理员触发同意，真正执行挂起的入群决策"""
    invites = _PENDING_INVITES.get(admin_wxid, [])
    now = time.time()
    
    # 过滤掉超过 30 分钟的请求
    active_invites = [x for x in invites if now - x.get("timestamp", 0) < 1800.0]
    if not active_invites:
        _PENDING_INVITES.pop(admin_wxid, None)
        await engine.driver.SendMsg(admin_wxid, "⚠️ 当前没有待审批的入群请示，或者请求已超时失效（有效期30分钟）。")
        return False

    # 取出最近的一条
    inv = active_invites.pop()
    _PENDING_INVITES[admin_wxid] = active_invites
    if not active_invites:
        _PENDING_INVITES.pop(admin_wxid, None)

    friend_wxid = inv["friend_wxid"]
    friend_name = inv["friend_name"]
    message = inv["message"]
    task_id = inv["task_id"]

    logger.info(f"[委托加群] 管理员 {admin_wxid} 授权同意，开始为好友 {friend_name}({friend_wxid}) 执行自动入群")
    await engine.driver.SendMsg(admin_wxid, f"正在为 “{friend_name}” 执行自动入群操作，请稍候...")

    from src.uia.input_guard import uia_lock
    from .reply_workflow_helpers import get_originally_hidden_state, finalize_workflow_cleanup
    from src.utils.uia_task_runner import run_uia_with_timeout

    async with uia_lock.async_guard(f"正在为 {friend_name} 执行委派加群动作...", hwnd=getattr(engine.driver, 'hwnd', None)):
        chat_ok = await run_uia_with_timeout(
            engine.driver.ChatWith, 15.0, friend_name, lock_input=True, foreground=True, msg_hint=message, wxid=friend_wxid
        )
        if not chat_ok:
            logger.warning(f"[委托加群] 会话切换失败: {friend_name}")
            await engine.driver.SendMsg(admin_wxid, f"❌ 自动入群失败：无法切换至 “{friend_name}” 的聊天窗口。")
            return False

        originally_hidden = get_originally_hidden_state(engine)
        # 执行物理加群点击
        success = await try_handle_group_invite(engine, friend_name, message, wxid=friend_wxid)
        await finalize_workflow_cleanup(engine, friend_name, originally_hidden)

        if success:
            from .base import _chat_daily_counter
            account_id = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
            _chat_daily_counter.increment("group_invite_approval", account_id)
            
            await engine.driver.SendMsg(admin_wxid, f"✅ 已成功加入 “{friend_name}” 邀请的群聊！")
            return True
        else:
            await engine.driver.SendMsg(admin_wxid, f"❌ 自动点击卡片入群失败，请检查微信窗口是否被遮挡。")
            return False


async def check_admin_delegated_command(engine: Any, name: str, message: str, wxid: str, task_id: str, reply_cfg: dict) -> bool:
    """如果当前消息是管理员发的控制指令，则路由到执行器并返回 True 进行前置拦截"""
    try:
        admin_wxid = reply_cfg.get("delegated_admin_wxid", "")
        if admin_wxid and (wxid == admin_wxid or name == admin_wxid):
            clean_msg = message.strip()
            account_id = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
            from .reply_preconditions import _skip_and_notify
            
            # 1. 审批口令
            if clean_msg in ("同意加群", "同意"):
                await execute_pending_join(engine, admin_wxid)
                await _skip_and_notify(engine, task_id, name, message, "收到管理员授权同意加群口令，已执行")
                return True
                
            # 2. 控制台状态与数据查询
            elif clean_msg in ("数据", "状态", "查询", "status"):
                nickname = getattr(engine.driver, '_nickname', "") or name
                from .base import _chat_daily_counter
                auto_reply_count = _chat_daily_counter.get_count("auto_reply", account_id)
                group_approval_count = _chat_daily_counter.get_count("group_invite_approval", account_id)
                pending_count = len(_PENDING_INVITES.get(admin_wxid, []))
                
                is_connected = True
                if hasattr(engine.driver, 'is_connected'):
                    try:
                        is_connected = engine.driver.is_connected()
                    except Exception:
                        pass
                status_str = "🟢 正常在线值守中" if is_connected else "🔴 物理连接异常"
                if is_bot_paused(account_id):
                    status_str += " (⏸️ 已暂停值守)"
                
                from datetime import datetime
                report_msg = (
                    f"📊 【挂机实例今日运行简报】\n"
                    f"• 值守微信: {nickname}\n"
                    f"• 运行状态: {status_str}\n"
                    f"• 今日自动回复: {auto_reply_count} 次\n"
                    f"• 今日入群审批: {group_approval_count} 次\n"
                    f"• 当前待审批: {pending_count} 个\n"
                    f"-----------------------\n"
                    f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )
                await engine.driver.SendMsg(admin_wxid, report_msg)
                await _skip_and_notify(engine, task_id, name, message, "管理员查询简报，已自动回复")
                return True
                
            # 3. 远程屏幕截图
            elif clean_msg in ("查看截图", "截图", "screen", "screenshot"):
                from .admin_command_executor import execute_screenshot_command
                await execute_screenshot_command(engine, name, admin_wxid)
                await _skip_and_notify(engine, task_id, name, message, "管理员查看截图口令，已处理")
                return True
                
            # 4. 远程系统重启
            elif clean_msg in ("重启系统", "重启", "restart"):
                from .admin_command_executor import execute_restart_command
                await execute_restart_command(engine, admin_wxid)
                return True
                
            # 5. 暂停 / 恢复回复值守
            elif clean_msg in ("暂停回复", "暂停值守", "停止挂机"):
                set_bot_paused(account_id, True)
                await engine.driver.SendMsg(admin_wxid, "⏸️ 挂机自动回复已暂停值守。非管理员的消息将不会进行自动回复。")
                await _skip_and_notify(engine, task_id, name, message, "管理员暂停回复")
                return True
                
            elif clean_msg in ("恢复回复", "恢复值守", "开启挂机"):
                set_bot_paused(account_id, False)
                await engine.driver.SendMsg(admin_wxid, "▶️ 挂机自动回复已重新开启值守，正在守护您的好友。")
                await _skip_and_notify(engine, task_id, name, message, "管理员恢复回复")
                return True
                
            # 6. 好友白名单开关控制
            elif clean_msg in ("开启好友白名单", "开启白名单"):
                from src.crm.account_data import get_account_settings, save_account_settings
                acc_settings = get_account_settings(account_id, force_reload=True)
                reply = acc_settings.get("reply", {})
                reply["auto_chat_friend_mode"] = "white"
                acc_settings["reply"] = reply
                save_account_settings(acc_settings, account_id)
                
                await engine.driver.SendMsg(admin_wxid, "✅ 已成功开启好友白名单模式（机器人仅自动回复白名单中的好友）。")
                await _skip_and_notify(engine, task_id, name, message, "管理员开启白名单模式")
                return True
                
            elif clean_msg in ("关闭好友白名单", "关闭白名单", "开启好友黑名单", "开启黑名单"):
                from src.crm.account_data import get_account_settings, save_account_settings
                acc_settings = get_account_settings(account_id, force_reload=True)
                reply = acc_settings.get("reply", {})
                reply["auto_chat_friend_mode"] = "black"
                acc_settings["reply"] = reply
                save_account_settings(acc_settings, account_id)
                
                await engine.driver.SendMsg(admin_wxid, "✅ 已成功切换为黑名单过滤模式（除了黑名单里的好友，其余好友默认全部回复）。")
                await _skip_and_notify(engine, task_id, name, message, "管理员关闭白名单模式")
                return True
                
            # 7. 总结/分析/画像 客户
            elif clean_msg.startswith(("总结", "分析", "画像")):
                idx_at = clean_msg.find("@")
                if idx_at != -1:
                    target_name = clean_msg[idx_at + 1:].strip()
                    if target_name:
                        from .admin_command_executor import execute_analyze_profile_command
                        await execute_analyze_profile_command(engine, admin_wxid, target_name)
                        await _skip_and_notify(engine, task_id, name, message, f"管理员索要 {target_name} 画像分析")
                        return True
                await engine.driver.SendMsg(admin_wxid, "⚠️ 分析指令格式不正确。正确格式示例：\n分析 @张三")
                await _skip_and_notify(engine, task_id, name, message, "管理员分析指令格式不正确")
                return True
            # 8. 帮助/口令指南
            elif clean_msg in ("帮助", "菜单", "指令", "口令", "help"):
                from .admin_command_executor import execute_help_command
                await execute_help_command(engine, admin_wxid)
                await _skip_and_notify(engine, task_id, name, message, "管理员索要帮助指南口令，已处理")
                return True

            # 9. 远程主动发送消息
            elif clean_msg.startswith("发消息"):
                idx_at = clean_msg.find("@")
                if idx_at != -1:
                    remain = clean_msg[idx_at + 1:].strip()
                    parts = remain.split(None, 1)
                    if len(parts) == 2:
                        target_name, text_to_send = parts[0].strip(), parts[1].strip()
                        if target_name and text_to_send:
                            from .admin_command_executor import execute_send_message_command
                            await execute_send_message_command(engine, name, admin_wxid, target_name, text_to_send)
                            await _skip_and_notify(engine, task_id, name, message, f"管理员远程发送消息至 {target_name}")
                            return True
                await engine.driver.SendMsg(admin_wxid, "⚠️ 发消息指令格式不正确。正确格式示例：\n发消息 @张三 中午好")
                await _skip_and_notify(engine, task_id, name, message, "管理员发送消息指令格式不正确")
                return True
    except Exception as e:
        logger.warning(f"[委托加群] 拦截管理员指令异常: {e}")
    return False
