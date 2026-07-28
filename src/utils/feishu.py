"""
飞书通知器（移植自 xm-bot4 utils/feishu_notifier.py — 193行部分反编译）

原始文件: utils/feishu_notifier.py (PARTIAL(3), 193 lines)
通过飞书 API 发送通知消息给指定用户。
"""
import json
import time
import logging
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class FeishuNotifier:
    """飞书通知器（完整移植自 xm-bot4）"""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.base_url = 'https://open.feishu.cn/open-apis'
        self._token_cache: Dict[str, dict] = {}

    def _get_feishu_config(self) -> dict:
        """获取飞书配置"""
        try:
            from src.api.config_api import _load_configs
            configs = _load_configs()
            return configs.get('feishu_settings', {})
        except Exception:
            pass
        return {}

    def is_configured(self) -> bool:
        """检查飞书是否已配置"""
        cfg = self._get_feishu_config()
        app_id = cfg.get('appId', '').strip()
        app_secret = cfg.get('appSecret', '').strip()
        phone = cfg.get('phone', '').strip()

        return (
            app_id.startswith('cli_')
            and len(app_secret) > 10
            and len(phone) == 11
            and phone.isdigit()
        )

    def _get_tenant_access_token(self) -> Optional[str]:
        """获取租户访问令牌"""
        if not HAS_REQUESTS:
            return None

        cfg = self._get_feishu_config()
        app_id = cfg.get('appId', '').strip()
        app_secret = cfg.get('appSecret', '').strip()

        if not app_id or not app_secret:
            return None

        # 检查缓存（90分钟有效）
        cache_key = f'{app_id}:{app_secret}'
        cached = self._token_cache.get(cache_key)
        if cached and time.time() - cached['time'] < 5400 and cached.get('token'):
            return cached['token']

        try:
            url = f'{self.base_url}/auth/v3/tenant_access_token/internal'
            resp = requests.post(url, json={
                'app_id': app_id,
                'app_secret': app_secret,
            }, timeout=self.timeout)

            logger.debug(f'飞书获取token响应: {resp.status_code}')
            if resp.status_code != 200:
                return None

            data = resp.json()
            token = data.get('tenant_access_token')
            if token:
                self._token_cache[cache_key] = {
                    'token': token,
                    'time': time.time(),
                }
            return token
        except Exception as e:
            logger.error(f'[飞书] 获取token失败: {e}')
            return None

    def _batch_get_id(self, token: str, mobile: str,
                      id_type: str = 'user_id') -> Tuple[Optional[str], dict]:
        """通过手机号查找用户 ID"""
        if not HAS_REQUESTS:
            return (None, {})

        url = f'{self.base_url}/contact/v3/users/batch_get_id'
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; charset=utf-8',
        }
        params = {'user_id_type': id_type} if id_type else {}
        body = {'mobiles': [mobile]}

        try:
            resp = requests.post(url, headers=headers, params=params,
                                 json=body, timeout=self.timeout)
            data = resp.json()

            if resp.status_code != 200 or data.get('code') != 0:
                return (None, data)

            users_data = data.get('data', {})
            users = users_data.get('users', []) or users_data.get('user_list', [])
            if not users:
                return (None, data)

            user = users[0]
            if id_type == 'user_id':
                return (user.get('user_id'), data)
            return (user.get('open_id') or user.get('user_id'), data)

        except Exception as e:
            return (None, {'error': str(e)})

    def _send_text_to_user(self, token: str, user_id: str,
                           id_type: str, text: str) -> Tuple[bool, dict]:
        """向用户发送文本消息"""
        if not HAS_REQUESTS:
            return (False, {})

        url = f'{self.base_url}/im/v1/messages'
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; charset=utf-8',
        }
        payload = {
            'receive_id': user_id,
            'msg_type': 'text',
            'content': json.dumps({'text': text}, ensure_ascii=False),
        }
        params = {'receive_id_type': id_type}

        try:
            resp = requests.post(url, headers=headers, params=params,
                                 json=payload, timeout=self.timeout)
            data = resp.json()
            if resp.status_code != 200:
                return (False, data)
            return (data.get('code') == 0, data)
        except Exception as e:
            return (False, {'error': str(e)})

    def _format_mobile(self, phone: str) -> Optional[str]:
        """格式化手机号"""
        if not phone:
            return None
        p = phone.strip()
        if not p:
            return None
        if p.startswith('+'):
            return p
        return f'{p}'

    def send_notification(self, content: str, scene: str = '通知') -> dict:
        """发送通知（完整移植自 xm-bot4）"""
        cfg = self._get_feishu_config()
        phone = cfg.get('phone', '').strip()

        token = self._get_tenant_access_token()
        if not token:
            return {'success': False, 'reason': 'token_error'}

        if not phone:
            return {'success': False, 'reason': 'phone_empty'}

        text = f'[{scene}] {content}'

        # 通过 open_id 发送
        open_id, detail = self._batch_get_id(token, phone, 'open_id')
        if not open_id:
            return {
                'success': False,
                'reason': 'user_lookup_failed',
                'detail': detail,
                'id_type': 'open_id',
            }

        ok, send_detail = self._send_text_to_user(token, open_id, 'open_id', text)
        return {
            'success': ok,
            'reason': None if ok else 'send_failed',
            'detail': send_detail,
            'id_type': 'open_id',
        }
