"""
好友与通讯录 API（纯内存 + 同步后端，零 SQLite — 只保留数据查询，同步操作已移至 friend_sync_api.py）
"""
import logging
from fastapi import APIRouter, Request
from src.utils.response import ok, err
from src.crm.account_data import ready_barrier

logger = logging.getLogger(__name__)
router = APIRouter()

_driver = None


def init(driver):
    global _driver
    _driver = driver


def get_driver():
    return _driver


@router.get("/api/friend/list")
async def get_friend_list(limit: int = 1000, instance_id: str = None):
    """从内存缓存获取联系人列表"""
    await ready_barrier.wait_until_ready()
    from src.utils.contacts_cache import contacts_cache
    from src.utils.instance_manager import InstanceManagerV2
    
    manager = InstanceManagerV2.get_instance()
    active_inst = None
    all_instances = manager.get_all_instances()
    if instance_id:
        if instance_id in all_instances:
            active_inst = all_instances[instance_id]
        else:
            for inst in all_instances.values():
                if inst.get("wxid") == instance_id:
                    active_inst = inst
                    break
                    
    if not active_inst:
        active_inst = manager.get_active_instance()
        
    if not active_inst and not instance_id:
        logger.info("[/friend/list] 当前无微信实例且未提供 instance_id，直接返回空好友列表")
        return ok({"data": [], "total": 0})
        
    active_id = (active_inst.get("wxid") if active_inst else None) or instance_id or "default"
    friends = contacts_cache.get_friends(active_id)

    if not friends:
        try:
            contacts_cache.load_from_cloud()
            friends = contacts_cache.get_friends(active_id)
        except Exception as e:
            logger.error(f"从持久层恢复好友列表失败: {e}")
            
    if not friends:
        friends = []
        
    # 清洗脏数据 + 全局去重（防止多账号或 default 回退导致的翻倍合并）
    sys_prefixes = ("新的朋友", "公众号", "企业微信联系人", "群聊", "标签", "服务号", "我的企业", "联系人", "文件传输助手")
    from src.uia.contacts.constants import is_synthetic_placeholder_wxid
    
    real_wxid_map = {}
    name_cat_map = {}
    
    for f in friends:
        name = f.get("name", "").strip()
        category = f.get("category", "联系人").strip() or "联系人"
        is_sys = False
        # ⚠️【A-Z 分组头过滤修复】len(name) == 1 时判定为字母分组头，但需排除中文单字昵称（如“杨”），需加上 isascii 判断
        if len(name) == 1 and name.isascii() and name.isalpha():
            is_sys = True
        for pre in sys_prefixes:
            if name.startswith(pre):
                suffix = name[len(pre):].strip()
                if not suffix or suffix.isdigit():
                    is_sys = True
                    break
            elif name == pre:
                is_sys = True
                break
                
        if not is_sys:
            wxid = (f.get("wxid") or "").strip()
            is_real = wxid and not is_synthetic_placeholder_wxid(wxid)
            key = (name, category)
            if is_real:
                real_wxid_map[wxid] = f
                name_cat_map[key] = f
            else:
                if key not in name_cat_map:
                    name_cat_map[key] = f
            
    final_friends = []
    seen_ids = set()
    for f in real_wxid_map.values():
        fid = id(f)
        if fid not in seen_ids:
            final_friends.append(f)
            seen_ids.add(fid)
            
    for f in name_cat_map.values():
        fid = id(f)
        if fid not in seen_ids:
            wxid = (f.get("wxid") or "").strip()
            is_real = wxid and not is_synthetic_placeholder_wxid(wxid)
            if not is_real:
                final_friends.append(f)
                seen_ids.add(fid)
                
    friends = final_friends
        
    if limit:
        friends = friends[:limit]
    
    logger.info(f"[/friend/list] 此时内存里 account_id='{active_id}' 共有 {len(friends)} 个好友")
    return ok({"data": friends, "total": len(friends)})


