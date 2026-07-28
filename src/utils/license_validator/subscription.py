"""
订阅制 (V2/V3) 校验与微信号绑定模块

设计原则：
  - 每次调用 check_subscription() 都直接请求 xm-user 服务端，拿到的就是权威最新数据
  - 不做内存缓存，避免用户已付费升级但本地还显示试用版的问题
  - 仅保留本地文件缓存（save_license），作为断网时的离线降级兜底
"""
import logging
import time
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from .storage import StorageMixin, CONFIG_DIR
from .network import NetworkMixin
from .helpers import get_sso_user_id, get_current_wechat_id
from .offline import check_offline_activation

logger = logging.getLogger(__name__)

class SubscriptionMixin(StorageMixin, NetworkMixin):
    # 内存级短期缓存：避免 Dashboard 并发刷新时重复发起慢速同步 HTTP
    # 30 秒过期 — 用户付费后最多等 30 秒生效，完全可接受
    _sub_cache: Dict[str, Any] = {}
    _sub_cache_ts: float = 0
    _SUB_CACHE_TTL = 30  # 秒
    _OFFLINE_GRACE_HOURS = 72  # 离线宽限时间（小时）
    _background_refresh_running: bool = False  # 防止并发后台刷新线程洪泛

    @classmethod
    def clear_subscription_cache(cls):
        cls._sub_cache.clear()
        cls._sub_cache_ts = 0
        logger.info("[订阅] 订阅状态内存缓存已清空")

    @classmethod
    def check_subscription(cls, force: bool = False, from_background: bool = False) -> Dict[str, Any]:
        """
        V3 服务端权威制校验 — 短期内存缓存 (30s) + 服务端实时查询
        force=True 可跳过缓存（如用户刚完成支付后手动刷新）
        优先尝试本地离线激活码校验 (Bypass Server)
        """
        # 1. 优先尝试本地离线激活码校验
        offline_result = check_offline_activation()
        if offline_result:
            cls._sub_cache = offline_result
            cls._sub_cache_ts = time.time()
            return offline_result

        # 2. 缓存命中：30 秒内直接返回上次结果
        if not force and cls._sub_cache and (time.time() - cls._sub_cache_ts) < cls._SUB_CACHE_TTL:
            return cls._sub_cache.copy()

        # 2.5 冷启动无缓存时，优先从本地物理文件加载上一次缓存，写入内存，防止冷启动时同步发送 HTTP 阻塞
        if not cls._sub_cache:
            user_id = cls._get_sso_user_id()
            fallback_res = cls._offline_fallback(user_id)
            trial_info = cls._get_trial_info(user_id)
            try:
                first_dt = datetime.fromisoformat(trial_info["first_launch"])
            except:
                first_dt = datetime.now()
            local_ends_at = (first_dt + timedelta(days=trial_info.get("trial_days", 3))).isoformat()
            if not fallback_res.get("trial_starts_at"):
                fallback_res["trial_starts_at"] = trial_info["first_launch"]
            if not fallback_res.get("trial_ends_at"):
                fallback_res["trial_ends_at"] = local_ends_at
            
            cls._sub_cache = fallback_res
            cls._sub_cache_ts = time.time()

        # 3. 避免 API 线程同步阻塞：如果已有缓存值，API 线程直接返回，后台异步刷新
        # 【修复】增加 _background_refresh_running 互斥标志，防止订阅 TTL 过期时
        # 多个并发 API 请求各自 spawn 独立刷新线程，导致 N 条「服务端连接失败」同时打出
        if not from_background and cls._sub_cache:
            if not cls._background_refresh_running:
                cls._background_refresh_running = True
                def _bg_refresh():
                    try:
                        cls.check_subscription(force=force, from_background=True)
                    finally:
                        cls._background_refresh_running = False
                import threading
                threading.Thread(
                    target=_bg_refresh,
                    name="async-subscription-update",
                    daemon=True
                ).start()
            return cls._sub_cache.copy()

        user_id = cls._get_sso_user_id()
        trial_info = cls._get_trial_info(user_id)
        
        try:
            first_dt = datetime.fromisoformat(trial_info["first_launch"])
        except:
            first_dt = datetime.now()
        local_ends_at = (first_dt + timedelta(days=trial_info.get("trial_days", 3))).isoformat()

        if not user_id:
            logger.debug("[订阅] 未登录，进入试用版逻辑")
            result = {
                "valid": False,
                "status": "trial_expired" if trial_info.get("trial_expired") else "trial",
                "mode": "subscription",
                "message": "请先登录后再使用",
                "trial_starts_at": trial_info["first_launch"],
                "trial_ends_at": local_ends_at,
                "days_remaining": trial_info.get("trial_remaining", 0),
                "plan_name": "试用版"
            }
            cls._sub_cache = result
            cls._sub_cache_ts = time.time()
            return result
        
        # 直接请求服务端
        logger.debug(f"[订阅] 正在为用户 {user_id} 请求服务端最新状态...")
        server_result = cls._query_server(user_id)
        
        if server_result:
            logger.info(f"[订阅] 服务端请求成功: plan={server_result.get('plan_name')}, status={server_result.get('status')}")
            if not server_result.get("trial_starts_at"): server_result["trial_starts_at"] = trial_info["first_launch"]
            if not server_result.get("trial_ends_at"): server_result["trial_ends_at"] = local_ends_at
            
            # 如果服务端返回的是试用过期，但本地试用还没过期（极少见），以服务端为准
            # 但如果服务端完全查不到（比如没初始化），则回退到本地试用逻辑
            if server_result.get("status") in ("error", "unbound", "invalid"):
                server_result["status"] = "trial_expired" if trial_info.get("trial_expired") else "trial"
                server_result["plan_name"] = "试用版"
                server_result["days_remaining"] = trial_info.get("trial_remaining", 0)

            # ☁️ 触发全量强制云端配置同步：如果当前是旗舰版，且之前不是旗舰版，或者处于 force 强制刷新状态下
            new_is_flagship = server_result.get("status") in ("active", "trial") and server_result.get("plan_code") == "flagship"
            old_was_flagship = False
            if cls._sub_cache:
                old_was_flagship = cls._sub_cache.get("status") in ("active", "trial") and cls._sub_cache.get("plan_code") == "flagship"
            
            if new_is_flagship and (force or not old_was_flagship):
                logger.info("[订阅] 检测到开通/续费旗舰版成功，强制刷新云端配置缓存...")
                try:
                    from src.utils.config_cache import config_cache
                    config_cache.load_from_cloud(clear_before_load=True)
                except Exception as sync_err:
                    logger.warning(f"[订阅] 自动同步云端配置失败: {sync_err}")

            cls._sub_cache = server_result
            cls._sub_cache_ts = time.time()
            return server_result
        
        # 服务端不可达时，才降级到本地文件缓存（离线兜底）
        logger.warning(f"[订阅] 服务端连接失败，降级至本地文件缓存 (User: {user_id})")
        offline_result = cls._offline_fallback(user_id)
        if not offline_result.get("trial_starts_at"): offline_result["trial_starts_at"] = trial_info["first_launch"]
        if not offline_result.get("trial_ends_at"): offline_result["trial_ends_at"] = local_ends_at
        if offline_result.get("status") in ("error", "unbound", "invalid", "offline_no_cache", "offline_expired"):
            offline_result["status"] = "trial_expired" if trial_info.get("trial_expired") else "trial"
            offline_result["plan_name"] = "试用版"
            offline_result["days_remaining"] = trial_info.get("trial_remaining", 0)
        cls._sub_cache = offline_result
        cls._sub_cache_ts = time.time()
        return offline_result

    @classmethod
    def _query_server(cls, user_id: str) -> Optional[Dict[str, Any]]:
        """向服务端查询订阅状态（每次实时请求，不缓存）"""
        from src.utils.const import PRODUCT_KEY
        from .machine import MachineMixin

        # 获取本机机器码，上报给服务端用于设备授权校验
        machine_code = MachineMixin.get_machine_code()
        logger.debug(f"[订阅] 本机机器码: {machine_code}")

        # 1. 尝试按当前 SSO 用户 ID 查询（携带机器码）
        result = cls._http_request("POST", "/api/subscription/_query", {
            "user_id": user_id,
            "product": PRODUCT_KEY,
            "machine_code": machine_code,
        })

        # 2. 如果 SSO 账号下没有有效订阅（非 active），或者为试用版，且当前已接管微信，尝试按微信号回查
        if result and result.get("success") is True:
            data = result.get("data", {})
            sub_status = data.get("status", "")
            is_valid = sub_status in ("active", "trial")
            if not is_valid or sub_status == "trial":
                wechat_id = cls._get_current_wechat_id()
                if wechat_id:
                    logger.debug(f"[订阅] SSO 账号订阅状态为 {sub_status}，尝试按接管微信号 {wechat_id} 回查...")
                    wechat_result = cls._http_request("POST", "/api/subscription/_query", {
                        "wechat_id": wechat_id,
                        "product": PRODUCT_KEY,
                        "machine_code": machine_code,
                    })
                    if wechat_result and wechat_result.get("success") is True:
                        wechat_data = wechat_result.get("data", {})
                        wechat_status = wechat_data.get("status", "")
                        # 如果微信号下有有效订阅，且优于当前 SSO 账号订阅
                        if wechat_status in ("active", "trial"):
                            if not is_valid or (sub_status == "trial" and wechat_status == "active"):
                                logger.info(f"[订阅] 成功通过微信号检索到更优有效订阅: {wechat_data.get('plan_name')}")
                                result = wechat_result

        if result and result.get("success") is True:
            parsed = cls._parse_server_response(result.get("data", {}), user_id)
            
            # 读取本地缓存以保留已发送标志
            cached_license = cls.load_license()
            
            # 拿到最新数据后，立即同步更新本地文件，用于离线兜底
            cls.save_license({
                "user_id": user_id,
                "plan_code": parsed.get("plan_code"),
                "plan_name": parsed.get("plan_name"),
                "status": parsed.get("status"),
                "expires_at": parsed.get("expires_at", ""),
                "days_remaining": parsed.get("days_remaining", 0),
                "max_industries": parsed.get("max_industries", 1),
                "last_check": datetime.now().isoformat(),
                "mode": "subscription",
                "features": parsed.get("features", {}),
                "machine_code": machine_code,
                "gift_seats_count": parsed.get("gift_seats_count", 0),
                "trial_bonus_sent": parsed.get("trial_bonus_sent", cached_license.get("trial_bonus_sent", False)),
                "base_bonus_sent": parsed.get("base_bonus_sent", cached_license.get("base_bonus_sent", False)),
                "professional_bonus_sent": parsed.get("professional_bonus_sent", cached_license.get("professional_bonus_sent", False)),
                "flagship_bonus_sent": parsed.get("flagship_bonus_sent", cached_license.get("flagship_bonus_sent", False)),
            })
            return parsed
        elif result and result.get("success") is False:
            msg = result.get("message", "查询失败")
            # 设备超限错误：给用户清晰的中文提示
            if "设备" in msg or "device" in msg.lower() or "machine" in msg.lower():
                logger.warning(f"[订阅] ⚠️ 设备授权校验失败: {msg}")
                return {
                    "valid": False,
                    "status": "device_limit_exceeded",
                    "mode": "subscription",
                    "user_id": user_id,
                    "message": msg,
                    "machine_code": machine_code,
                }
            logger.warning(f"[订阅] 服务端返回业务错误: {msg}")
            return {"valid": False, "status": "error", "mode": "subscription", "user_id": user_id, "message": msg}

        return None

    @classmethod
    def _parse_server_response(cls, data: dict, user_id: str) -> Dict[str, Any]:
        """解析服务端响应 (已委派至 helpers 模块以满足 300 行文件上限限制)"""
        from .helpers import parse_subscription_server_response
        return parse_subscription_server_response(
            data=data,
            user_id=user_id,
            cached_license=cls.load_license(),
            current_wechat_id=cls._get_current_wechat_id()
        )

    @classmethod
    def _offline_fallback(cls, user_id: str) -> Dict[str, Any]:
        """离线降级逻辑"""
        cached = cls.load_license()
        if cached.get("mode") != "subscription" or str(cached.get("user_id", "")) != str(user_id):
            return {"valid": False, "status": "offline_no_cache", "mode": "subscription", "user_id": user_id, "message": "首次使用需要联网验证订阅状态"}
        
        last_check = cached.get("last_check", "")
        if last_check:
            try:
                last_dt = datetime.fromisoformat(last_check)
                hours_offline = (datetime.now() - last_dt).total_seconds() / 3600
                if hours_offline > cls._OFFLINE_GRACE_HOURS:
                    return {
                        "valid": False, "status": "offline_expired", "mode": "subscription", "user_id": user_id,
                        "plan_code": cached.get("plan_code", "trial"), "plan_name": cached.get("plan_name", "试用版"),
                        "message": f"离线超过{cls._OFFLINE_GRACE_HOURS}小时，请连接网络重新验证", "offline": True,
                    }
            except: pass
        
        is_valid = cached.get("status") in ("active", "trial")
        offline_result = {
            "valid": is_valid, "status": cached.get("status", "unknown"), "mode": "subscription", "user_id": user_id,
            "plan_code": cached.get("plan_code", "trial"), "plan_name": cached.get("plan_name", "试用版"),
            "expires_at": cached.get("expires_at", ""), "days_remaining": cached.get("days_remaining", 0),
            "max_industries": cached.get("max_industries", -1 if cached.get("plan_code") == "flagship" else 1),
            "gift_seats_count": cached.get("gift_seats_count", 0),
            "message": "订阅正常（离线模式）" if is_valid else "无法验证订阅（离线）", "offline": True,
        }
        return offline_result

    @staticmethod
    def _get_sso_user_id() -> Optional[str]:
        """从 SSO 共享文件获取当前登录用户的 ID"""
        return get_sso_user_id()

    @staticmethod
    def _get_current_wechat_id() -> Optional[str]:
        """获取当前接管的微信号 ID (wxid)"""
        return get_current_wechat_id()

    @classmethod
    def bind_wechat(cls, wechat_id: str) -> Dict[str, Any]:
        """绑定微信号到当前用户的订阅"""
        user_id = get_sso_user_id()
        if not user_id: return {"success": False, "message": "未登录"}
        if not wechat_id: return {"success": False, "message": "微信 ID 为空"}
        
        result = cls._http_request("POST", "/api/subscription/bind-wechat", {"user_id": user_id, "wechat_id": wechat_id})
        if result and result.get("success") is True:
            cls.clear_subscription_cache()
            try:
                bot_config = CONFIG_DIR / "bot_config.json"
                data = json.loads(bot_config.read_text(encoding='utf-8')) if bot_config.exists() else {}
                data["wechat_id"] = wechat_id
                bot_config.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            except: pass
            return {"success": True, "data": result.get("data", {})}
        return {"success": False, "message": result.get("message", "绑定失败") if result else "网络不可达"}
