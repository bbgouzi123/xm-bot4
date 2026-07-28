"""
xm-bot4引擎：统一内存配置缓存层 — 替代 SQLite bot_config 表

架构（极简两层）：
    ☁️ 同步后端 PostgreSQL — 唯一持久化源
        ↕  启动时拉取 / 修改时推送
    🧠 Python 内存字典 — 运行时热缓存 (0.01ms 读取)

断网不考虑：产品基于微信自动化，断网微信都用不了，我们自然也不用跑。

使用方式：
    from src.utils.config_cache import config_cache
    
    # 读取配置（永远从内存读）
    value = config_cache.get("global_api_config", default={})
    
    # 写入配置（写内存 + 异步推同步后端）
    config_cache.set("rest_time_settings", {...})
    
    # 启动时初始化（从同步后端拉取填充内存）
    config_cache.load_from_cloud()
"""

import json
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ConfigCache:
    """
    全局统一内存配置缓存 — 单例模式
    
    替代所有对 AccountDatabaseManager bot_config 表的直接读写。
    所有配置数据统一在内存中维护，同步后端为唯一持久化源。
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._cache: Dict[str, Dict[str, Any]] = {}  # {user_id: {key: value}}
        self._updated_at: Dict[str, Dict[str, str]] = {}  # {user_id: {key: updated_at_str}}
        self._rw_lock = threading.RLock()
        self._last_token = None
        self._last_user_id = "default"
        self.is_loaded = False
        self.sync_in_progress = False
        logger.info("[ConfigCache] 内存配置缓存已初始化")

    # ==================== 核心读写 ====================

    def _get_active_user_id(self) -> str:
        """获取当前活跃的 SaaS 用户 ID，带内存缓存以消除重复解码开销"""
        try:
            from src.utils.cloud_sync import get_cloud_client
            client = get_cloud_client()
            token = client.jwt_token if client else None
            if token:
                if getattr(self, "_last_token", None) == token:
                    return self._last_user_id
                
                user_id = client._decode_token_sub(token)
                if user_id:
                    self._last_token = token
                    self._last_user_id = user_id
                    return user_id
        except Exception:
            pass
        return "default"

    def get(self, key: str, default: Any = None) -> Any:
        """从内存读取配置（0.01ms，无 I/O）"""
        user_id = self._get_active_user_id()
        with self._rw_lock:
            user_cache = self._cache.setdefault(user_id, {})
            return user_cache.get(key, default)

    def get_updated_at(self, key: str) -> Optional[str]:
        """获取云端最后修改的时间戳字符串 (UTC 格式)"""
        user_id = self._get_active_user_id()
        with self._rw_lock:
            user_updated = self._updated_at.setdefault(user_id, {})
            return user_updated.get(key)

    def set(self, key: str, value: Any, sync_cloud: bool = True):
        """写入配置 → 内存 + 异步推同步后端

        Args:
            key: 配置键名
            value: 配置值（dict/list/str/int 等可 JSON 序列化的值）
            sync_cloud: 是否异步推送到同步后端（默认 True）
        """
        from datetime import datetime
        user_id = self._get_active_user_id()
        with self._rw_lock:
            user_cache = self._cache.setdefault(user_id, {})
            user_updated = self._updated_at.setdefault(user_id, {})
            user_cache[key] = value
            # 手动更新本地内存的修改时间戳为最新 UTC 时间
            user_updated[key] = datetime.utcnow().isoformat() + "Z"

        if sync_cloud:
            self._async_push_to_cloud(key, value)

    def delete(self, key: str):
        """删除指定配置"""
        user_id = self._get_active_user_id()
        with self._rw_lock:
            user_cache = self._cache.setdefault(user_id, {})
            user_updated = self._updated_at.setdefault(user_id, {})
            user_cache.pop(key, None)
            user_updated.pop(key, None)

    def get_all(self) -> Dict[str, Any]:
        """获取当前活跃用户全部缓存的配置快照"""
        user_id = self._get_active_user_id()
        with self._rw_lock:
            user_cache = self._cache.setdefault(user_id, {})
            return user_cache.copy()

    # ==================== 启动加载 ====================

    def load_from_cloud(self, clear_before_load: bool = True) -> bool:
        """从同步后端拉取当前用户设置到内存
 
        Args:
            clear_before_load: 加载前是否清空现有缓存（用户切换时必须清空，
                                防止 A 用户的 AI Key 残留给 B 用户使用）
 
        Returns:
            True = 同步后端加载成功
        """
        self.sync_in_progress = True
        try:
            user_id = self._get_active_user_id()
            if not clear_before_load and self.is_loaded:
                return True
            from src.utils.cloud_sync import get_cloud_client
            cloud = get_cloud_client()
            settings = cloud.pull_settings()
 
            if isinstance(settings, list):
                with self._rw_lock:
                    if clear_before_load:
                        self._cache[user_id] = {}
                        self._updated_at[user_id] = {}
                        logger.info(f"[ConfigCache] ♻️ 清空本地内存配置缓存 (用户: {user_id})")
                    
                    user_cache = self._cache.setdefault(user_id, {})
                    user_updated = self._updated_at.setdefault(user_id, {})
                    for item in settings:
                        key = item.get("setting_key", "")
                        val = item.get("setting_val")
                        up_at = item.get("updated_at")
                        if key and val is not None:
                            user_cache[key] = val
                            if up_at:
                                user_updated[key] = str(up_at)
 
                self.is_loaded = True
                logger.info(f"[ConfigCache] ☁️ 成功从同步后端加载 {len(settings)} 项配置到内存 (用户: {user_id})")
                
                # 🌟 [冷启动同步 & AI 服务热重载]
                # 加载成功后，强制触发本地配置合并持久化与 AI 服务热加载，杜绝冷启动及账号切换配置不同步的顽疾
                try:
                    from src.api.config_api.base_config import _load_configs
                    from src.api.config_api.config_test_api import _reload_ai_service
                    # _load_configs() 内置比对逻辑，发现云端更新则会自动合并写入本地磁盘
                    configs = _load_configs()
                    _reload_ai_service(force=True)
                except Exception as reload_err:
                    logger.debug(f"[ConfigCache] 自动重载 AI 服务异常（可能尚未完全启动）: {reload_err}")
                
                return True
 
        except Exception as e:
            logger.warning(f"[ConfigCache] ☁️ 同步后端配置拉取失败: {e}")
        finally:
            self.sync_in_progress = False
 
        return False
    # ==================== 同步后端推送（含重试） ====================

    # 最大重试次数与退避基数（秒）
    _MAX_RETRIES = 3
    _RETRY_BACKOFF_BASE = 2  # 2s, 4s, 8s

    def _async_push_to_cloud(self, key: str, value: Any):
        """异步推送单个配置到同步后端（含重试与失败通知）

        重试策略：最多 3 次，指数退避（2s → 4s → 8s）。
        全部失败后通过 WebSocket 通知前端，让用户知道配置未同步到云端。
        """
        import copy
        safe_value = copy.deepcopy(value) if isinstance(value, (dict, list)) else value

        def _do():
            import time as _time
            last_err = None
            for attempt in range(1, self._MAX_RETRIES + 1):
                try:
                    from src.utils.cloud_sync import get_cloud_client
                    get_cloud_client().save_setting(key, safe_value)
                    if attempt > 1:
                        logger.info(f"[ConfigCache] ☁️ 第 {attempt} 次重试推送成功 ({key})")
                    return  # 推送成功，结束
                except Exception as e:
                    last_err = e
                    if attempt < self._MAX_RETRIES:
                        wait = self._RETRY_BACKOFF_BASE ** attempt
                        logger.warning(
                            f"[ConfigCache] ☁️ 推送失败 ({key})，{wait}s 后第 {attempt + 1} 次重试: {e}"
                        )
                        _time.sleep(wait)

            # 重试全部耗尽 — 记录 warning 并通知前端
            logger.warning(f"[ConfigCache] ☁️ 推送最终失败 ({key})，{self._MAX_RETRIES} 次重试均失败: {last_err}")
            self._notify_push_failure(key)

        threading.Thread(target=_do, daemon=True, name=f"cloud-push-{key[:12]}").start()

    @staticmethod
    def _notify_push_failure(key: str):
        """通过 WebSocket 通知前端配置推送失败，让用户感知到云端同步异常"""
        try:
            from src.utils.websocket_manager import ws_manager
            import asyncio
            payload = {
                "type": "config_sync_warning",
                "data": {
                    "message": "配置已保存到本地，但未能同步到云端。换电脑前请确保网络正常并重新保存一次。",
                    "key": key,
                },
            }
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), loop)
            else:
                loop.run_until_complete(ws_manager.broadcast(payload))
        except Exception:
            pass  # WebSocket 通知是尽力而为，不阻塞主流程


# ===== 全局单例 =====
config_cache = ConfigCache()
"""Module-level singleton for immediate import usage.

Usage:
    from src.utils.config_cache import config_cache
    val = config_cache.get("global_api_config", {})
"""