@router.get("/api/group/list")
async def get_group_list(limit: int = 1000, instance_id: str = None):
    """从内存缓存获取群聊列表"""
    await ready_barrier.wait_until_ready()
    from src.utils.contacts_cache import contacts_cache
    from src.utils.instance_manager import InstanceManagerV2
    
    manager = InstanceManagerV2.get_instance()
    active_inst = None
    all_instances = manager.get_all_instances()
    if instance_id:
        if instance_id in all_instances:
            active_inst = all_instances[instance_id]
        else:
            for inst in all_instances.values():
                if inst.get("wxid") == instance_id:
                    active_inst = inst
                    break
                    
    if not active_inst:
        active_inst = manager.get_active_instance()
        
    if not active_inst and not instance_id:
        logger.info("[/group/list] 当前无微信实例且未提供 instance_id，直接返回空群聊列表")
        return ok({"data": [], "total": 0})
        
    active_id = (active_inst.get("wxid") if active_inst else None) or instance_id or "default"
    groups = contacts_cache.get_groups(active_id)

    # 调试日志
    with contacts_cache._rw_lock:
        all_keys = list(contacts_cache._groups.keys())
    logger.info(f"[调试] get_group_list: active_id={active_id!r}, 内存 _groups 中的所有 keys={all_keys}, 获取到的群聊数={len(groups) if groups else 0}")

    if not groups:
        try:
            contacts_cache.load_from_cloud()
            groups = contacts_cache.get_groups(active_id)
            with contacts_cache._rw_lock:
                all_keys_after = list(contacts_cache._groups.keys())
            logger.info(f"[调试] load_from_cloud 后: active_id={active_id!r}, keys={all_keys_after}, 获取到的群聊数={len(groups) if groups else 0}")
        except Exception as e:
            logger.error(f"从持久层恢复群聊列表失败: {e}")
            
    if not groups:
        groups = []
        
    # 全局群聊去重
    dedup_map = {}
    for g in groups:
        name = g.get("name")
        if name:
            dedup_map[name] = g
        else:
            dedup_map[id(g)] = g
    groups = list(dedup_map.values())
        
    if limit:
        groups = groups[:limit]
        
    logger.info(f"[/group/list] 此时内存里 account_id='{active_id}' 共有 {len(groups)} 个群聊")
    return ok({"data": groups, "total": len(groups)})


@router.post("/api/friend/update")
async def update_friend(request: Request):
    """更新联系人的备注或标签（内存 + 同步后端）"""
    await ready_barrier.wait_until_ready()
    try:
        from src.utils.contacts_cache import contacts_cache
        from src.crm.account_data import get_active_account
        data = await request.json()
        wxid = data.get("wxid")
        name = data.get("name")
        if not wxid and not name:
            return err(40000, "缺少 wxid 或 name")

        account_id = get_active_account() or "main"
        update_fields = {}
        if "remark" in data:
            update_fields["remark"] = data["remark"]
        if "tag" in data:
            update_fields["tag"] = data["tag"]
        if "is_takeover" in data:
            update_fields["is_takeover"] = bool(data["is_takeover"])

        # 1. 提取用于在微信中定位该好友会话的当前名称
        session_name = None
        if wxid and ("remark" in data or "tag" in data):
            friends = contacts_cache.get_friends(account_id)
            for f in friends:
                if f.get("wxid") == wxid:
                    session_name = f.get("remark") or f.get("name")
                    break

        if wxid:
            contacts_cache.update_friend(account_id, wxid, **update_fields)
        else:
            is_group = data.get("is_group", False)
            cat = "群聊" if is_group else "联系人"
            contacts_cache.merge_friend_detail_by_name(account_id, name, cat, **update_fields)
            if "remark" in data or "tag" in data:
                session_name = name

        # 2. 异步触发 UIA 物理修改微信备注和标签
        if session_name and ("remark" in data or "tag" in data):
            remark_val = data.get("remark")
            tag_val = data.get("tag")
            tags_list = [t.strip() for t in tag_val.split(",") if t.strip()] if tag_val else []

            def _do_uia_remark_tag_sync():
                try:
                    import time
                    # 留出 1.5s 供前端完成 API 响应处理与弹窗关闭
                    time.sleep(1.5)
                    from src.uia.modules.core import driver_registry
                    driver = driver_registry.get_primary_driver()
                    if driver and driver.is_connected():
                        switched = driver.ChatWith(session_name)
                        if switched:
                            driver.apply_remark_and_tags_from_chat(
                                friend_name=session_name,
                                remark=remark_val,
                                tags=tags_list
                            )
                        else:
                            logger.warning(f"[API] 无法切换到会话 '{session_name}'，同步取消")
                except Exception as ex:
                    logger.error(f"[API] 异步微信备注和标签同步失败: {ex}")

            import threading
            threading.Thread(target=_do_uia_remark_tag_sync, daemon=True, name="api_remark_tag_sync").start()

        return ok({"message": "联系人信息更新成功"})
    except Exception as e:
        logger.error(f"更新失败: {e}")
        return err(40000, "操作失败", {"error": str(e)})


@router.get("/api/contacts/export")
async def export_contacts(type: str = "friend", is_desktop: bool = False, selected_ids: str = ""):
    """导出微信通讯录为 Excel/CSV 报表资产"""
    await ready_barrier.wait_until_ready()
    from src.utils.contacts_exporter import do_export_contacts
    return do_export_contacts(type, is_desktop=is_desktop, selected_ids=selected_ids)


# /api/contacts/add_whitelist 移至 friend_filter_api.py 以对齐单文件 300 行限额


