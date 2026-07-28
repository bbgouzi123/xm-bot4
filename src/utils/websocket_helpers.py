import sys
import logging
import re

logger = logging.getLogger(__name__)

def resolve_bot_wxid(kwargs: dict, task_id: str, task_cache: dict) -> str:
    """自动解析或遗传任务对应的机器人微信 ID"""
    bot_wxid = kwargs.get("bot_wxid")
    
    # 1. 任务归属遗传：若此前已有此任务的历史缓存，优先继承原有的机器人微信归属
    if not bot_wxid or bot_wxid == "default":
        if task_cache and task_id in task_cache:
            cached_bot = task_cache[task_id].get("data", {}).get("bot_wxid")
            if cached_bot and cached_bot != "default":
                bot_wxid = cached_bot

    # 2. 从调用栈帧提取
    if not bot_wxid:
        try:
            frame = sys._getframe()
            while frame:
                locs = frame.f_locals
                if 'account_id' in locs and isinstance(locs['account_id'], str) and locs['account_id']:
                    bot_wxid = locs['account_id']
                    break
                if 'bot_wxid' in locs and isinstance(locs['bot_wxid'], str) and locs['bot_wxid']:
                    bot_wxid = locs['bot_wxid']
                    break
                if 'engine' in locs:
                    engine = locs['engine']
                    drv = getattr(engine, 'driver', None)
                    if drv:
                        bot_wxid = getattr(drv, 'bot_wxid', None) or getattr(drv, '_wxid', None)
                        if bot_wxid: break
                if 'driver' in locs:
                    drv = locs['driver']
                    bot_wxid = getattr(drv, 'bot_wxid', None) or getattr(drv, '_wxid', None)
                    if bot_wxid: break
                if 'self' in locs:
                    slf = locs['self']
                    drv = getattr(slf, 'driver', None)
                    if drv:
                        bot_wxid = getattr(drv, 'bot_wxid', None) or getattr(drv, '_wxid', None)
                        if bot_wxid: break
                frame = frame.f_back
        except Exception:
            pass

    # 3. 如果还是没有，从全局当前活跃账户获取
    if not bot_wxid or bot_wxid == "default":
        try:
            from src.crm.account_data import get_active_account
            bot_wxid = get_active_account()
        except Exception:
            pass

    # 4. 冷启动/免登兜底
    if not bot_wxid or bot_wxid == "default":
        try:
            from app.state import account_manager as am
            if am:
                active_insts = [inst for inst in am._instances.values() if inst.driver.is_connected()]
                if len(active_insts) == 1 and active_insts[0].wxid:
                    bot_wxid = active_insts[0].wxid
                elif am.primary_instance and am.primary_instance.wxid:
                    bot_wxid = am.primary_instance.wxid
        except Exception:
            pass

    return bot_wxid or "default"

