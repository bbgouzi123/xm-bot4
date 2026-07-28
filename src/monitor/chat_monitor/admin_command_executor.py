import asyncio
import logging
import os
import time
from typing import Any
import uiautomation as auto

logger = logging.getLogger(__name__)

# 暂停状态控制集，生命周期在后端内存中
_PAUSED_BOTS = set()

def is_bot_paused(bot_wxid: str) -> bool:
    return bot_wxid in _PAUSED_BOTS

def set_bot_paused(bot_wxid: str, paused: bool):
    if paused:
        _PAUSED_BOTS.add(bot_wxid)
    else:
        _PAUSED_BOTS.discard(bot_wxid)


async def try_handle_group_invite(engine: Any, name: str, message: str, wxid: str = None) -> bool:
    """尝试自动点击并加入群邀请卡片"""
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

        logger.info(f"[自动加群] 检测到加群邀请消息. 会话: {name}, 消息内容: {message}")

        # 3. 在当前已打开的聊天窗口中查找群邀请 ListItem 卡片
        # 限制深度为 8，以防在消息过多的情况下发生 UIA 遍历超时
        from src.utils.safe_uia import safe_walk_control, safe_control_type, safe_get_name
        from src.uia.retry import smooth_click_at

        card_ctrl = None
        for ctrl, _ in safe_walk_control(engine.driver.root, max_depth=8):
            try:
                if safe_control_type(ctrl) == 'ListItemControl':
                    c_name = safe_get_name(ctrl)
                    if "邀请你加入群聊" in c_name:
                        card_ctrl = ctrl
                        break
            except Exception:
                continue

        if not card_ctrl:
            logger.warning("[自动加群] 聊天窗口中未找到包含 '邀请你加入群聊' 的卡片组件")
            return False

        logger.info(f"[自动加群] 成功定位卡片: {safe_get_name(card_ctrl)}")

        # 4. 双击/Invoke 打开加群网页窗口
        try:
            invoke_pattern = card_ctrl.GetInvokePattern()
            if invoke_pattern:
                invoke_pattern.Invoke()
                logger.info("[自动加群] 成功通过 InvokePattern 打开加群链接")
            else:
                legacy = card_ctrl.GetLegacyIAccessiblePattern()
                if legacy:
                    legacy.DoDefaultAction()
                    logger.info("[自动加群] 成功通过 LegacyIAccessible DoDefaultAction (双击) 打开加群卡片")
                else:
                    card_ctrl.DoubleClick()
                    logger.info("[自动加群] 成功通过 DoubleClick 打开加群链接")
        except Exception as e:
            logger.warning(f"[自动加群] Invoke/Legacy 尝试打开卡片失败，降级物理双击: {e}")
            try:
                card_ctrl.DoubleClick()
            except Exception as e2:
                logger.error(f"[自动加群] 物理双击卡片失败: {e2}")
                return False

        # 5. 等待并定位加群网页窗口 (类名为 Chrome_WidgetWin_0 且内部有 "加入群聊" 按钮)
        join_win = None
        join_btn = None

        await asyncio.sleep(1.5)  # 等待窗口渲染

        for attempt in range(4):
            for win in auto.GetRootControl().GetChildren():
                try:
                    if win.ControlTypeName == "WindowControl" and win.ClassName == "Chrome_WidgetWin_0":
                        btn = win.ButtonControl(Name="加入群聊")
                        if btn.Exists(0.2):
                            join_win = win
                            join_btn = btn
                            break
                except Exception:
                    continue
            if join_win:
                break
            await asyncio.sleep(1.0)

        if not join_win or not join_btn:
            logger.warning("[自动加群] 未找到弹出的加群网页窗口或未识别到其中的 '加入群聊' 按钮")
            return False

        logger.info(f"[自动加群] 定位加群窗口成功 (hwnd={join_win.NativeWindowHandle})")

        # 6. 点击 "加入群聊" 按钮
        try:
            invoke_btn = join_btn.GetInvokePattern()
            if invoke_btn:
                invoke_btn.Invoke()
                logger.info("[自动加群] 成功通过 InvokePattern 点击了 '加入群聊'")
            else:
                smooth_click_at(join_btn)
                logger.info("[自动加群] 成功通过 smooth_click_at 点击了 '加入群聊'")
        except Exception as e:
            logger.warning(f"[自动加群] 尝试点击 '加入群聊' 按钮失败，降级平滑物理点击: {e}")
            try:
                smooth_click_at(join_btn)
            except Exception as e2:
                logger.error(f"[自动加群] 平滑点击 '加入群聊' 失败: {e2}")
                return False

        await asyncio.sleep(2.5)  # 等待加入操作完成

        # 7. 关闭加群网页窗口
        close_btn = None
        try:
            close_btn = join_win.ButtonControl(Name="关闭")
        except Exception:
            pass

        if close_btn and close_btn.Exists(0.5):
            try:
                close_invoke = close_btn.GetInvokePattern()
                if close_invoke:
                    close_invoke.Invoke()
                    logger.info("[自动加群] 成功关闭加群窗口 (Invoke)")
                else:
                    smooth_click_at(close_btn)
                    logger.info("[自动加群] 成功关闭加群窗口 (Click)")
            except Exception as e:
                logger.warning(f"[自动加群] 点击关闭按钮失败，降级平滑点击: {e}")
                try:
                    smooth_click_at(close_btn)
                except Exception:
                    pass
        else:
            # 找不到关闭按钮，用 Alt+F4 强制关闭
            try:
                join_win.SendKeys("{Alt}{F4}")
                logger.info("[自动加群] 找不到关闭按钮，发送 Alt+F4 强行关闭窗口")
            except Exception as e:
                logger.error(f"[自动加群] 发送 Alt+F4 关闭窗口失败: {e}")

        logger.info("[自动加群] ✅ 自动加入群聊链路成功完成")
        return True
    except Exception as ex:
        logger.error(f"[自动加群] 运行异常: {ex}")
        return False


