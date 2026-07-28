"""
网络请求封装模块
"""
from typing import Optional
from .env import license_client

class NetworkMixin:
    @staticmethod
    def _http_request(method: str, path: str, body: dict = None) -> Optional[dict]:
        """
        向 XM-User 后端发送 HTTP 请求
        """
        import logging
        logger = logging.getLogger(__name__)

        # 1. 发送前自动从 SSO 加载并注入最新的 Token
        try:
            from src.sso_bridge import read_sso_session
            session = read_sso_session()
            if session and session.get("access_token"):
                license_client.set_token(session["access_token"])
        except Exception as e:
            logger.debug(f"[订阅校验] 从 SSO 获取并设置 Token 异常: {e}")

        # 2. 发起请求
        if method.upper() == "GET":
            res = license_client.get(path)
        elif method.upper() == "POST":
            res = license_client.post(path, body)
        elif method.upper() == "PUT":
            res = license_client.put(path, body)
        elif method.upper() == "DELETE":
            res = license_client.delete(path)
        else:
            res = license_client._request(method, path, body)

        # 3. 结果检查：如果因为 token 失效导致授权查询失败，尝试无感刷新并重试一次
        is_unauthorized = False
        if res and isinstance(res, dict):
            code = res.get("code")
            msg = res.get("message") or res.get("msg") or ""
            if code == 40002 or code == 401 or "token" in str(msg).lower() or "认证" in str(msg) or "授权" in str(msg):
                is_unauthorized = True

        if is_unauthorized:
            logger.info("[订阅校验] 检测到接口返回授权失效，正在尝试自动无感刷新 token...")
            try:
                from src.utils.cloud_sync import get_cloud_client
                refreshed = get_cloud_client().try_refresh_token()
                if refreshed:
                    logger.info("[订阅校验] 无感刷新成功，正在发起接口重试...")
                    # 重新设置 token 并发起重试
                    session = read_sso_session()
                    if session and session.get("access_token"):
                        license_client.set_token(session["access_token"])
                    
                    if method.upper() == "GET":
                        return license_client.get(path)
                    elif method.upper() == "POST":
                        return license_client.post(path, body)
                    elif method.upper() == "PUT":
                        return license_client.put(path, body)
                    elif method.upper() == "DELETE":
                        return license_client.delete(path)
                    else:
                        return license_client._request(method, path, body)
            except Exception as re:
                logger.warning(f"[订阅校验] 自动刷新重试失败: {re}")

        return res