def fill_whitelist_status(task_type: str, bot_wxid: str, kwargs: dict):
    """自动补全加白和黑名单（免打扰）前缀与精确匹配状态"""
    # ⚠️ [修复] bot_wxid 为 default/空时，配置加载必然为空，强制跳过，
    # 避免写入 is_whitelisted=False 导致前端显示「未设置」/「加白」按钮激活
    if not bot_wxid or bot_wxid == "default":
        return
    if task_type == "自动回复" and bot_wxid:
        try:
            from src.crm.account_settings_store import get_account_settings
            from src.utils.contacts_cache import contacts_cache
            
            friend_name = kwargs.get("friend_name")
            friend_wxid = kwargs.get("friend_wxid")
            is_group_task = kwargs.get("is_group", False)
            
            # 若无明确 is_group 字段，尝试根据名字/类型推断
            if not is_group_task:
                if friend_wxid and friend_wxid.endswith("@chatroom"):
                    is_group_task = True
                elif friend_name:
                    is_group_task = "、" in friend_name or bool(re.search(r'[\(（]\d+[\)）]$', friend_name))
            
            if "is_group" not in kwargs:
                kwargs["is_group"] = is_group_task
            
            # 尝试根据姓名反查 wxid，以匹配白名单中 wxid:xxx 的写法
            if not friend_wxid and friend_name:
                all_friends = contacts_cache.get_friends(bot_wxid) or []
                all_groups = contacts_cache.get_groups(bot_wxid) or []
                if is_group_task:
                    clean_name = re.sub(r'[\(（]\d+[\)）]$', '', friend_name).strip()
                    for g in all_groups:
                        if g.get("name") == friend_name or g.get("name") == clean_name or g.get("wxid") == friend_name:
                            friend_wxid = g.get("wxid")
                            break
                else:
                    for f in all_friends:
                        if f.get("name") == friend_name or f.get("remark") == friend_name or f.get("nickname") == friend_name or f.get("wxid") == friend_name:
                            friend_wxid = f.get("wxid")
                            break

            # 获取配置
            settings = get_account_settings(bot_wxid)
            reply = settings.get("reply", {})
            
            whitelist_key = "auto_chat_group_whitelist" if is_group_task else "auto_chat_friend_whitelist"
            excludes_key = "auto_chat_group_excludes" if is_group_task else "auto_chat_friend_excludes"
            
            lst_white = reply.get(whitelist_key, [])
            lst_black = reply.get(excludes_key, [])
            
            def check_match_detail(target_name, target_wxid, list_to_check):
                if not list_to_check:
                    return False, False, ""
                possible_names = {target_name} if target_name else set()
                clean = re.sub(r'[\(（]\d+[\)）]$', '', target_name).strip() if target_name else ""
                if clean:
                    possible_names.add(clean)
                if target_wxid:
                    possible_names.add(target_wxid)
                
                for x in list_to_check:
                    if not x:
                        continue
                    x_clean = str(x).strip()
                    prefix_val = ""
                    if x_clean.startswith("prefix:"):
                        prefix_val = x_clean[7:].strip()
                    elif x_clean.endswith("*") and len(x_clean) > 1:
                        prefix_val = x_clean[:-1].strip()
                    
                    if prefix_val:
                        if any(p.startswith(prefix_val) for p in possible_names if p):
                            return True, True, x_clean
                        continue
                    
                    if x_clean in possible_names:
                        return True, False, x_clean
                    
                    stripped = ""
                    if x_clean.startswith("wxid:"):
                        stripped = x_clean[5:].strip()
                    elif x_clean.startswith("uid_"):
                        stripped = x_clean[4:].strip()
                    elif x_clean.startswith("namecat:"):
                        stripped = x_clean[8:].split("::")[0].strip()
                    else:
                        stripped = x_clean
                    
                    if stripped in possible_names:
                        return True, False, x_clean
                return False, False, ""

            is_whitelisted_flag, is_white_prefix, white_rule = check_match_detail(friend_name, friend_wxid, lst_white)
            is_blacklisted_flag, is_black_prefix, black_rule = check_match_detail(friend_name, friend_wxid, lst_black)
            
            if "is_whitelisted" not in kwargs:
                kwargs["is_whitelisted"] = is_whitelisted_flag
            if "is_blacklisted" not in kwargs:
                kwargs["is_blacklisted"] = is_blacklisted_flag
            if "is_white_prefix" not in kwargs:
                kwargs["is_white_prefix"] = is_white_prefix
            if "white_rule" not in kwargs:
                kwargs["white_rule"] = white_rule
            if "is_black_prefix" not in kwargs:
                kwargs["is_black_prefix"] = is_black_prefix
            if "black_rule" not in kwargs:
                kwargs["black_rule"] = black_rule
        except Exception as e_complete:
            logger.debug(f"[WS] 自动填充加白列表属性发生异常: {e_complete}")

def update_status_overlay(status: str, message: str, task_type: str, progress: int, total: int, kwargs: dict):
    """同步任务更新到右上角悬浮看板"""
    try:
        from src.utils.status_overlay import status_overlay
        if status_overlay.hwnd:
            friend_name = kwargs.get("friend_name") or kwargs.get("name") or "-"
            
            display_status = status
            if status == "running":
                display_status = "运行中"
            elif status == "completed":
                display_status = "已完成"
            elif status == "error":
                display_status = "异常"
            elif status == "pending":
                display_status = "等待中"
            
            # 针对不同处理阶段细化状态看板头部状态 Badge 的字眼
            if "大模型" in message or "生成" in message:
                display_status = "AI生成中"
            elif "避让" in message or "空闲" in message:
                display_status = "避让中"
            elif "分析" in message:
                display_status = "分析中"
            elif "决策" in message:
                display_status = "决策中"
            elif "排队" in message or "队列" in message:
                display_status = "排队中"
            elif "成功" in message or "触达" in message:
                display_status = "已完成"

            # 仅对智能聊天回复/自动回复等监控任务进行过滤，避免被其他批量后台任务冲刷看板
            if task_type in ("自动回复", "智能聊天回复") or "回复" in message or "微信" in message:
                percent = int((progress / total) * 100) if total > 0 else 0
                status_overlay.update(
                    status=display_status,
                    detail=message,
                    friend=friend_name,
                    from_control_center=True,
                    task_type=task_type,
                    progress=percent
                )
    except Exception as overlay_ex:
        logger.debug(f"[WS] 同步任务更新至状态看板异常: {overlay_ex}")