async def execute_screenshot_command(engine: Any, name: str, admin_wxid: str):
    """执行远程屏幕截图并私发给管理员"""
    try:
        from PIL import ImageGrab
        import tempfile
        
        img = ImageGrab.grab()
        temp_dir = tempfile.gettempdir()
        img_path = os.path.join(temp_dir, f"screen_{int(time.time())}.png")
        img.save(img_path)
        
        await engine.driver.SendMsg(admin_wxid, "正在截取云端屏幕，请稍候...")
        
        from src.uia.input_guard import uia_lock
        from .reply_workflow_helpers import get_originally_hidden_state, finalize_workflow_cleanup
        from src.utils.uia_task_runner import run_uia_with_timeout
        
        async with uia_lock.async_guard(f"正在向管理员发送屏幕截图...", hwnd=getattr(engine.driver, 'hwnd', None)):
            originally_hidden = get_originally_hidden_state(engine)
            chat_ok = await run_uia_with_timeout(
                engine.driver.ChatWith, 15.0, name, lock_input=True, foreground=True, wxid=admin_wxid
            )
            if chat_ok:
                await run_uia_with_timeout(engine.driver.SendFiles, 15.0, name, img_path, wxid=admin_wxid)
            await finalize_workflow_cleanup(engine, name, originally_hidden)
            
        try:
            os.remove(img_path)
        except Exception:
            pass
    except Exception as e_screen:
        await engine.driver.SendMsg(admin_wxid, f"❌ 屏幕截图失败，原因: {e_screen}")


async def execute_restart_command(engine: Any, admin_wxid: str):
    """执行远程系统重启，退出进程由守护脚本拉起"""
    await engine.driver.SendMsg(admin_wxid, "🔄 正在重启挂机后台，预计 5-10 秒内由守护程序自动拉起并恢复运行...")
    logger.warning("[管理员指令] 收到管理员重启指令，准备安全退出由守护脚本拉起")
    await asyncio.sleep(1.0)
    os._exit(0)


async def execute_send_message_command(engine: Any, name: str, admin_wxid: str, target_name: str, text_to_send: str):
    """执行远程主动发送消息指令"""
    await engine.driver.SendMsg(admin_wxid, f"正在向 “{target_name}” 转发消息: {text_to_send} ...")
    
    from src.uia.input_guard import uia_lock
    from .reply_workflow_helpers import get_originally_hidden_state, finalize_workflow_cleanup
    from src.utils.uia_task_runner import run_uia_with_timeout
    
    async with uia_lock.async_guard(f"正在向 {target_name} 远程发送消息...", hwnd=getattr(engine.driver, 'hwnd', None)):
        originally_hidden = get_originally_hidden_state(engine)
        chat_ok = await run_uia_with_timeout(
            engine.driver.ChatWith, 15.0, target_name, lock_input=True, foreground=True
        )
        if chat_ok:
            await run_uia_with_timeout(engine.driver.send_message, 15.0, target_name, text_to_send)
            await engine.driver.SendMsg(admin_wxid, f"✅ 消息已成功送达 “{target_name}”！")
        else:
            await engine.driver.SendMsg(admin_wxid, f"❌ 发送失败：微信中无法定位该会话 “{target_name}”")
        await finalize_workflow_cleanup(engine, name, originally_hidden)


