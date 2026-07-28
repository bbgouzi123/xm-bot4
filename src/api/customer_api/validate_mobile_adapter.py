import httpx
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class ValidateMobileAdapter:
    """手机号合规性校验适配器（外部黑白名单校验）"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    async def validate(self, mobile: str) -> bool:
        """调用外部接口校验手机号是否可以加好友。返回 True 表示合规，False 表示拦截。"""
        url = self.config.get("validate_url")
        if not url:
            # 默认放行
            return True
            
        headers = self.config.get("headers", {})
        params = self.config.get("params", {})
        
        query_params = {**params, "mobile": mobile}
        
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers=headers, params=query_params)
                if resp.status_code == 200:
                    res_data = resp.json()
                    valid_field = self.config.get("valid_field", "valid")
                    if valid_field in res_data:
                        return bool(res_data[valid_field])
                    if "data" in res_data and isinstance(res_data["data"], dict):
                        return bool(res_data["data"].get("valid", True))
                    return True
        except Exception as e:
            logger.error(f"[ValidateMobileAdapter] 校验手机号 {mobile} 异常: {e}")
            return bool(self.config.get("fail_safe", True))
        return True
