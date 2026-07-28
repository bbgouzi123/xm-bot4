"""
好友与群聊过滤配置 API（白名单 / 排除黑名单一键配置）
"""
import logging
import re
from fastapi import APIRouter, Request
from src.utils.response import ok, err
from src.utils.contacts_cache import contacts_cache
from src.crm.account_data import get_active_account, get_account_settings, save_account_settings
from .friend_filter_helper import resolve_is_group, trigger_whitelist_retry

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/api/contacts/add_whitelist")
async def add_to_whitelist(request: Request):
    """一键将好友或群聊加入白名单"""
    try:
        data = await request.json()
        name = data.get("name", "").strip()
        is_group = data.get("is_group", False)
        if not name:
            return err(40000, "好友或群聊名称不能为空")

        wxid = get_active_account()
        is_group = resolve_is_group(name, is_group, wxid)

        # 🌟 联合覆盖自愈机制：找出所有需要同步更新配置的微信号集合（包含 default 账号及当前运行中已连接的所有微信真实 ID）
        target_wxids = {wxid} if wxid else set()
        target_wxids.add("default")
        try:
            from app.state import account_manager as am
            if am:
                for inst in am._instances.values():
                    if inst.driver.is_connected() and inst.wxid:
                        target_wxids.add(inst.wxid)
                if am.primary_instance and am.primary_instance.wxid:
                    target_wxids.add(am.primary_instance.wxid)
        except Exception:
            pass

        # 优先使用数据库同步查找真实的 wxid
        import asyncio
        loop = asyncio.get_running_loop()
        found_wxid = await loop.run_in_executor(None, contacts_cache.find_wxid_with_db_sync, wxid, name, is_group)

        # 🌟 修复：如果直接从 active_account (可能是 default) 找不到，遍历已连接的真实 bot 账号反查其真实的 wxid
        if not found_wxid:
            for twx in target_wxids:
                if twx and twx != "default":
                    found_wxid = await loop.run_in_executor(None, contacts_cache.find_wxid_with_db_sync, twx, name, is_group)
                    if found_wxid:
                        logger.info(f"[API] 成功通过在线实例 {twx} 反查到群聊 '{name}' 的 wxid={found_wxid}")
                        break

        # 🌟 再次降级兜底：从 WebSocket 任务缓存中反查该会话的真实 wxid (防御新好友/陌生人尚未写入 contacts.db 的场景)
        if not found_wxid:
            try:
                from src.utils.websocket_manager import ws_manager
                possible_task_ids = [f"whitelist_{name}", f"auto_reply_{name}"]
                if hasattr(ws_manager, "task_cache"):
                    for t_id in possible_task_ids:
                        if t_id in ws_manager.task_cache:
                            cached_wxid = ws_manager.task_cache[t_id].get("data", {}).get("friend_wxid")
                            if cached_wxid:
                                found_wxid = cached_wxid
                                logger.info(f"[API] 从任务缓存中成功反查到 '{name}' 的 wxid={found_wxid}")
                                break
            except Exception as e_cache:
                logger.debug(f"[API] 从任务缓存反查 wxid 异常: {e_cache}")

        target_id = f"wxid:{found_wxid}" if found_wxid else f"namecat:{name}::{'群聊' if is_group else '联系人'}"

        # 收集所有需要排除/删除的变体，以彻底清除在另一个列表中的记录并防重复
        variants = {name, target_id}
        if found_wxid:
            variants.add(f"wxid:{found_wxid}")
            variants.add(found_wxid)
        variants.add(f"namecat:{name}::{'群聊' if is_group else '联系人'}")
        clean_name = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
        variants.add(clean_name)
        variants.add(f"namecat:{clean_name}::{'群聊' if is_group else '联系人'}")

        whitelist_key = "auto_chat_group_whitelist" if is_group else "auto_chat_friend_whitelist"
        excludes_key = "auto_chat_group_excludes" if is_group else "auto_chat_friend_excludes"

        # 🌟 用户点击“加白”联动规则：自动把‘开启聊天’系统回复开关也同步开启
        try:
            from src.api.config_api import _load_configs, _save_configs
            g_cfgs = _load_configs() or {}
            if not g_cfgs.get("auto_reply_enabled", True):
                g_cfgs["auto_reply_enabled"] = True
                _save_configs(g_cfgs)
                logger.info("[API] 加白自动开启全局 auto_reply_enabled 开关")
        except Exception as e_cfg:
            logger.debug(f"[API] 自动开启全局回复开关异常: {e_cfg}")

        # 遍历所有目标账号，全部同步覆盖写入
        for target_wx in target_wxids:
            if not target_wx:
                continue
            try:
                settings = get_account_settings(target_wx, force_reload=True)
                settings["auto_reply_enabled"] = True
                reply = settings.get("reply", {})

                # 1. 更新白名单：双重保险强绑定追加（namecat + wxid）
                lst = reply.get(whitelist_key, [])
                if not isinstance(lst, list):
                    lst = list(lst)

                # 追加 namecat 变体
                namecat_val = f"namecat:{name}::{'群聊' if is_group else '联系人'}"
                if namecat_val not in lst:
                    lst.append(namecat_val)
                    
                # 追加 wxid 变体
                if found_wxid:
                    wxid_val = f"wxid:{found_wxid}"
                    if wxid_val not in lst:
                        lst.append(wxid_val)

                reply[whitelist_key] = lst

                # 2. 顺便将其从黑名单中移出以对齐一致性
                ex_lst = reply.get(excludes_key, [])
                if isinstance(ex_lst, list):
                    new_ex_lst = [x for x in ex_lst if x not in variants]
                    if len(new_ex_lst) != len(ex_lst):
                        reply[excludes_key] = new_ex_lst

                settings["reply"] = reply
                save_account_settings(settings, target_wx)
            except Exception as e_save:
                logger.warning(f"[API] 同步加白配置到账号 {target_wx} 异常: {e_save}")

        try:
            from src.uia.session import session_type_cache
            session_type_cache.clear_session_type(name)
            if clean_name:
                session_type_cache.clear_session_type(clean_name)
        except Exception as e:
            logger.debug(f"[API] 清除会话缓存失败（非阻塞）: {e}")

        # 3. 顺便清除该联系人在 contacts_cache 中可能残留的人工接管 (is_takeover) 标志
        try:
            target_key = found_wxid or name
            if target_key:
                contacts_cache.update_friend(wxid, target_key, is_takeover=False)
                contacts_cache.merge_friend_detail_by_name(wxid, name, "群聊" if is_group else "联系人", is_takeover=False)
                logger.info(f"[API] 自动清除 {name} ({target_key}) 的 is_takeover 人工接管状态，以允许其履约自动回复")
        except Exception as e_takeover:
            logger.warning(f"[API] 清除 is_takeover 状态异常: {e_takeover}")


        # 触发被拦截消息的自动回复重试/放行
        await trigger_whitelist_retry(wxid, name, is_group, found_wxid)

        logger.info(f"[API] 一键加白成功: 已将 '{name}' (is_group={is_group}) 写入所有关联实例配置中")
        return ok({"message": f"成功将 '{name}' 加入白名单"})
    except Exception as e:
        logger.error(f"加白失败: {e}")
        return err(40000, "操作失败", {"error": str(e)})

