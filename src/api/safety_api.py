"""
防封策略与日配额统计 API
"""
import logging
from fastapi import APIRouter, Request
from src.utils.response import ok, err, ok_msg

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/safety", tags=["safety"])

@router.get("/moment-settings")
async def get_moment_settings_api():
    """获取朋友圈互动配置"""
    from src.utils.moment_config import get_moment_settings
    from src.crm.account_data import get_active_account
    settings = get_moment_settings(get_active_account())
    return ok(settings)

@router.post("/moment-settings")
async def save_moment_settings_api(request: Request):
    """保存朋友圈互动配置"""
    from src.utils.moment_config import save_moment_settings
    from src.crm.account_data import get_active_account
    data = await request.json()
    save_moment_settings(data, get_active_account())
    return ok_msg("操作成功")

@router.get("/rest-time")
async def get_rest_time_api():
    """获取休息时间配置"""
    from src.utils.rest_time import get_rest_config, check_nominal_rest_time
    from src.crm.account_data import get_active_account
    config = get_rest_config(get_active_account())
    config["currently_resting"] = check_nominal_rest_time(account_id=get_active_account())
    
    try:
        from src.utils.config_cache import config_cache
        config["force_awake"] = config_cache.get("force_awake_override", False)
    except:
        config["force_awake"] = False
        
    return ok(config)

@router.post("/force-wake")
async def force_wake_api(request: Request):
    """强制唤醒/恢复休眠"""
    from src.utils.config_cache import config_cache
    data = await request.json()
    action = data.get("action", "wake")
    config_cache.set("force_awake_override", action == "wake")
    
    if action == "wake":
        _log.warning("[强制唤醒] 用户已手动触发强制唤醒，休息时间限制被暂时绕过")
    else:
        _log.info("[强制唤醒] 用户已手动恢复自然休息状态")
        
    return ok_msg("操作成功")

@router.post("/rest-time")
async def save_rest_time_api(request: Request):
    """保存休息时间配置"""
    from src.utils.rest_time import save_rest_config
    from src.crm.account_data import get_active_account
    data = await request.json()
    save_rest_config(data, get_active_account())
    return ok_msg("操作成功")

@router.get("/daily-stats")
def get_daily_stats_api():
    from src.crm.account_data import get_active_account
    from src.utils.cloud_sync import get_cloud_client
    import json
    import datetime

    account_id = get_active_account()
    cloud = get_cloud_client()
    
    # 初始化各指标上限
    limits = {
        "like": 150,
        "comment": 80,
        "moment_post": 10,
        "add_friend": 50,
        "auto_reply": 500,
        "group_message": 200,
    }
    
    # 1. 尝试从用户朋友圈策略中拉取点赞与评论上限
    try:
        from src.utils.moment_config import get_moment_settings
        ms = get_moment_settings(account_id)
        if "daily_like_limit" in ms:
            limits["like"] = int(ms["daily_like_limit"])
        if "daily_comment_limit" in ms:
            limits["comment"] = int(ms["daily_comment_limit"])
    except:
        pass

    # 2. 尝试从 license 里拉取自动回复的上限
    try:
        from src.utils.license_validator import LicenseValidator
        features = LicenseValidator.check_features()
        limits["auto_reply"] = features.get("ai_daily_limit", 30)
    except:
        pass

    # 3. 直接通过拉取云端事件日志列表来做最权威的今日用量统计
    counts = {dim: 0 for dim in limits.keys()}
    org_counts = {dim: 0 for dim in limits.keys()}
    
    try:
        # 直接拉取最近的 1000 条事件日志
        result = cloud._get("/api/v1/events?limit=1000", need_auth=True)
        if result and isinstance(result, dict) and "data" in result:
            result = result["data"]
            
        if result and isinstance(result, list):
            _today = datetime.datetime.now().strftime("%Y-%m-%d")
            
            # 反向提取映射
            reverse_map = {
                'like': 'like',
                'comment': 'comment',
                'moment_post': 'moment_post',
                'friend_request': 'add_friend',
                'chat_log': 'auto_reply',
                'group_message': 'group_message'
            }
            
            def _is_today_event(row):
                ed = row.get("event_data", {})
                if isinstance(ed, str):
                    try:
                        ed = json.loads(ed)
                    except:
                        ed = {}
                elif not isinstance(ed, dict):
                    ed = {}
                
                # 获取事件产生时间
                ts = ed.get("created_at") or row.get("created_at") or row.get("createdAt") or row.get("timestamp") or ""
                ts_str = str(ts).replace("T", " ")[:10]
                return ts_str == _today

            def _matches_account(row):
                ed = row.get("event_data", {})
                if isinstance(ed, str):
                    try:
                        ed = json.loads(ed)
                    except:
                        ed = {}
                elif not isinstance(ed, dict):
                    ed = {}
                return (ed.get("account_id") or "").strip() == account_id

            for r in result:
                if not _is_today_event(r):
                    continue
                et = r.get("event_type")
                dim = reverse_map.get(et)
                if dim:
                    if dim in org_counts:
                        org_counts[dim] += 1
                    if _matches_account(r):
                        if dim in counts:
                            counts[dim] += 1
    except Exception as e:
        import traceback
        _log.warning(f"[统计服务] 实时同步后端统计异常，降级处理: {e}\n{traceback.format_exc()}")
        # 降级读本地 DailyCounter
        try:
            from src.utils.daily_counter import DailyCounter
            counter = DailyCounter()
            for dim in limits.keys():
                counts[dim] = counter.get_count(dim, account_id)
                org_counts[dim] = counts[dim]
        except:
            pass

    # 4. 构建并组装前端所需的 stats 结构
    stats = {}
    for dim, limit in limits.items():
        count = counts[dim]
        org_count = org_counts[dim]
        remaining = max(0, limit - count) if limit != -1 else -1
        percentage = round(count / limit * 100) if limit > 0 else 0
        
        org_remaining = max(0, limit - org_count) if limit != -1 else -1
        org_percentage = round(org_count / limit * 100) if limit > 0 else 0
        
        stats[dim] = {
            "count": count,
            "limit": limit,
            "remaining": remaining,
            "percentage": min(100, percentage),
            "org_count": org_count,
            "org_remaining": org_remaining,
            "org_percentage": min(100, org_percentage),
        }

    # 5. 补齐特殊展示的指标
    stats["total_tokens"] = {"count": 0, "limit": -1, "remaining": -1, "percentage": 0}
    try:
        from src.utils.daily_counter import DailyCounter
        violation_count = DailyCounter().get_count("violation", account_id)
    except:
        violation_count = 0
    stats["violation"] = {
        "count": violation_count,
        "limit": -1,
        "remaining": -1,
        "percentage": 0
    }

    # 6. 将当前确定的 counts 同步到本地 DailyCounter 内存中，
    # 避免程序在运行 can_do 检查时因为内存计数被重启清空而判断偏低
    try:
        from src.utils.daily_counter import DailyCounter
        counter = DailyCounter()
        for dim, count in counts.items():
            current_mem = counter.get_count(dim, account_id)
            if count > current_mem:
                counter.increment(dim, account_id, count - current_mem)
    except:
        pass

    return ok(stats)


