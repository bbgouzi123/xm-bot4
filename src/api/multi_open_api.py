"""
微信智能多开与沙箱管理 API
"""
import os
import time
import subprocess
import logging
try:
    import winreg
except ImportError:
    winreg = None
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from src.utils.response import ok, err, ok_msg
from src.utils.instance_manager import InstanceManagerV2
from src.utils.multi_open_helper import auto_tile_wechat_windows, auto_bind_new_instances

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/instances", tags=["instances_multi_open"])
manager = InstanceManagerV2.get_instance()

class MultiOpenRequest(BaseModel):
    count: int = 2


def _instance_occupies_multiopen_slot(inst: object) -> bool:
    """判断是否计入套餐多开上限"""
    if not isinstance(inst, dict):
        return False
    if inst.get("status") == "offline":
        return False
    return inst.get("status") == "online" or bool(inst.get("window_handle"))


@router.post("/multi-open")
async def multi_open_wechat(req: MultiOpenRequest):
    """微信智能多开 — 三级隔离策略"""
    from src.utils.mutex_killer import (
        kill_wechat_mutex,
        build_isolated_env,
        apply_filesave_path,
        restore_filesave_path,
    )
    from src.utils.isolate_container_manager import get_isolate_container_manager, is_isolate_container_available
    from src.utils.license_validator import LicenseValidator
    from src.utils.multi_open_helper import start_background_auto_login_and_bind
    import win32gui
    from src.uia.modules.core.connect import _is_wechat_title

    # 记录当前已存在的微信句柄，用于后台精确识别新实例并自动登录/绑定
    old_hwnds = set()
    def _cb(hwnd, _):
        try:
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if cls.endswith("Qt51514QWindowIcon") and _is_wechat_title(title):
                old_hwnds.add(hwnd)
        except Exception:
            pass
    win32gui.EnumWindows(_cb, None)

    features = LicenseValidator.check_features()
    max_wechat = int(features.get("max_wechat", 1) or 1)
    _ov = os.environ.get("XM_BOT4_MAX_WECHAT_OVERRIDE", "").strip()
    if _ov:
        try:
            max_wechat = max(1, int(_ov))
            logger.info(f"[多开] 已启用 XM_BOT4_MAX_WECHAT_OVERRIDE，有效上限={max_wechat}")
        except ValueError:
            pass

    online_count = sum(
        1
        for inst in manager.get_all_instances().values()
        if _instance_occupies_multiopen_slot(inst)
    )
    if online_count >= max_wechat:
        logger.warning(f"[多开] 槽位已满：已占用 {online_count}/{max_wechat}")
        return ok(
            {
                "blocked": True,
                "reason": "multi_open_quota_exceeded",
                "max_wechat": max_wechat,
                "online_count": online_count,
                "upgrade_hints": {
                    "pro": "Pro 套餐最多可同时管理 3 个微信窗口",
                    "flagship": "旗舰套餐最多可同时管理 10 个微信窗口",
                },
            },
            msg="当前套餐的多开槽位已满，升级套餐即可继续多开",
        )
    
    if online_count + req.count > max_wechat:
        req.count = max_wechat - online_count
        logger.warning(f"[多开] 超过配额，截断启动数量为: {req.count}")
    
    from src.utils.wechat_launcher import get_wechat_path
    wechat_path = get_wechat_path()
    if wechat_path:
        process_name = os.path.basename(wechat_path).lower()
    else:
        raise HTTPException(status_code=400, detail="未检测到常规微信安装路径，无法执行自动化代码")
    
    existing_count = len(manager.get_all_instances())
    container_mgr = get_isolate_container_manager()

    if is_isolate_container_available():
        logger.info("[多开] 🏆 检测到星码安全隔离舱 DLL，使用自研隔离舱方案")
        
        start_index = existing_count + 1
        container_result = container_mgr.multi_open_wechat(wechat_path, req.count, start_index=start_index)

        if container_result["success"]:
            auto_tile_details = []
            try:
                auto_tile_details = auto_tile_wechat_windows(container_result["instances_started"])
            except Exception as e:
                logger.warning(f"[多开] 自动平铺异常: {e}")

            auto_bind_count = 0
            try:
                auto_bind_count = await auto_bind_new_instances(manager)
            except Exception as e:
                logger.warning(f"[多开] 自动绑定异常: {e}")

            # 启动后台自动登录和绑定监控（即使用户未启用一键登录，也能监听并绑定为 online 实例）
            start_background_auto_login_and_bind(old_hwnds, container_result["instances_started"], manager)

            return ok({
                "message": (
                    f"已通过自研安全隔离舱启动 {container_result['instances_started']} 个微信实例，"
                    f"每个实例拥有完全独立的物理环境与指纹防关联保护"
                    + (f"，已自动绑定 {auto_bind_count} 个" if auto_bind_count else "")
                ),
                "method": "安全隔离舱",
                "mutex_killed": 0,
                "instances_started": container_result["instances_started"],
                "sandbox_names": container_result["sandbox_names"],
                "isolated_dirs": [],
                "details": container_result["details"] + auto_tile_details,
                "auto_bound": auto_bind_count,
            })
        else:
            logger.warning(f"[多开] 隔离舱方案失败（{container_result.get('error', '未知')}），降级到 Mutex 暗杀方案")

    mutex_result = kill_wechat_mutex()
    logger.info(f"[多开] Mutex 暗杀结果: killed={mutex_result['killed_count']}, pids={mutex_result['wechat_pids']}")
    
    use_fallback = False
    if not mutex_result["success"] or (mutex_result["killed_count"] == 0 and len(mutex_result["wechat_pids"]) > 0):
        logger.warning("[多开] Mutex 暗杀不完整，回退到并发启动方案（需要先杀掉现有微信）")
        use_fallback = True
        os.system('TASKKILL /F /IM wechat.exe >nul 2>&1')
        os.system('TASKKILL /F /IM weixin.exe >nul 2>&1')
        time.sleep(1)
    
    instances_started = 0
    isolated_dirs: list = []
    pre_filesave_original: Optional[str] = None

    for i in range(req.count):
        try:
            if not use_fallback and i > 0:
                kill_wechat_mutex()
                time.sleep(0.3)

            instance_index = existing_count + i + 1
            try:
                env, instance_dir = build_isolated_env(instance_index)
            except Exception as e:
                logger.warning(f"[多开] 构造隔离 env 失败，退回默认环境: {e}")
                env, instance_dir = None, None

            if instance_dir:
                original = apply_filesave_path(instance_dir, process_name)
                if i == 0:
                    pre_filesave_original = original
                isolated_dirs.append(instance_dir)

            popen_kwargs = dict(
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            if env is not None:
                popen_kwargs["env"] = env
            if instance_dir:
                popen_kwargs["cwd"] = instance_dir

            subprocess.Popen([wechat_path], **popen_kwargs)
            instances_started += 1

            if i < req.count - 1:
                time.sleep(1.5)
        except Exception as e:
            logger.error(f"启动微信失败: {e}")

    try:
        restore_filesave_path(process_name, pre_filesave_original)
    except Exception as e:
        logger.warning(f"[多开] 恢复 FileSavePath 失败: {e}")

    if instances_started > 0:
        start_background_auto_login_and_bind(old_hwnds, instances_started, manager)

    method = "传统并发启动" if use_fallback else "Mutex 精准暗杀"
    return ok({
        "message": f"多开指令已执行 ({method})，预期唤醒 {instances_started} 个实例",
        "method": method,
        "mutex_killed": mutex_result["killed_count"],
        "instances_started": instances_started,
        "isolated_dirs": isolated_dirs,
        "details": mutex_result.get("details", []),
    })

# ==================== 隔离舱管理 API ====================

@router.get("/sandbox/status")
async def get_sandbox_status():
    """获取隔离舱方案的完整状态"""
    from src.utils.isolate_container_manager import get_isolate_container_manager
    mgr = get_isolate_container_manager()
    return ok(mgr.get_full_status())


@router.post("/sandbox/terminate/{index}")
async def terminate_sandbox(index: int):
    """终止指定隔离实例内的所有进程"""
    try:
        import psutil
        count = 0
        for p in psutil.process_iter(['pid', 'name', 'environ']):
            try:
                env = p.info.get('environ') or {}
                if env.get("XM_WECHAT_INSTANCE") == str(index):
                    p.kill()
                    count += 1
            except Exception:
                pass
        return ok_msg(f"已终止隔离实例 {index} 中的 {count} 个相关进程")
    except Exception as e:
        return err(50000, f"终止隔离实例进程失败: {e}")


@router.post("/sandbox/reset/{index}")
async def reset_sandbox(index: int):
    """重置指定隔离舱（清空实例数据）"""
    import shutil
    from src.utils.mutex_killer import ensure_data_isolation
    try:
        data_dir = ensure_data_isolation(index)
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)
        ensure_data_isolation(index)
        return ok_msg(f"隔离舱 {index} 已成功重置")
    except Exception as e:
        return err(50000, f"重置隔离舱失败: {e}")