@router.post("/api/contacts/add_blacklist")
async def add_to_blacklist(request: Request):
    """一键将好友或群聊加入免打扰排除名单（拉黑）"""
    try:
        data = await request.json()
        name = data.get("name", "").strip()
        is_group = data.get("is_group", False)
        if not name:
            return err(40000, "好友或群聊名称不能为空")

        wxid = get_active_account()
        is_group = resolve_is_group(name, is_group, wxid)

        # 优先使用数据库同步查找真实的 wxid
        import asyncio
        loop = asyncio.get_running_loop()
        found_wxid = await loop.run_in_executor(None, contacts_cache.find_wxid_with_db_sync, wxid, name, is_group)

        target_id = f"wxid:{found_wxid}" if found_wxid else f"namecat:{name}::{'群聊' if is_group else '联系人'}"

        # 收集所有需要排除/删除的变体，以彻底清除在另一个列表中的记录并防重复
        variants = {name, target_id}
        if found_wxid:
            variants.add(f"wxid:{found_wxid}")
            variants.add(found_wxid)
        variants.add(f"namecat:{name}::{'群聊' if is_group else '联系人'}")
        clean_name = re.sub(r'[\(（]\d+[\)）]$', '', name).strip()
        variants.add(clean_name)
        variants.add(f"namecat:{clean_name}::{'群聊' if is_group else '联系人'}")

        # 🌟 联合覆盖自愈机制：找出所有需要同步更新配置的微信号集合
        target_wxids = {wxid} if wxid else set()
        target_wxids.add("default")
        try:
            from app.state import account_manager as am
            if am:
                for inst in am._instances.values():
                    if inst.driver.is_connected() and inst.wxid:
                        target_wxids.add(inst.wxid)
                if am.primary_instance and am.primary_instance.wxid:
                    target_wxids.add(am.primary_instance.wxid)
        except Exception:
            pass

        excludes_key = "auto_chat_group_excludes" if is_group else "auto_chat_friend_excludes"
        whitelist_key = "auto_chat_group_whitelist" if is_group else "auto_chat_friend_whitelist"

        # 遍历所有目标账号，全部同步覆盖写入排除名单
        for target_wx in target_wxids:
            if not target_wx:
                continue
            try:
                settings = get_account_settings(target_wx, force_reload=True)
                reply = settings.get("reply", {})

                # 1. 更新排除名单：双重保险强绑定追加（namecat + wxid）
                lst = reply.get(excludes_key, [])
                if not isinstance(lst, list):
                    lst = list(lst)

                # 追加 namecat 变体
                namecat_val = f"namecat:{name}::{'群聊' if is_group else '联系人'}"
                if namecat_val not in lst:
                    lst.append(namecat_val)
                    
                # 追加 wxid 变体
                if found_wxid:
                    wxid_val = f"wxid:{found_wxid}"
                    if wxid_val not in lst:
                        lst.append(wxid_val)

                reply[excludes_key] = lst

                # 2. 顺便将其从白名单中移出以对齐一致性
                wh_lst = reply.get(whitelist_key, [])
                if isinstance(wh_lst, list):
                    new_wh_lst = [x for x in wh_lst if x not in variants]
                    if len(new_wh_lst) != len(wh_lst):
                        reply[whitelist_key] = new_wh_lst

                settings["reply"] = reply
                save_account_settings(settings, target_wx)
            except Exception as e_save:
                logger.warning(f"[API] 同步加黑配置到账号 {target_wx} 异常: {e_save}")

        logger.info(f"[API] 一键免打扰成功: 已将 '{name}' 写入所有关联实例排除名单中")
        return ok({"message": f"成功将 '{name}' 加入免打扰排除名单"})
    except Exception as e:
        logger.error(f"加黑失败: {e}")
        return err(40000, "操作失败", {"error": str(e)})


