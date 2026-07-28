from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import uuid
import logging
import os
import subprocess
import time
try:
    import winreg
except ImportError:
    winreg = None
from src.utils.response import ok, err, ok_msg

try:
    import uiautomation as auto
except ImportError:
    auto = None

from src.utils.instance_manager import InstanceManagerV2

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/instances", tags=["instances"])
manager = InstanceManagerV2.get_instance()


class SetActiveRequest(BaseModel):
    instance_id: str



_last_scan_time = 0.0

@router.get("")
async def get_instances():
    """获取所有已注册的微信实例及当前活跃项"""
    # 自动探测同步当前系统中的微信窗口以更新或绑定实例
    global _last_scan_time
    now = time.time()
    # 【优化】引入 10 秒的扫描冷冻期，防止高频轮询引起工作线程池堆积
    if now - _last_scan_time > 10.0:
        _last_scan_time = now
        try:
            from fastapi.concurrency import run_in_threadpool
            from src.api.instance_helpers import do_scan_sync
            # 【优化】采用 run_in_threadpool 异步化扫描，杜绝主事件循环的同步 IO 阻塞
            await run_in_threadpool(do_scan_sync)
        except Exception as e_scan:
            logger.debug(f"[实例] get_instances 内自动扫描异常: {e_scan}")

    # 动态同步底层驱动最新抓取到的信息（解决后端默默抓完但未通知到 InstanceManager 的情况）
    # 动态注入实时监控运行状态（active=True 表示 ChatMonitor 正在运行，接管中）
    # 这让前端能区分"UI 查看焦点"和"实际接管运行"，消除多开时的用户误解
    try:
        from app.state import account_manager as am
        from src.crm.account_data import make_avatar_url
        all_inst = manager.get_all_instances()
        for inst_id, inst_data in all_inst.items():
            hwnd = inst_data.get("window_handle")
            wxid = inst_data.get("wxid") or inst_id
            
            # 实时从本地元数据文件获取最新、最真实的昵称以防丢失
            latest_nickname = ""
            if wxid:
                try:
                    from src.crm.account_data import _load_account_meta
                    meta = _load_account_meta(wxid)
                    if meta and meta.get("nickname") and meta.get("nickname") != wxid:
                        latest_nickname = meta["nickname"]
                except Exception:
                    pass

            if hwnd and hwnd in am._instances:
                target_drv = am._instances[hwnd].driver
                update_data = {}
                
                # 统一规整 _nickname: 如果它是 wxid，说明没有真正获取到昵称，应当被视为空
                drv_nick = target_drv._nickname
                if drv_nick == target_drv._wxid or (drv_nick and drv_nick.startswith("wxid_")):
                    drv_nick = ""

                # 如果驱动还没拿到昵称，尝试通过解密本地 WCDB 数据库进行静默提取
                if not drv_nick:
                    try:
                        from src.wechat_4x.db_profile_extractor import extract_profile_from_db
                        res = extract_profile_from_db(hwnd, target_drv._wxid)
                        if res:
                            db_wxid, db_nickname = res
                            if db_nickname:
                                drv_nick = db_nickname
                                target_drv._nickname = db_nickname
                            if db_wxid:
                                target_drv._wxid = db_wxid
                                update_data["wxid"] = db_wxid
                                update_data["avatar"] = make_avatar_url(db_wxid)
                    except Exception as e_db:
                        logger.debug(f"[实例] 在线实例从数据库静默提取信息异常: {e_db}")

                if latest_nickname and not drv_nick:
                    target_drv._nickname = latest_nickname
                    drv_nick = latest_nickname

                current_nick = inst_data.get("nickname")
                
                # 优先采用活跃驱动中拿到的实时最新昵称，如果有变化，同步更新到本地元数据文件防闪烁
                if drv_nick and not drv_nick.startswith("wxid_"):
                    if drv_nick != current_nick:
                        update_data["nickname"] = drv_nick
                    if drv_nick != latest_nickname:
                        try:
                            from src.crm.account_data import _save_account_meta
                            _save_account_meta(wxid, drv_nick, wxid)
                            latest_nickname = drv_nick
                        except Exception as e_save:
                            logger.debug(f"[实例] 实时同步昵称到本地元数据异常: {e_save}")
                # 驱动没拿到昵称时，降级使用本地缓存
                elif latest_nickname and latest_nickname != current_nick and not latest_nickname.startswith("wxid_"):
                    update_data["nickname"] = latest_nickname

                if target_drv._wxid:
                    if target_drv._wxid != inst_data.get("wxid") or not inst_data.get("avatar"):
                        update_data["wxid"] = target_drv._wxid
                        update_data["avatar"] = make_avatar_url(target_drv._wxid)

                if update_data:
                    manager.update_instance(inst_id, update_data)
            else:
                current_nick = inst_data.get("nickname")
                db_nick = ""
                # 即使不处于 am._instances (可能微信在运行但尚未接管)，如果 hwnd 可用，尝试通过解密本地数据库提取
                if hwnd:
                    try:
                        from src.wechat_4x.db_profile_extractor import extract_profile_from_db
                        res = extract_profile_from_db(hwnd, wxid)
                        if res:
                            db_wxid, db_nickname = res
                            update_fields = {}
                            if db_nickname and not db_nickname.startswith("wxid_"):
                                db_nick = db_nickname
                                if db_nickname != current_nick:
                                    update_fields["nickname"] = db_nickname
                                    current_nick = db_nickname
                            if db_wxid:
                                if db_wxid != inst_data.get("wxid") or not inst_data.get("avatar"):
                                    update_fields["wxid"] = db_wxid
                                    update_fields["avatar"] = make_avatar_url(db_wxid)
                            if update_fields:
                                manager.update_instance(inst_id, update_fields)
                    except Exception:
                        pass

                # 优先采用数据库解密出的实时最新昵称，如有变化，同步更新到本地元数据缓存防闪烁
                if db_nick and not db_nick.startswith("wxid_"):
                    if db_nick != latest_nickname:
                        try:
                            from src.crm.account_data import _save_account_meta
                            _save_account_meta(wxid, db_nick, wxid)
                            latest_nickname = db_nick
                        except Exception:
                            pass
                # 否则降级使用本地元数据缓存
                elif latest_nickname and latest_nickname != current_nick and not latest_nickname.startswith("wxid_"):
                    manager.update_instance(inst_id, {"nickname": latest_nickname})
    except Exception as e:
        logger.debug(f"[多开] 动态同步底层实例信息异常: {e}")

    # 动态注入备注 (note) 与实时接管/监控状态 (active)
    from src.api.instance_settings_api import load_instance_settings
    raw_instances = manager.get_all_instances()
    enriched_instances = {}
    for inst_id, inst_data in raw_instances.items():
        wxid = inst_data.get("wxid")
        note = ""
        if wxid:
            try:
                cfg = load_instance_settings(wxid)
                note = cfg.get("note", "")
            except Exception:
                pass
        
        # 动态获取该实例的监控是否处于运行状态，覆盖/注入前端期望的 active 属性（表示接管中状态）
        # 这样既避免了修改底层 mmap 中的 active (表示查看焦点) 引起的无限轮询广播与闪烁，又能精准展现给前端
        hwnd = inst_data.get("window_handle")
        is_monitoring = False
        if hwnd:
            try:
                from app.state import account_manager as am
                if hwnd in am._instances:
                    target_mon = am._instances[hwnd].monitor
                    is_monitoring = bool(getattr(target_mon, "_running", False))
            except Exception:
                pass

        enriched_instances[inst_id] = {
            **inst_data,
            "active": is_monitoring,
            "note": note
        }

    # 检查多开限制
    from src.utils.license_validator import LicenseValidator
    try:
        features = LicenseValidator.check_features()
        max_wechat = int(features.get("max_wechat", 1) or 1)
    except Exception:
        max_wechat = 1

    _ov = os.environ.get("XM_BOT4_MAX_WECHAT_OVERRIDE", "").strip()
    if _ov:
        try:
            max_wechat = max(1, int(_ov))
        except ValueError:
            pass

    return ok({
        "active_id": manager.get_active_instance_id(),
        "instances": enriched_instances,
        "max_wechat": max_wechat
    })

