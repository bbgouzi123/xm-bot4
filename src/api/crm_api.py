"""
CRM API — 客户画像管理 + 行业配置管理

提供的端点：
1. 行业配置 CRUD + 切换
2. 客户画像列表 + 详情 + 统计
3. 标签搜索 + 同步状态
"""
from fastapi import APIRouter, Request
import logging
from src.utils.response import ok, err, ok_msg

router = APIRouter()
logger = logging.getLogger(__name__)


def _async_cloud_push(setting_key: str, data: dict):
    """异步推送设置到同步后端（不阻塞 API 响应）"""
    import threading
    def _push():
        try:
            from src.utils.cloud_sync import get_cloud_client
            get_cloud_client().save_setting(setting_key, data)
        except Exception:
            pass
    threading.Thread(target=_push, daemon=True).start()


from .crm_account_api import clear_chat_context_for_account as _clear_chat_context_for_account




def _get_industry_config():
    """默认获取全局行业配置"""
    from src.crm.industry_config import IndustryConfigManager
    return IndustryConfigManager(account_id="global")


def _compute_activated_ids(profiles, active_id: str, max_industries: int) -> list:
    """
    计算「已激活行业 ID 列表」— 在订阅配额内可使用的行业。
    规则：
    - max_industries == -1（旗舰版）→ 全部返回
    - 否则：优先保留当前活跃行业，其余按原始顺序依次填充至配额上限
    """
    all_ids = [p.id for p in profiles]
    if max_industries < 0:
        return all_ids
    if max_industries == 0:
        # 0 表示无权限，仅保留当前活跃的（防止切换）
        return [active_id] if active_id and active_id in all_ids else []

    activated = []
    # 1. 优先保留当前活跃行业
    if active_id and active_id in all_ids:
        activated.append(active_id)
    # 2. 其余按顺序填充
    for pid in all_ids:
        if pid in activated:
            continue
        if len(activated) >= max_industries:
            break
        activated.append(pid)
    return activated


# ==================== 行业配置 ====================

@router.get("/api/crm/industry/profiles")
async def list_industry_profiles():
    """获取所有行业配置（含订阅配额信息）"""
    icm = _get_industry_config()
    profiles = icm.get_all_profiles()
    active_id = icm._active_id
    is_inherited = True

    try:
        from src.utils.instance_manager import InstanceManagerV2
        from src.api.instance_settings_api import load_instance_settings
        manager = InstanceManagerV2.get_instance()
        active_inst_id = manager.get_active_instance_id()
        if active_inst_id and active_inst_id in manager.get_all_instances():
            cfg = load_instance_settings(active_inst_id)
            if cfg.get("industry_profile_id"):
                active_id, is_inherited = cfg["industry_profile_id"], False
    except Exception as e:
        logger.warning(f"[行业联动] 获取当前活跃微信行业失败: {e}")

    # ═══ Feature Gate：附加订阅配额信息，前端据此展示锁定/解锁状态 ═══
    from src.utils.license_validator import LicenseValidator
    import asyncio
    loop = asyncio.get_running_loop()
    sub = await loop.run_in_executor(None, LicenseValidator.check_subscription)
    max_industries = sub.get("max_industries", 1)
    plan_name = sub.get("plan_name", "试用版")

    activated_ids = _compute_activated_ids(profiles, active_id, max_industries)

    return ok({
        "profiles": [p.to_dict() for p in profiles],
        "active_id": active_id,
        "is_inherited": is_inherited,
        "quota": {
            "max_industries": max_industries,
            "plan_name": plan_name,
            "activated_ids": activated_ids,
        },
    })


@router.get("/api/crm/industry/active")
async def get_active_profile():
    """获取当前激活的行业配置"""
    icm = _get_industry_config()
    active = icm.get_active_profile()
    return ok({
        "profile": active.to_dict() if active else None,
    })