async def execute_analyze_profile_command(engine: Any, admin_wxid: str, target_name: str):
    """执行远程索要客户画像与聊天总结指令"""
    try:
        account_id = getattr(engine.driver, 'bot_wxid', None) or getattr(engine.driver, '_wxid', None) or 'default'
        
        # 1. 查找好友 wxid
        from src.utils.contacts_cache import contacts_cache
        all_friends = contacts_cache.get_friends(account_id)
        
        f_wxid = ""
        f_nickname = ""
        f_remark = ""
        
        for f in all_friends:
            f_w = (f.get("wxid") or "").strip()
            f_n = (f.get("name") or "").strip()
            f_r = (f.get("remark") or "").strip()
            f_a = (f.get("alias") or "").strip()
            if f_w == target_name.strip():
                f_wxid, f_nickname, f_remark = f_w, f_n, f_r
                break
            if f_n == target_name.strip() or f_r == target_name.strip() or f_a == target_name.strip():
                f_wxid, f_nickname, f_remark = f_w, f_n, f_r
                break
                
        if not f_wxid:
            await engine.driver.SendMsg(admin_wxid, f"⚠️ 未在通讯录中找到好友 “{target_name}”。\n请确认名字（备注/昵称/微信号）输入正确。")
            return
            
        # 2. 获取画像资料
        from src.crm.profile_manager import ProfileManager
        pm = ProfileManager(account_id=account_id)
        profile = pm.get_profile(f_wxid, nickname=f_nickname or target_name)
        
        # 3. 组装简报
        nickname_display = profile.nickname or f_nickname or "未知"
        remark_display = profile.remark or f_remark or "无"
        region_display = profile.region or "未知"
        chat_count_display = profile.chat_count
        
        # 获取意向度标签
        intent_tag = profile.get_tag("intent")
        intent_level = intent_tag.value if intent_tag else "未评估"
        
        # 标签列表展示
        tags_str = " | ".join(f"{t.subcategory}:{t.value}" for t in profile.tags) if profile.tags else "暂无标签"
        
        # 笔记展示
        notes_str = "\n".join(f"- {note}" for note in profile.notes) if profile.notes else "暂无随手笔记"
        
        # 总结展示
        summary_display = profile.conversation_summary.strip() if profile.conversation_summary else "暂无 AI 聊天总结（可以多让机器人与他聊几句哦）"
        
        report_msg = (
            f"👤 【客户 AI 画像总结报告】\n"
            f"• 昵称: {nickname_display}\n"
            f"• 备注: {remark_display}\n"
            f"• 地区: {region_display}\n"
            f"• 互动轮数: {chat_count_display} 次\n"
            f"• 意向评估: {intent_level}\n"
            f"• 客户标签: {tags_str}\n"
            f"-----------------------\n"
            f"📝 AI 聊天总结:\n"
            f"{summary_display}\n"
            f"-----------------------\n"
            f"📌 随手笔记:\n"
            f"{notes_str}"
        )
        
        await engine.driver.SendMsg(admin_wxid, report_msg)
    except Exception as e:
        logger.error(f"[管理员指令] 生成画像总结报告异常: {e}")
        await engine.driver.SendMsg(admin_wxid, f"❌ 获取画像分析失败，原因: {e}")


async def execute_help_command(engine: Any, admin_wxid: str):
    """向管理员私发支持的控制指令列表说明书"""
    help_msg = (
        "🎛️ 【星码行空挂机助手远程指令指南】\n"
        "您已绑定为该实例的委派管理员，可发送以下指令控制值守：\n\n"
        "1️⃣ 【数据简报】\n"
        "• 发送: 数据 / 状态 / 查询 / status\n\n"
        "2️⃣ 【决策审批】\n"
        "• 发送: 同意 / 同意加群 (当收到入群卡片时)\n\n"
        "3️⃣ 【画像总结】\n"
        "• 发送: 分析 @客户 / 总结 @客户 / 画像 @客户\n\n"
        "4️⃣ 【远程发送】\n"
        "• 发送: 发消息 @接收人 消息内容\n\n"
        "5️⃣ 【值守控制】\n"
        "• 发送: 暂停回复 / 恢复回复\n"
        "• 发送: 开启白名单 / 关闭白名单\n\n"
        "6️⃣ 【系统运维】\n"
        "• 发送: 截图 / 查看截图\n"
        "• 发送: 重启 / 重启系统\n"
        "-----------------------\n"
        "*注：上述指令仅限管理员发送有效，普通好友发送仍走 AI 回复流程。"
    )
    await engine.driver.SendMsg(admin_wxid, help_msg)