@router.get("/active")
async def get_active_instance():
    active_id = manager.get_active_instance_id()
    inst = manager.get_all_instances().get(active_id) if active_id else None
    return ok({
        "id": active_id,
        "nickname": inst.get("nickname", ""),
        "wxid": inst.get("wxid", ""),
        "avatar": inst.get("avatar", ""),
    } if inst else None)


@router.post("/active")
async def set_active(req: SetActiveRequest):
    if not manager.set_active_instance(req.instance_id):
        raise HTTPException(status_code=404, detail="Instance not found")
        
    inst = manager.get_all_instances().get(req.instance_id)
    hwnd = inst.get("window_handle") if inst else None
    target_wxid = inst.get("wxid") if inst else req.instance_id
    target_nickname = inst.get("nickname") if inst else ""

    if hwnd:
        try:
            from app.state import account_manager as am, driver, monitor
            if am and hwnd in am._instances:
                target_driver = am._instances[hwnd].driver
                driver.__dict__.update(target_driver.__dict__)
                if monitor:
                    monitor.driver = driver
                target_wxid = target_driver._wxid or target_wxid
                target_nickname = target_driver._nickname or target_nickname
            else:
                driver.connect_by_hwnd(hwnd)
                target_wxid = driver._wxid or target_wxid
                target_nickname = driver._nickname or target_nickname
        except Exception as e:
            logger.error(f"切换底层驱动焦点失败: {e}")
            
    try:
        from src.crm.account_data import set_active_account
        set_active_account(target_wxid, target_nickname)
        try:
            from src.friend.friend_queue.storage import reload_from_cloud_for_active_bot
            reload_from_cloud_for_active_bot()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"同步设置活跃账号隔离上下文异常: {e}")

    return ok({"active_id": req.instance_id})