@router.post("/api/crm/industry/switch")
async def switch_industry(request: Request):
    """切换行业配置（受订阅版本行业数量限制）"""
    data = await request.json()
    profile_id = data.get("profile_id", "")
    icm = _get_industry_config()

    # ═══ Feature Gate：校验目标行业是否在已激活配额内 ═══
    from src.utils.license_validator import LicenseValidator
    import asyncio
    loop = asyncio.get_running_loop()
    sub = await loop.run_in_executor(None, LicenseValidator.check_subscription)
    max_industries = sub.get("max_industries", 1)
    # max_industries == -1 表示不限制（旗舰版）
    if max_industries > 0:
        all_profiles = icm.get_all_profiles()
        active_id = icm._active_id
        activated_ids = _compute_activated_ids(all_profiles, active_id, max_industries)
        if profile_id not in activated_ids:
            return err(
                40301,
                f"当前版本（{sub.get('plan_name', '试用版')}）最多可使用 {max_industries} 个行业配置，"
                f"该行业不在可用配额内，请升级套餐后解锁"
            )

    active_wxid = None
    try:
        from src.utils.instance_manager import InstanceManagerV2
        manager = InstanceManagerV2.get_instance()
        active_inst_id = manager.get_active_instance_id()
        if active_inst_id and active_inst_id in manager.get_all_instances():
            active_wxid = active_inst_id
    except Exception as e:
        logger.warning(f"[行业联动] 获取活跃微信号失败: {e}")

    if active_wxid:
        from src.api.instance_settings_api import load_instance_settings, save_instance_settings
        try:
            cfg = load_instance_settings(active_wxid)
            # 用户主动切换时，直接写入当前账号专属配置（无论之前是否有专属均存储）
            cfg["industry_profile_id"] = profile_id
            save_instance_settings(active_wxid, cfg)

            # 实时同步到运行中的 monitor
            from src.api.instance_settings_api import _sync_to_live_monitor
            _sync_to_live_monitor(active_wxid, cfg)

            # 🌟 切换行业后异步清空该账号所有聊天上下文，防止旧行业历史污染新行业 AI 回复
            _clear_chat_context_for_account(active_wxid)

            return ok({
                "active": icm.get_profile_by_id(profile_id).to_dict() if icm.get_profile_by_id(profile_id) else None,
                "is_inherited": False
            })
        except Exception as e:
            logger.error(f"[行业联动] 写入专属行业失败，降级写全局: {e}")


    # 降级或默认跟随全局：直接修改全局
    success = icm.switch_profile(profile_id)
    if not success:
        return err(40000, "切换失败")

    # 🌟 切换行业后异步清空当前账号所有聊天上下文，防止旧行业历史污染新行业 AI 回复
    from src.crm.account_data import get_active_account
    _clear_chat_context_for_account(get_active_account() or "main")

    active = icm.get_active_profile()
    return ok({
        "active": active.to_dict() if active else None,
        "is_inherited": True
    })


@router.post("/api/crm/industry/create")
async def create_industry(request: Request):
    """创建行业配置（受订阅版本行业数量限制）"""
    data = await request.json()
    icm = _get_industry_config()

    # ═══ Feature Gate：校验行业配置数量是否超出订阅配额 ═══
    from src.utils.license_validator import LicenseValidator
    import asyncio
    loop = asyncio.get_running_loop()
    sub = await loop.run_in_executor(None, LicenseValidator.check_subscription)
    max_industries = sub.get("max_industries", 1)
    if max_industries > 0:  # -1 = 不限制
        current_count = len(icm.get_all_profiles())
        if current_count >= max_industries:
            return err(
                40301,
                f"当前版本（{sub.get('plan_name', '试用版')}）最多支持 {max_industries} 个行业配置，"
                f"已创建 {current_count} 个，请升级套餐后再添加"
            )

    profile = icm.create_profile(
        name=data.get("name", "新配置"),
        product=data.get("product", ""),
        selling_point=data.get("selling_point", ""),
        persona=data.get("persona", ""),
        forbidden=data.get("forbidden", ""),
        knowledge=data.get("knowledge", ""),
        intensity=data.get("intensity", 2),
        price_list=data.get("price_list", []),
        icon=data.get("icon", "🤖"),
        chat_eq=data.get("chat_eq"),
        homepage_link=data.get("homepage_link", ""),
        enable_live_record=data.get("enable_live_record", True),
        phone=data.get("phone", ""),
        address=data.get("address", ""),
    )
    return ok({"profile": profile.to_dict()})


