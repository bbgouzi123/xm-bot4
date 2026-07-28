from fastapi import Request
import json
import time
from src.utils.response import ok, err, ok_msg
from .state import router, CONFIG_DIR
from . import state

def _get_current_user_id() -> str:
    try:
        from src.sso_bridge import read_sso_session
        session = read_sso_session()
        if session and session.get("user"):
            return session["user"].get("id", "")
    except Exception:
        pass
    return ""

def _get_user_config_file() -> state.Path:
    uid = _get_current_user_id()
    return CONFIG_DIR / f"config_{uid[:12]}.json" if uid else CONFIG_DIR / "config.json"

from src.utils.platform_defaults import _has_valid_ai_config

import threading
from pathlib import Path
import shutil

_init_lock = threading.RLock()

def ensure_config_initialized():
    """保证配置目录和默认配置文件已初始化。
    如果用户主目录下的配置不存在，尝试从程序当前目录或 exe 同级目录复制 config.json，
    如果都找不到，则使用平台默认的配置写入一个初始 config.json，实现开箱即用。
    另外，如果存在全局 config.json 但当前登录用户的 config_<uid>.json 不存在，
    则自动将全局 config.json 复制/初始化为用户的专属配置文件，确保多账号登录无缝继承。
    """
    with _init_lock:
        import sys
        
        if not CONFIG_DIR.exists():
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                print(f"[配置初始化] 创建配置目录: {CONFIG_DIR}")
            except Exception as e:
                print(f"[配置初始化] 无法创建配置目录 {CONFIG_DIR}: {e}")
                return

        global_config_file = CONFIG_DIR / "config.json"
        user_config_file = _get_user_config_file()
        
        # 1. 如果全局 config.json 和用户专属 config 都不存在，需要寻找或创建全局 config.json
        if not global_config_file.exists() and not user_config_file.exists():
            candidates = []
            try:
                # 当前工作目录
                candidates.append(Path.cwd() / "config.json")
                # 执行程序 (sys.executable) 同级目录
                if getattr(sys, "frozen", False):
                    candidates.append(Path(sys.executable).parent / "config.json")
                else:
                    # 开发模式：脚本 (main.py) 同级目录或 backend-python 目录下
                    main_dir = Path(__file__).resolve().parent.parent.parent.parent
                    candidates.append(main_dir / "config.json")
            except Exception:
                pass

            copied = False
            for candidate in candidates:
                if candidate.exists() and candidate.is_file():
                    try:
                        shutil.copy(candidate, global_config_file)
                        print(f"[配置初始化] 成功从 {candidate} 复制默认配置到 {global_config_file}")
                        copied = True
                        break
                    except Exception as e:
                        print(f"[配置初始化] 从 {candidate} 复制配置失败: {e}")

            if not copied:
                # 生成平台默认的 config.json
                from src.utils.platform_defaults import get_platform_ai_defaults
                try:
                    defaults = get_platform_ai_defaults() or {}
                    defaults["bot_auto_start"] = True
                    global_config_file.write_text(json.dumps(defaults, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"[配置初始化] 已生成默认平台配置: {global_config_file}")
                except Exception as e:
                    print(f"[配置初始化] 生成平台默认配置失败: {e}")

        # 2. 如果存在全局 config.json，但用户专属 config.json 不存在，进行继承拷贝
        if global_config_file.exists() and not user_config_file.exists() and user_config_file != global_config_file:
            try:
                shutil.copy(global_config_file, user_config_file)
                print(f"[配置初始化] 成功拷贝全局配置为用户专属配置: {user_config_file}")
            except Exception as e:
                print(f"[配置初始化] 拷贝专属配置失败: {e}")

_loading_thread_started = False
_loading_lock = threading.Lock()

def _load_configs() -> dict:
    ensure_config_initialized()
    from src.utils.config_cache import config_cache
    if not config_cache.is_loaded:
        global _loading_thread_started
        with _loading_lock:
            if not _loading_thread_started:
                _loading_thread_started = True
                print("[配置加载] ⚙️ 内存缓存为空，正在后台异步拉取云端最新配置...")
                threading.Thread(
                    target=lambda: config_cache.load_from_cloud(clear_before_load=False),
                    name="async-config-cloud-load",
                    daemon=True
                ).start()
    cached = config_cache.get("global_api_config")
    user_file = _get_user_config_file()
    old_global_file = CONFIG_DIR / "config.json"
    local_data = {}
    local_file_used = None
    for f in [user_file, old_global_file]:
        if f.exists():
            try:
                local_data = json.loads(f.read_text(encoding='utf-8'))
                local_file_used = f
                break
            except Exception:
                pass

    # 比对本地和云端的配置更新时间戳，选择最新版本的配置，防止本地空/默认配置覆盖云端真实配置
    if cached and isinstance(cached, dict):
        local_updated_at = local_data.get("updated_at", "")
        cloud_updated_at = config_cache.get_updated_at("global_api_config") or cached.get("updated_at", "")
        
        from src.utils.platform_defaults import is_user_config_custom
        cloud_is_custom = is_user_config_custom(cached)
        local_is_custom = is_user_config_custom(local_data)
        
        use_cloud = False
        if cloud_updated_at and (not local_updated_at or cloud_updated_at > local_updated_at):
            use_cloud = True
        elif not local_is_custom and cloud_updated_at != local_updated_at:
            # 如果本地尚未配置自定义参数，而云端已存在任何配置且时间戳不一致，则优先拉取并同步到本地
            use_cloud = True
            
        if use_cloud:
            print(f"[配置加载] ☁️ 优先使用云端配置，并同步写入本地 ({cloud_updated_at} vs {local_updated_at})")
            user_config = dict(cached)
            if cloud_updated_at:
                user_config["updated_at"] = cloud_updated_at
            # 立即写入本地物理文件做持久化兜底，解决新装用户不下载保存云端 Coze 配置的问题
            try:
                if local_file_used:
                    local_file_used.write_text(json.dumps(user_config, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as write_err:
                print(f"[配置加载] 警告：自动将云端配置同步到本地失败: {write_err}")
        else:
            # 否则合并（若本地非自定义且云端有值，则以云端优先；否则本地修改优先）
            if not local_is_custom and cached:
                user_config = cached
            else:
                user_config = {**cached, **local_data}
    elif local_data:
        user_config = local_data
    else:
        user_config = {}

    # 仅在内存返回时做深拷贝并合并默认参数，绝不改变缓存数据与写入本地磁盘
    result_config = dict(user_config)
    if not _has_valid_ai_config(result_config):
        from src.utils.platform_defaults import get_platform_ai_defaults
        defaults = get_platform_ai_defaults()
        if defaults:
            # 强制覆盖 AI 配置，确保平台默认托管通道成功拉起，避免使用损坏的局部配置
            result_config["external_api_settings"] = defaults.get("external_api_settings", {})
            result_config["_platform_managed"] = True
            if "agents" in defaults:
                result_config["agents"] = defaults["agents"]
            
            # 其他非 AI 配置参数，使用常规合并
            for key, val in defaults.items():
                if key not in ("external_api_settings", "_platform_managed", "agents"):
                    if key not in result_config or not result_config[key]:
                        result_config[key] = val

    if "operation_ripple_enabled" not in result_config:
        result_config["operation_ripple_enabled"] = True

    if "bot_auto_start" not in result_config:
        result_config["bot_auto_start"] = True

    _reload_customer_adapters(result_config)
    return result_config


def _save_configs(configs: dict):
    # 自动校正 _platform_managed 标记：如果已配置了有效的自定义 AI，则不能标记为平台托管
    if _has_valid_ai_config(configs):
        configs["_platform_managed"] = False
        
    from src.utils.config_cache import config_cache
    from datetime import datetime
    # 🛡️ 架构清理大闸：全局配置中绝对不允许保存或同步自动回复隔离名单与同步临时状态
    for k in ["auto_chat_friend_excludes", "auto_chat_group_excludes", "moment_interact_friend_excludes", "is_syncing"]:
        configs.pop(k, None)
        
    now_str = datetime.utcnow().isoformat() + "Z"
    configs["updated_at"] = now_str
    
    config_cache.set("global_api_config", configs)
    try:
        if not CONFIG_DIR.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config_file = _get_user_config_file()
        config_file.write_text(json.dumps(configs, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[配置保存] 警告：无法写入本地配置兜底文件: {e}")


@router.get("/api/config")
async def get_all_configs():
    configs = _load_configs()
    from src.utils.config_cache import config_cache
    configs["is_syncing"] = config_cache.sync_in_progress
    return configs


@router.get("/api/config/ai-source")
async def get_ai_source():
    from src.utils.platform_defaults import is_user_config_custom, has_platform_defaults
    configs = _load_configs()
    is_custom = is_user_config_custom(configs)
    platform_available = has_platform_defaults()
    return ok({
        "source": "custom" if is_custom else "platform",
        "platform_available": platform_available,
        "is_custom": is_custom,
    })


@router.post("/api/config/reset-to-platform")
async def reset_to_platform():
    from src.utils.config_cache import config_cache
    from src.utils.platform_defaults import is_user_config_custom, get_platform_ai_defaults, has_platform_defaults
    
    # 1. 强力从云端拉取最新配置到内存缓存
    print("[配置重置] 🔄 正在从云端拉取最新配置...")
    config_cache.load_from_cloud(clear_before_load=True)
    cached = config_cache.get("global_api_config")
    
    # 2. 如果云端配置存在且为自定义配置，我们以云端配置为准覆盖本地
    if cached and isinstance(cached, dict) and is_user_config_custom(cached):
        print("[配置重置] ☁️ 检测到云端存在有效的自定义 AI 配置，正在覆盖本地...")
        configs = cached
    else:
        # 3. 否则，如果云端没有自定义配置，我们生成硬编码的平台默认配置覆盖本地
        print("[配置重置] 🤖 云端未检测到自定义配置，正在恢复为平台托管默认通道...")
        if not has_platform_defaults():
            return err(40000, "平台默认 AI 通道未配置，无法恢复")
        configs = get_platform_ai_defaults() or {}
        configs["_platform_managed"] = True

    # 4. 强制写入本地物理文件并推送云端
    _save_configs(configs)
    
    # 5. 立即强制热重载 AI 服务
    _reload_ai_service(force=True)
    return ok_msg("已恢复为平台托管 AI 通道，并同步拉取云端配置")


@router.post("/api/config")
async def save_all_configs(request: Request):
    data = await request.json()
    configs = _load_configs()
    if isinstance(data, dict):
        for k, v in data.items():
            configs[k] = v
    _save_configs(configs)
    _reload_ai_service(force=True)
    _reload_friend_request_monitor(configs)
    _reload_customer_adapters(configs)
    return ok_msg("操作成功")


@router.get("/api/config/{config_type}")
async def get_config(config_type: str):
    return _load_configs().get(config_type, {})


@router.post("/api/config/{config_type}")
async def save_config(config_type: str, request: Request):
    data, configs = await request.json(), _load_configs()
    configs[config_type] = data[config_type] if (isinstance(data, dict) and config_type in data) else data
    _save_configs(configs)
    if config_type in ("ai_platform", "coze_settings", "dify_settings", "external_api_settings", "agents"):
        _reload_ai_service(force=True)
    _reload_friend_request_monitor(configs)
    _reload_customer_adapters(configs)
    return ok_msg("操作成功")


@router.get("/api/safety/moment-settings")
async def get_moment_settings_api():
    from src.crm.account_data import get_active_account
    from src.utils.moment_config import get_moment_settings
    return get_moment_settings(get_active_account())


@router.post("/api/safety/moment-settings")
async def save_moment_settings_api(request: Request):
    from src.crm.account_data import get_active_account
    from src.utils.moment_config import save_moment_settings
    save_moment_settings(await request.json(), get_active_account())
    return ok_msg("操作成功")


from .config_test_api import _reload_friend_request_monitor, _reload_ai_service, _reload_customer_adapters


@router.post("/api/config/work-hours")
async def save_work_hours_api(request: Request):
    """远程控制：同步并映射企业上下班时段到本地休息守卫"""
    data = await request.json()
    work_start = data.get("work_start", "09:00")
    work_end = data.get("work_end", "18:00")
    
    # 互补转换：工作时间 09:00-18:00，则休眠时间 18:00-09:00
    rest_config = {
        "enabled": True,
        "manual_suspend_minutes": 30,
        "ignore_reply_whitelist": False,
        "weekday_start": work_end,
        "weekday_end": work_start,
        "weekend_start": work_end,
        "weekend_end": work_start,
        "protected_actions": [
            "like", "comment", "moment_post", "auto_reply", "add_friend", "friend_request", "patrol"
        ]
    }
    
    from src.utils.rest_time import save_rest_config
    from src.crm.account_data import get_active_account
    save_rest_config(rest_config, get_active_account())
    return ok_msg("工作时段更新成功")