@router.post("/scan")
async def scan_instances():
    from fastapi.concurrency import run_in_threadpool
    from src.api.instance_helpers import do_scan_sync
    found = await run_in_threadpool(do_scan_sync)
    return ok({"found_count": found, 
        "active_id": manager.get_active_instance_id(),
        "instances": manager.get_all_instances()})

@router.delete("/{instance_id}")
async def remove_instance(instance_id: str):
    inst = manager.get_all_instances().get(instance_id)
    if inst and inst.get("window_handle"):
        try:
            from app.state import account_manager as am
            if am and inst["window_handle"] in am._instances:
                del am._instances[inst["window_handle"]]
                if am._primary_hwnd == inst["window_handle"]:
                    am._primary_hwnd = next(iter(am._instances), None)
        except Exception:
            pass
    try:
        from src.utils.wechat_key_store import clear_persisted_wechat_key
        clear_persisted_wechat_key(instance_id)
    except Exception:
        pass
    try:
        from src.crm.account_data import get_account_data_dir
        import shutil
        d = get_account_data_dir(instance_id)
        if os.path.exists(d):
            shutil.rmtree(d)
    except Exception:
        pass
    manager.remove_instance(instance_id)
    return ok_msg("操作成功")


_saved_positions = {}
_is_tiled_state = False


@router.post("/tile")
async def tile_instances():
    global _is_tiled_state, _saved_positions
    from src.api.instance_helpers import perform_tile_instances
    res = perform_tile_instances(_is_tiled_state, _saved_positions)
    if isinstance(res, dict) and res.get("code") in (200, 20000):
        _is_tiled_state = not _is_tiled_state
    return res


@router.post("/test-jump-unread")
async def test_jump_unread():
    from app.state import account_manager, driver
    from src.utils.instance_manager import InstanceManagerV2
    
    target_driver = None
    manager = InstanceManagerV2.get_instance()
    active_id = manager.get_active_instance_id()
    
    if active_id:
        if active_id in account_manager._instances:
            inst = account_manager._instances[active_id]
            if inst and getattr(inst, "driver", None):
                target_driver = inst.driver
        elif isinstance(active_id, str) and active_id.isdigit() and int(active_id) in account_manager._instances:
            inst = account_manager._instances[int(active_id)]
            if inst and getattr(inst, "driver", None):
                target_driver = inst.driver
        else:
            for inst in account_manager._instances.values():
                if inst.wxid == active_id or (inst.driver and getattr(inst.driver, "_wxid", None) == active_id):
                    target_driver = inst.driver
                    break

    if not target_driver:
        target_driver = account_manager.primary_driver or driver

    if not target_driver or not getattr(target_driver, "hwnd", None) or not target_driver.is_connected():
        return err("当前未连接任何微信实例，请先扫描或选择微信实例")

    from fastapi.concurrency import run_in_threadpool
    success = await run_in_threadpool(target_driver.jump_to_next_unread, True)
    return ok_msg("测试成功") if success else err("触发失败，未找到未读")