@router.post("/api/crm/industry/update")
async def update_industry(request: Request):
    """更新行业配置"""
    data = await request.json()
    profile_id = data.pop("id", "")
    if not profile_id:
        return err(40000, "缺少 id")
    icm = _get_industry_config()
    updated_id = icm.update_profile(profile_id, data)
    if updated_id:
        # 🌟 核心修复：如果发生了克隆（如系统模板修改后自动克隆），
        # 且当前活跃微信实例正绑定在旧行业 profile_id 上，则自动将其专属配置切换至新行业 updated_id！
        if updated_id != profile_id:
            try:
                from src.utils.instance_manager import InstanceManagerV2
                from src.api.instance_settings_api import load_instance_settings, save_instance_settings, _sync_to_live_monitor
                manager = InstanceManagerV2.get_instance()
                active_inst_id = manager.get_active_instance_id()
                if active_inst_id and active_inst_id in manager.get_all_instances():
                    cfg = load_instance_settings(active_inst_id)
                    if cfg.get("industry_profile_id") == profile_id or not cfg.get("industry_profile_id"):
                        cfg["industry_profile_id"] = updated_id
                        save_instance_settings(active_inst_id, cfg)
                        _sync_to_live_monitor(active_inst_id, cfg)
                        logger.info(f"[行业配置] 自动将活跃微信实例 {active_inst_id} 绑定升级为新克隆行业: {updated_id}")
            except Exception as bind_err:
                logger.warning(f"[行业配置] 升级活跃微信专属行业绑定异常: {bind_err}")

        profile = icm.get_profile_by_id(updated_id)
        return ok({"profile": profile.to_dict() if profile else None})
    return err(40400, "配置不存在")


@router.post("/api/crm/industry/delete")
async def delete_industry(request: Request):
    """删除行业配置"""
    data = await request.json()
    profile_id = data.get("profile_id", "")
    icm = _get_industry_config()
    success = icm.delete_profile(profile_id)
    if success:
        return ok_msg("删除成功")
    return err(40400, "配置不存在")


@router.get("/api/crm/industry/templates")
async def get_industry_templates():
    """获取行业模板（优先同步后端缓存，硬编码仅兜底）"""
    from src.crm.industry_config import SYSTEM_TEMPLATES, IndustryProfile
    
    # 优先从同步后端缓存读取（与 IndustryConfigManager._load 一致）
    source_templates = SYSTEM_TEMPLATES
    try:
        from src.utils.cloud_sync import load_cloud_cache_fast
        cloud = load_cloud_cache_fast("industry_templates.json")
        if isinstance(cloud, dict) and "data" in cloud:
            cloud = cloud.get("data", [])
        if cloud and isinstance(cloud, list) and len(cloud) > 0:
            source_templates = cloud
    except Exception:
        pass
    
    templates = {}
    for tmpl in source_templates:
        if not isinstance(tmpl, dict):
            continue
        p = IndustryProfile.from_dict(tmpl)
        templates[tmpl.get("id", "")] = p.to_dict()
    return ok({"templates": templates})


@router.post("/api/crm/industry/reset")
async def reset_industry(request: Request):
    """恢复系统行业到默认模板（删除私有覆写）
    
    用户误修改系统预设行业后，调用此接口可一键恢复到同步后端/系统模板默认值。
    """
    data = await request.json()
    profile_id = data.get("profile_id", "")
    if not profile_id:
        return err(40000, "缺少 profile_id")
    icm = _get_industry_config()
    success = icm.reset_to_default(profile_id)
    if success:
        profile = icm.get_profile_by_id(profile_id)
        return ok({"profile": profile.to_dict() if profile else None})
    return err(40400, "该行业不是系统模板或未曾修改过")

