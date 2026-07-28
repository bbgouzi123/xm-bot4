"""
手机号验证服务（移植自 xm-bot4 services/validate_mobile_service.py — 37行部分反编译）
"""
import re
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class ValidateMobileService:
    """手机号验证服务"""

    MOBILE_PATTERN = re.compile(r'^1[3-9]\d{9}$')

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._api_url = self.config.get('api_url', '')
        self._api_key = self.config.get('api_key', '')

    def validate(self, phone: str) -> Dict:
        """验证手机号"""
        if not phone:
            return {'valid': False, 'error': '手机号不能为空'}

        phone = phone.strip().replace(' ', '').replace('-', '')

        # 去除国际区号
        if phone.startswith('+86'):
            phone = phone[3:]
        elif phone.startswith('86') and len(phone) == 13:
            phone = phone[2:]

        if not self.MOBILE_PATTERN.match(phone):
            return {'valid': False, 'error': '手机号格式不正确'}

        return {
            'valid': True,
            'phone': phone,
            'formatted': f'+86{phone}',
        }

    async def validate_and_check(self, phone: str) -> Dict:
        """验证并在线查询（如有 API 配置）"""
        result = self.validate(phone)
        if not result['valid']:
            result['has_wechat'] = False
            return result

        result['has_wechat'] = True
        if self._api_url and self._api_key:
            try:
                import requests
                resp = requests.post(
                    self._api_url,
                    json={'phone': phone},
                    headers={'Authorization': f'Bearer {self._api_key}'},
                    timeout=10,
                )
                if resp.status_code == 200:
                    api_data = resp.json()
                    result.update(api_data)
                    # 识别常见未注册微信反馈
                    if api_data.get('has_wechat') is False or api_data.get('registered') is False:
                        result['has_wechat'] = False
                    elif 'status' in api_data and api_data.get('status') in (0, '0', 'unregistered'):
                        result['has_wechat'] = False
            except Exception as e:
                logger.warning(f'[手机验证] 在线查询失败: {e}')

        return result