@router.post("/daily-limits")
async def update_daily_limits_api(request: Request):
    from src.utils.daily_counter import DailyCounter
    data = await request.json()
    DailyCounter().update_limits(data)
    return ok_msg("操作成功")

@router.post("/pause-all")
async def pause_all_api():
    """远程控制：挂起本地自动化任务"""
    from src.utils.stop_signal import stop_signal
    stop_signal.request_stop("企业管控平台远程下发暂停指令")
    return ok_msg("暂停指令已在本地执行")

@router.post("/resume-all")
async def resume_all_api():
    """远程控制：恢复本地自动化任务"""
    from src.utils.stop_signal import stop_signal
    stop_signal.reset()
    return ok_msg("恢复指令已在本地执行")

@router.post("/blacklist/sync")
async def blacklist_sync_api(request: Request):
    """远程控制：同步企业黑名单"""
    from src.utils.config_cache import config_cache
    data = await request.json()
    blacklist = data.get("blacklist", [])
    config_cache.set("blacklist", blacklist, sync_cloud=False)
    _log.info(f"[企业管控] 全局企业黑名单已同步: {len(blacklist)} 个")
    return ok_msg("黑名单同步成功")

@router.post("/forbidden-words/sync")
async def forbidden_words_sync_api(request: Request):
    """远程控制：同步企业敏感违禁词"""
    from src.utils.config_cache import config_cache
    data = await request.json()
    forbidden_words = data.get("forbidden_words", [])
    config_cache.set("forbidden_words", forbidden_words, sync_cloud=False)
    _log.info(f"[企业管控] 企业全局敏感违禁词已同步: {len(forbidden_words)} 个")
    return ok_msg("违禁词同步成功")

@router.get("/speed-mode")
async def get_speed_mode_api():
    """获取极速托管模式设置"""
    from src.utils.config_cache import config_cache
    enabled = config_cache.get("speed_mode", False)
    lock_input = config_cache.get("speed_mode_lock_input", False)
    return ok({"enabled": enabled, "lock_input": lock_input})

@router.post("/speed-mode")
async def save_speed_mode_api(request: Request):
    """设置极速托管模式"""
    from src.utils.config_cache import config_cache
    import ctypes
    data = await request.json()
    enabled = data.get("enabled", False)
    lock_input = data.get("lock_input", False)
    
    config_cache.set("speed_mode", enabled)
    config_cache.set("speed_mode_lock_input", lock_input)
    
    # 应用 BlockInput
    if enabled and lock_input:
        try:
            # BlockInput 需要管理员身份运行才会真正锁定输入，但接口始终支持调用
            res = ctypes.windll.user32.BlockInput(True)
            _log.info(f"[极速模式] 已启用极速托管，全局锁定物理键鼠输入: {'成功' if res else '失败(可能无管理员权限)'}")
        except Exception as e:
            _log.warning(f"[极速模式] 锁定键鼠输入异常: {e}")
    else:
        try:
            ctypes.windll.user32.BlockInput(False)
            _log.info("[极速模式] 已解除物理键鼠输入控制")
        except Exception as e:
            pass
            
    return ok({"enabled": enabled, "lock_input": lock_input})
