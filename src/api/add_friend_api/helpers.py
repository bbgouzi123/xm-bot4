import random
import logging
import asyncio
from src.friend import friend_queue

logger = logging.getLogger(__name__)

def _get_wait_time(interval_cfg):
    if isinstance(interval_cfg, list) and len(interval_cfg) == 2:
        return random.uniform(interval_cfg[0], interval_cfg[1]) * 60
    return float(interval_cfg) * 60 * random.uniform(0.7, 1.3)

def _generate_remark(item: dict, config: dict = None) -> str:
    if not config:
        config = {}
    remark_mode = config.get("remark_mode", "default")
    if remark_mode != "custom":
        company = item.get("company_name", "")[:8]
        legal = item.get("legal_person", "")
        if legal and company: return f"{legal[0]}总-{company}"
        elif company: return company
        elif legal: return f"{legal[0]}总"
        return ""

    template = config.get("remark_template", "")
    if not template:
        return ""

    import datetime
    created_at = item.get("created_at") or ""
    date_str = created_at[:10] if len(created_at) >= 10 else datetime.date.today().strftime("%Y-%m-%d")

    replacements = {
        "{姓名}": item.get("legal_person") or item.get("nickname") or "",
        "{公司}": item.get("company_name") or "",
        "{行业}": item.get("industry_profile_name") or "",
        "{电话}": item.get("phone") or item.get("wechat_id") or "",
        "{日期}": date_str,
        "{编号}": str(item.get("row_index") or item.get("id") or ""),
    }

    extra = item.get("extra_fields") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            replacements[f"{{{k}}}"] = str(v)
            replacements[f"{{extra_fields.{k}}}"] = str(v)

    remark = template
    for key, val in replacements.items():
        remark = remark.replace(key, val)

    return remark[:16].strip()

def _merge_tags(item: dict, config: dict) -> str:
    all_tags = []
    task_tags = config.get("tags", "")
    if task_tags:
        for t in task_tags.replace("，", ",").split(","):
            if t.strip() and t.strip() not in all_tags: all_tags.append(t.strip())
    industry = item.get("industry_profile_name", "")
    if industry and industry not in all_tags: all_tags.append(industry)
    return ",".join(all_tags)

def _generate_verify_message(item: dict, config: dict) -> str:
    mode = config.get("verify_mode", "ai")
    if mode == "fixed": return config.get("verify_message", "")[:25]
    
    company = item.get("company_name", "")[:8]
    legal = item.get("legal_person", "")
    surname = legal[0] if legal else ""
    title = f"{surname}总" if surname else ""
    
    templates = []
    if title and company:
        templates.extend([f"{title}您好，我们做AI获客系统的，看到贵司想交流", f"{title}好，微信全自动拓客想和{company}对接下"])
    elif title:
        templates.extend([f"{title}您好，我们是做AI获客系统的，想交流合作", f"{title}好，AI全自动加同行/拓客方面想向您请教"])
    elif company:
        templates.extend([f"您好，看到{company}，我们做AI获客，想交流", "您好，我们是做微信全自动获客的，想和贵司合作"])
    else:
        templates.extend(["您好，我们研发了AI加好友获客软件，想交流合作", "您好，做微信自动营销拓客的，想认识一下"])
    return random.choice(templates)[:25]

def _sync_to_crm(item: dict, result: dict):
    wxid = result.get("wxid") or item.get("wechat_id")
    nickname = result.get("nickname") or item.get("nickname") or item.get("legal_person")
    if not wxid: return
    try:
        from src.crm.profile_manager import ProfileManager
        from src.crm.tag_manager import TagEntry
        pm = ProfileManager()
        tags = []
        if item.get("company_name"):
            tags.append(TagEntry("career", "company_name", item["company_name"], source="friend_import"))
        extra = item.get("extra_fields", {})
        for k, v in extra.items():
            if v and str(v).strip():
                tags.append(TagEntry("import_data", k, str(v).strip()[:200], source="friend_import"))
        if tags: pm.update_tags(wxid, tags, source="friend_import", nickname=nickname)
    except Exception: pass

def _sync_cloud():
    try:
        import threading
        from src.utils.cloud_sync import get_cloud_client
        threading.Thread(target=get_cloud_client().sync_friend_queue, daemon=True).start()
    except Exception: pass

