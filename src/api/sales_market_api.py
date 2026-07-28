"""
销冠包启用/激活相关 API
"""
from fastapi import APIRouter, Request
from src.utils.response import ok, err
from src.api.config_api.base_config import _load_configs, _save_configs

router = APIRouter()

@router.get("/api/v1/sales-market/active-package")
async def get_active_package():
    try:
        configs = _load_configs()
        active_id = configs.get("active_sales_package_id", "")
        is_inherited = True

        # 方案 A 联动
        try:
            from src.utils.instance_manager import InstanceManagerV2
            from src.api.instance_settings_api import load_instance_settings
            manager = InstanceManagerV2.get_instance()
            active_inst_id = manager.get_active_instance_id()
            if active_inst_id and active_inst_id in manager.get_all_instances():
                wxid = active_inst_id
                cfg = load_instance_settings(wxid)
                inst_pkg = cfg.get("sales_package_id", "")
                if inst_pkg:
                    active_id = inst_pkg
                    is_inherited = False
        except Exception:
            pass

        return ok({"active_package_id": active_id, "is_inherited": is_inherited})
    except Exception as e:
        return err(50000, f"获取当前启用销冠包失败: {e}")

@router.post("/api/v1/sales-market/active-package")
async def set_active_package(request: Request):
    try:
        data = await request.json()
        package_id = data.get("package_id") or ""
        
        # 方案 A 联动：根据当前活跃微信是专属还是继承，决定写入专属还是全局
        active_wxid = None
        try:
            from src.utils.instance_manager import InstanceManagerV2
            manager = InstanceManagerV2.get_instance()
            active_inst_id = manager.get_active_instance_id()
            if active_inst_id and active_inst_id in manager.get_all_instances():
                active_wxid = active_inst_id
        except Exception:
            pass

        if active_wxid:
            from src.api.instance_settings_api import load_instance_settings, save_instance_settings
            try:
                cfg = load_instance_settings(active_wxid)
                # 用户主动切换时，直接写入当前账号专属配置（无论之前是否有专属值）
                cfg["sales_package_id"] = package_id
                save_instance_settings(active_wxid, cfg)

                # 实时同步到运行中的 monitor
                from src.api.instance_settings_api import _sync_to_live_monitor
                _sync_to_live_monitor(active_wxid, cfg)

                return ok({"success": True, "active_package_id": package_id, "is_inherited": False})
            except Exception as inst_ex:
                import logging
                logging.getLogger(__name__).warning(f"[销冠包] 写入专属配置失败，降级写全局: {inst_ex}")

        # 无活跃微信或专属写入失败时，跟随全局：修改全局配置
        configs = _load_configs()
        configs["active_sales_package_id"] = package_id
        _save_configs(configs)
        return ok({"success": True, "active_package_id": package_id, "is_inherited": True})
    except Exception as e:
        return err(50000, f"切换销冠包策略失败: {e}")
