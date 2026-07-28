"""
CloudSync Token 管理 Mixin

将 try_refresh_token 和 _handle_unauthorized_token 从 base.py 中分离，
维持 base.py 在 300 行限额内，同时保持单一职责：
- try_refresh_token: 使用 refresh_token 静默刷新 access_token
- _handle_unauthorized_token: 401 处理流程（热重载 → 前端无感续期 → 广播退出）
"""
import logging
import threading
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class CloudSyncAuthTokenMixin:
    """Token 刷新与失效处理 Mixin，需与 CloudSyncBaseMixin 组合使用"""

    # 子类必须提供的属性（由 CloudSyncBaseMixin.__init__ 初始化）
    jwt_token: str
    _silent_refresh_pending: bool

    @staticmethod
    def _try_load_sso_token() -> Optional[str]:
        from .helpers import try_load_sso_token
        return try_load_sso_token()

    @staticmethod
    def _decode_token_sub(token: str) -> Optional[str]:
        from .helpers import decode_token_sub
        return decode_token_sub(token)

    def try_refresh_token(self) -> bool:
        """使用 SSO 中的 refresh_token 尝试无感刷新 access_token"""
        try:
            from src.utils.license_validator.env import SA_LICENSE_API
            from src.sso_bridge import refresh_sso_token
            logger.info("[同步服务] 正在通过全局服务尝试无感刷新 SSO Token...")
            refreshed = refresh_sso_token(SA_LICENSE_API)
            if refreshed:
                new_token = self._try_load_sso_token()
                if new_token:
                    self.jwt_token = new_token

                    # 同步更新其它 http 客户端
                    from src.utils.license_validator.env import license_client
                    license_client.set_token(new_token)

                    # 广播成功刷新给前端
                    try:
                        from src.utils.websocket_manager import ws_manager
                        import asyncio
                        loop = getattr(ws_manager, "loop", None)
                        if loop:
                            asyncio.run_coroutine_threadsafe(
                                ws_manager.broadcast({"type": "auth_token_refreshed", "data": {"access_token": new_token}}),
                                loop
                            )
                    except Exception:
                        pass
                return True
        except Exception as e:
            logger.warning(f"[同步服务] 尝试无感刷新 Token 异常: {e}")
        return False

    def _handle_unauthorized_token(self):
        """
        当云端检测到 JWT 令牌失效/过期时的处理逻辑。
        [无感续期流程]:
          1. 先尝试后端主动刷新 + SSO 文件热重载（有效 token 即接受，不强要求与旧 token 不同）
          2. 如 SSO 也没有新 Token，通过 WS 请求前端做静默续期，等待最多 25 秒
          3. 25 秒内 SSO 文件出现任何有效 Token → 热重载跳过退出广播
          4. 25 秒内仍无有效 Token → 确认过期，广播 auth_session_invalid
        """
        from .helpers import is_token_expired
        logger.warning("[同步服务] 核心同步接口返回 401/认证过期，尝试从 SSO 文件热重载 Token...")
        try:
            # Step 1: 先尝试后端主动刷新（速度更快，不依赖前端 WS）
            try:
                self.try_refresh_token()
            except Exception as pre_refresh_err:
                logger.debug(f"[同步服务] Step 1 预刷新异常（忽略）: {pre_refresh_err}")

            # Step 1b: SSO 文件热重载（🔧 修复：有效 token 即接受，不强要求 token 字符串不同）
            # 原 bug：若 try_refresh_token 成功但新 token 与旧 token 内容相同（如服务端缓存
            # 返回同一 token），fresh_token != self.jwt_token 不成立，会跳过热重载直接等 25 秒超时
            # 注意：此处 try_load_sso_token 内部会自动处理过期检测（含5分钟预判），
            #       但只有 token 真正无效（过期超过当前时刻）才返回 None，所以这里额外做精确验证
            fresh_token = self._try_load_sso_token()
            if fresh_token and not is_token_expired(fresh_token, preempt_seconds=0):
                old_preview = self.jwt_token[:16] if self.jwt_token else "empty"
                self.jwt_token = fresh_token
                try:
                    from src.utils.license_validator.env import license_client
                    license_client.set_token(fresh_token)
                except Exception:
                    pass
                if fresh_token != (old_preview + "..." if len(old_preview) < len(fresh_token) else old_preview):
                    logger.info(f"[同步服务] ✅ 从 SSO 文件热重载到有效 Token ({old_preview}...)，跳过退出广播")
                else:
                    logger.info("[同步服务] ✅ SSO Token 有效（与当前相同或已刷新），继续运行")
                return

            # Step 2: 通过 WS 请求前端执行无感续期，等待最多 25 秒
            if not self._silent_refresh_pending:
                self._silent_refresh_pending = True
                try:
                    from src.utils.websocket_manager import ws_manager
                    import asyncio
                    import time
                    loop = getattr(ws_manager, "loop", None)
                    if loop:
                        asyncio.run_coroutine_threadsafe(
                            ws_manager.broadcast({"type": "auth_silent_refresh_request", "data": {"reason": "backend_401"}}),
                            loop
                        )
                        # 💡 关键防护：检查当前是否是在正在运行的 asyncio 事件循环线程（主线程）中。
                        # 如果在主线程，我们绝对禁止执行同步阻塞的 time.sleep() 等待，否则会造成全站 API 响应超时卡死！
                        is_in_event_loop = False
                        try:
                            asyncio.get_running_loop()
                            is_in_event_loop = True
                        except RuntimeError:
                            pass

                        if is_in_event_loop:
                            logger.warning("[同步服务] 检测到在主线程中触发 401，仅执行单次 SSO 重载，不进行阻塞式等待以防进程挂起")
                            refreshed_token = self._try_load_sso_token()
                            if refreshed_token and not is_token_expired(refreshed_token, preempt_seconds=0):
                                self.jwt_token = refreshed_token
                                try:
                                    from src.utils.license_validator.env import license_client
                                    license_client.set_token(refreshed_token)
                                except Exception:
                                    pass
                                return
                        else:
                            logger.info("[同步服务] 🔑 已请求前端静默刷新 Token，等待最多 25 秒...")
                            deadline = time.monotonic() + 25
                            while time.monotonic() < deadline:
                                time.sleep(1)
                                # 🔧 修复：只要能拿到任何有效（未过期）token 就接受，不强要求与旧值不同
                                # 此处用精确判断（preempt_seconds=0）：续期刷新后的 token 只要未真正过期就接受
                                refreshed_token = self._try_load_sso_token()
                                if refreshed_token and not is_token_expired(refreshed_token, preempt_seconds=0):
                                    self.jwt_token = refreshed_token
                                    logger.info(f"[同步服务] ✅ 前端静默刷新成功，已热重载有效 Token ({refreshed_token[:16]}...)，跳过退出广播")
                                    try:
                                        from src.utils.license_validator.env import license_client
                                        license_client.set_token(refreshed_token)
                                    except Exception:
                                        pass
                                    return
                            logger.warning("[同步服务] 前端静默刷新 25 秒内无响应，执行广播退出")
                except Exception as ws_req_err:
                    logger.warning(f"[同步服务] 请求前端静默刷新异常: {ws_req_err}")
                finally:
                    self._silent_refresh_pending = False
            else:
                logger.warning("[同步服务] 当前已有静默刷新等待中，跳过重复触发")
                return

            # Step 3: 确认真实过期，广播退出
            logger.warning("[同步服务] SSO 文件中无有效 Token，确认认证已过期，广播 auth_session_invalid")
            self.jwt_token = ""
            try:
                from src.utils.websocket_manager import ws_manager
                import asyncio
                loop = getattr(ws_manager, "loop", None)
                if loop:
                    asyncio.run_coroutine_threadsafe(
                        ws_manager.broadcast({"type": "auth_session_invalid", "data": {"reason": "401_expired"}}),
                        loop
                    )
            except Exception as ws_err:
                logger.error(f"[同步服务] 广播 auth_session_invalid 异常: {ws_err}")
        except Exception as sso_err:
            logger.error(f"[同步服务] 处理失效会话异常: {sso_err}")

