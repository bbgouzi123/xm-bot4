import json
import logging
import threading
from typing import Any, Optional
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import URLError, HTTPError

from xm_py_server.runtime_urls import LOOPBACK_HOST, http_origin, prod_gateway_url
from .helpers import (
    scope_bot_wxid,
    detect_cloud_url,
    try_load_sso_token,
    try_load_sso_user_id,
    decode_token_sub,
    cache_to_local,
    load_from_cache,
    generate_dev_jwt_token,
    load_queue_from_disk,
)
from .auth_token_manager import CloudSyncAuthTokenMixin

logger = logging.getLogger(__name__)

# 🔧 [Bug 2a Fix] 预建无代理 Opener，用于本地模式（内网直连）下绕过 getproxies_registry()
# Windows 注册表代理查询（getproxies_registry）在某些系统状态下会永久阻塞后台线程，
# 本地回环地址不需要任何代理，使用空代理 handler 可完全跳过注册表读取。
_NO_PROXY_HANDLER = urllib_request.ProxyHandler({})
_NO_PROXY_OPENER = urllib_request.build_opener(_NO_PROXY_HANDLER)



def _scope_bot_wxid() -> str:
    return scope_bot_wxid()


class CloudSyncBaseMixin(CloudSyncAuthTokenMixin):
    """数据同步基础 Mixin（自适应本地 Rust 服务/线上同步后端）：包含单例逻辑、配置、SSO 绑定及 HTTP 基础方法"""


    # 生产环境线上网关地址（真正的远程同步后端）
    _PROD_CLOUD_URL = prod_gateway_url("/api/xm-bot4-cloud")
    # 本地开发时连接的本地 Rust 后端服务端口 (xm-bot4-cloud)
    _LOCAL_CLOUD_URL = http_origin(LOOPBACK_HOST, 42040)

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @staticmethod
    def _detect_cloud_url() -> str:
        return detect_cloud_url()

    def __init__(self, cloud_url: str = "", jwt_token: str = ""):
        if self._initialized:
            return

        # 同步后端地址：显式传入 > 环境自动检测
        self.cloud_url = cloud_url or self._detect_cloud_url()
        self.is_remote = self.cloud_url.startswith("https://")
        self.jwt_token = jwt_token

        # [用户隔离] 优先从 SSO 会话文件获取真实 access_token
        if not self.jwt_token:
            self.jwt_token = self._try_load_sso_token()

        # 兜底：SSO 无 token 时自签一个系统管理员令牌
        if not self.jwt_token:
            try:
                sso_user_id = self._try_load_sso_user_id()
                self.jwt_token = generate_dev_jwt_token(sso_user_id)
            except Exception as e:
                logger.warning(f"[同步服务] 免密授权盾颁发失败: {e}")

        self.enabled = True
        self._queue: list[dict] = []
        self._queue_lock = threading.RLock()
        self._queue_file = Path.home() / ".xm-ai-bot" / "cloud_sync" / "event_queue.json"
        self._sync_thread: Optional[threading.Thread] = None
        self._running = False
        self._load_queue_from_disk()
        self._initial_sync_done = False
        self._initialized = True
        # [无感续期] 防重入标志由 CloudSyncAuthTokenMixin 管理，此处初始化
        self._silent_refresh_pending = False
        env_label = "🌐 线上网关" if self.is_remote else "🏠 本地后端"
        logger.info(f"[同步服务] 初始化完成 → {env_label} {self.cloud_url}")


    @staticmethod
    def _try_load_sso_token() -> Optional[str]:
        return try_load_sso_token()

    @staticmethod
    def _try_load_sso_user_id() -> Optional[str]:
        return try_load_sso_user_id()

    def configure(self, cloud_url: str = "", jwt_token: str = ""):
        """动态更新配置"""
        if cloud_url:
            self.cloud_url = cloud_url
            self.is_remote = cloud_url.startswith("https://")
        if jwt_token:
            self.jwt_token = jwt_token

    def sync_token_from_sso(self) -> bool:
        """从 SSO 会话文件重新加载 token 并刷新配置缓存"""
        new_token = self._try_load_sso_token()
        if new_token and new_token != self.jwt_token:
            old_sub = self._decode_token_sub(self.jwt_token)
            self.jwt_token = new_token
            new_sub = self._decode_token_sub(new_token)
            logger.info(f"[同步服务] 🔄 Token 已热切换: {old_sub[:8] if old_sub else '?'}... → {new_sub[:8] if new_sub else '?'}...")
            
            # 重置企业权限被拒的限制标记，方便新用户重新探测企业接口
            self._enterprise_forbidden = False

            try:
                from src.utils.config_cache import config_cache
                config_cache.load_from_cloud()
                logger.info("[同步服务] ♻️ 配置缓存已随用户切换重新加载")
            except Exception as e:
                logger.warning(f"[同步服务] 配置缓存重载失败: {e}")

            try:
                from src.utils.contacts_cache import contacts_cache
                contacts_cache.load_from_cloud(force=True)
                logger.info("[同步服务] ♻️ 通讯录缓存已随用户切换重新加载")
            except Exception as e:
                logger.warning(f"[同步服务] 通讯录重载失败: {e}")

            # 登录/切换用户后，确保后台同步线程处于启动状态
            try:
                self.start_background_sync()
            except Exception as e:
                logger.warning(f"[同步服务] 自动启动后台同步线程失败: {e}")

            # [大厂时序优化] 此时 Token 已经确定就绪，安全激活排期日历与好友队列的网络同步
            try:
                from src.crm.moment_planner_service.bootstrap import bootstrap_schedules_lazy
                bootstrap_schedules_lazy()
            except Exception as e:
                logger.warning(f"[同步服务] 延迟激活排期日历失败: {e}")

            try:
                from src.friend.friend_queue.storage import bootstrap_friend_queue_lazy
                bootstrap_friend_queue_lazy()
            except Exception as e:
                logger.warning(f"[同步服务] 延迟激活好友队列失败: {e}")

            return True
        return False


    @staticmethod
    def _decode_token_sub(token: str) -> Optional[str]:
        return decode_token_sub(token)


    def _request(self, method: str, path: str, data: Any = None,
                 need_auth: bool = False, timeout: int = None, _is_retry: bool = False) -> Optional[Any]:
        """发送 HTTP 请求到同步后端"""
        self.last_status_code = None
        if need_auth and not self.jwt_token:
            logger.debug(f"[同步服务] 接口 [{path}] 需要认证，但未配置有效 JWT 令牌，已自动拦截请求。")
            self.last_status_code = 401
            return None

        if timeout is None:
            if "/contacts" in path:
                timeout = 30 if self.is_remote else 15
            else:
                timeout = 15 if self.is_remote else 10
        import urllib.parse
        quoted_path = urllib.parse.quote(path, safe="/:")
        url = f"{self.cloud_url}{quoted_path}"
        headers = {"Content-Type": "application/json"}
        if need_auth and self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"

        # [内网豁免] 本地直连模式下注入内网信任头，绕过 xm-bot-cloud 强制加密拦截
        # 远程 HTTPS 模式由加密中间件正常处理，不需要此头
        if not self.is_remote:
            import os
            proxy_secret = os.getenv("XM_INTERNAL_PROXY_SECRET", "xm-internal-2026")
            headers["X-XM-Internal-Proxy"] = proxy_secret

        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None

        ssl_context = None
        if self.is_remote:
            import ssl
            ssl_context = ssl.create_default_context()

        req = urllib_request.Request(url, data=body, headers=headers, method=method)

        try:
            # 🔧 [Bug 2a Fix] 本地回环模式使用预建无代理 Opener，跳过 getproxies_registry() 注册表读取
            # 远程 HTTPS 模式仍走标准 urlopen（含 SSL context）
            # 两条路径共享同一套 resp 解析逻辑，避免代码重复
            def _do_open():
                if not self.is_remote:
                    return _NO_PROXY_OPENER.open(req, timeout=timeout)
                return urllib_request.urlopen(req, timeout=timeout, context=ssl_context)

            with _do_open() as resp:
                self.last_status_code = getattr(resp, "status", 200)
                raw_bytes = resp.read()
                raw_text = raw_bytes.decode("utf-8")
                
                if not raw_text.strip():
                    logger.debug(f"[同步服务] 接口返回空 Payload [{path}]")
                    return None

                try:
                    result = json.loads(raw_text)
                except json.JSONDecodeError as jde:
                    logger.error(
                        f"[同步服务] 接口返回非 JSON 数据 [{path}]: "
                        f"解析错误={jde}, 内容前100字符={raw_text[:100]!r}"
                    )
                    return None

                # 兼容：部分 API 返回顶层 JSON 为 list 而非标准 {code, data} 包装
                if isinstance(result, list):
                    return result
                if isinstance(result, dict):
                    if "code" in result:
                        if result.get("code") == 20000:
                            return result.get("data") if "data" in result else result
                        else:
                            if result.get("code") == 40400:
                                logger.debug(f"[同步服务] 配置未找到: {result.get('msg')}")
                            else:
                                logger.warning(f"[同步服务] API 错误: {result.get('msg')}")
                            if result.get("code") == 40002 or "认证令牌" in str(result.get("msg", "")):
                                self.last_status_code = 401
                                if not _is_retry and self.try_refresh_token():
                                    return self._request(method, path, data, need_auth, timeout, _is_retry=True)
                                self._handle_unauthorized_token()
                            return None
                    else:
                        # 没有 code 包裹，说明是 raw 字典数据，直接返回整个字典
                        return result
                # 其他类型（如纯字符串）直接返回
                return result
        except HTTPError as he:
            self.last_status_code = he.code
            try:
                err_body = he.read().decode("utf-8")
            except Exception:
                err_body = ""
            if he.code in (403, 404):
                logger.debug(
                    f"[同步服务] 接口响应 HTTP 异常 [{path}]: "
                    f"status={he.code}, reason={he.reason}, body={err_body[:200]}"
                )
            else:
                logger.warning(
                    f"[同步服务] 接口响应 HTTP 异常 [{path}]: "
                    f"status={he.code}, reason={he.reason}, body={err_body[:200]}"
                )
            if he.code == 401 or (err_body and ("认证令牌" in err_body or "40002" in err_body or "Unauthorized" in err_body)):
                if not _is_retry and self.try_refresh_token():
                    return self._request(method, path, data, need_auth, timeout, _is_retry=True)
                self._handle_unauthorized_token()
            return None
        except URLError as e:
            self.last_status_code = -1
            logger.debug(f"[同步服务] 网络不可达 ({url}): {e}")
            return None
        except Exception as e:
            self.last_status_code = -2
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str or "refused" in err_str or "connection" in err_str:
                logger.debug(f"[同步服务] 连接或超时异常 [{path}] (已降级处理): {e}")
            else:
                logger.warning(f"[同步服务] 请求异常 [{path}]: {e}")
            return None

    def _get(self, path: str, need_auth: bool = False, timeout: int = None) -> Optional[Any]:
        return self._request("GET", path, need_auth=need_auth, timeout=timeout)

    def _post(self, path: str, data: Any, need_auth: bool = False, timeout: int = None) -> Optional[Any]:
        return self._request("POST", path, data=data, need_auth=need_auth, timeout=timeout)

    def _put(self, path: str, data: Any, need_auth: bool = False, timeout: int = None) -> Optional[Any]:
        return self._request("PUT", path, data=data, need_auth=need_auth, timeout=timeout)

    def _cache_to_local(self, filename: str, data: Any):
        """将同步后端数据缓存到本地文件"""
        cache_to_local(filename, data)

    def _load_from_cache(self, filename: str) -> Optional[Any]:
        """从本地缓存加载数据"""
        return load_from_cache(filename)

    def check_health(self) -> bool:
        """检查同步后端服务是否可达"""
        data = self._get("/health")
        if data and data.get("status") == "healthy":
            logger.info(f"[同步服务] 服务健康 ✅ (v{data.get('version', '?')})")
            return True
        return False

    def _load_queue_from_disk(self):
        """启动时从本地加载未上报事件，支持异常退出后的重放"""
        self._queue = load_queue_from_disk(self._queue_file)