async def _wait_for_pending_replies_if_any(driver, task_state):
    """如果检测到有待回复的消息，暂停加好友，礼让回复任务"""
    import asyncio
    from app.state import account_manager
    if not driver:
        return
    
    # 寻找当前 driver 对应的 monitor 实例
    inst = None
    if getattr(driver, "_wxid", None):
        inst = account_manager.get_instance_by_wxid(driver._wxid)
    if not inst:
        for active_inst in account_manager._instances.values():
            if active_inst.driver == driver:
                inst = active_inst
                break
    
    if not inst or not inst.monitor:
        return

    # 检查是否有需要优先回复的消息
    has_replies = bool(inst.monitor._message_buffer) or bool(inst.monitor._processing)
    if not has_replies:
        return

    logger.info("[加好友避让] ⚠️ 检测到有未读/待回复的消息流入，优先礼让回复，暂停加好友任务...")
    task_state["paused_by_reply"] = True

    # 释放微信界面：如果有“添加朋友”或“申请添加朋友”弹窗，必须关掉它
    from src.uia.add_friend import AddFriendEngine
    engine = AddFriendEngine(driver)
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: engine._close_add_friend_dialogs(None, None)
        )
    except Exception as e:
        logger.error(f"[加好友避让] 关闭添加朋友弹窗异常: {e}")

    # 循环等待直到回复全部完成
    while task_state["running"] and (bool(inst.monitor._message_buffer) or bool(inst.monitor._processing)):
        await asyncio.sleep(2)

    # 回复完成后，额外冷静 5 秒
    if task_state["running"]:
        logger.info("[加好友避让] ✅ 待回复消息已处理完毕，冷静 5 秒后恢复加好友任务...")
        await asyncio.sleep(5)

    task_state["paused_by_reply"] = False


async def _validate_mobile_if_enabled(item: dict, config: dict, queue_id: int, search_id: str, task_state: dict) -> bool:
    """如果开启了手机号验证，执行前置过滤。如果被拦截返回 False，通过返回 True。"""
    if config.get("validate_mobile", False) and item.get("phone"):
        try:
            logger.info(f"[一键加人] 执行前置号码验证: {item['phone']}")
            from src.api.config_api.base_config import _load_configs
            from src.ai.validate_mobile import ValidateMobileService
            vm_settings = _load_configs().get("validate_mobile_settings", {})
            if vm_settings.get("enabled", False):
                vm_service = ValidateMobileService(vm_settings)
                check_res = await vm_service.validate_and_check(item["phone"])
                if not check_res.get("has_wechat", True):
                    reason = check_res.get("error", "未开通微信")
                    logger.info(f"[一键加人] 过滤拦截：{item['phone']} 未注册微信 ({reason})")
                    from src.friend import friend_queue
                    friend_queue.update_status(queue_id, "failed", error_msg=reason)
                    friend_queue.add_log(queue_id, search_id, item.get("company_name", ""), "add_friend", "failed", f"[前置过滤拦截] 未注册微信: {reason}")
                    task_state["progress"]["processed"] += 1
                    task_state["progress"]["failed"] += 1
                    return False
                else:
                    logger.info(f"[一键加人] 前置验证通过，号码已注册微信: {item['phone']}")
        except Exception as ex:
            logger.warning(f"[前置过滤] 执行检验异常: {ex}")
    return True


def _trigger_risk_alert_if_frequent(reason: str, drv, task_state: dict):
    """如果检测到频繁/风控关键词，触发告警并暂停任务"""
    risk_keywords = ["操作频繁", "过于频繁", "被限制", "频繁", "风控", "限制", "安全限制", "操作过于频繁"]
    if any(kw in reason for kw in risk_keywords):
        logger.warning(f"[防封风控保护] 微信加粉提示风控/频繁: {reason}. 自动暂停当前任务流水线并触发全渠道报警！")
        task_state["paused"] = True
        from src.utils.alert_notifier import alert_notifier
        import platform
        asyncio.create_task(alert_notifier.trigger_risk_alert(
            machine_code=platform.node(),
            account_id=getattr(drv, "_wxid", None) or getattr(drv, "nickname", None) or "未知微信",
            reason=f"微信加粉过程中触发风控限制: {reason}",
            is_fatal=False,
            hwnd=drv.hwnd if drv else 0
        ))
        asyncio.create_task(alert_notifier.send_user_notification(
            title="🛡️ 安全风控与防封挂起",
            body="触发高频防封阈值，任务已自动挂起安全时间",
            category="alert"
        ))


def _trigger_exception_alert(e: Exception, drv, task_state: dict):
    """加粉任务崩溃时触发告警"""
    try:
        from src.utils.alert_notifier import alert_notifier
        import platform
        task_state["paused"] = True
        asyncio.create_task(alert_notifier.trigger_risk_alert(
            machine_code=platform.node(),
            account_id=getattr(drv, "_wxid", None) or getattr(drv, "nickname", None) or "未知微信",
            reason=f"加粉任务遇到未捕获异常崩溃: {str(e)}",
            is_fatal=False,
            hwnd=drv.hwnd if drv else 0
        ))
    except Exception as ae:
        logger.error(f"发送异常告警失败: {ae}")

