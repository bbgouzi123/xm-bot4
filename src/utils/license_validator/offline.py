import base64
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from .storage import CONFIG_DIR
from .machine import MachineMixin

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

logger = logging.getLogger(__name__)

# 固定的主密钥进行 Base64 url-safe 解密
MASTER_KEY = b'eG1fYm90NF9vZmZsaW5lX2xpY2Vuc2Vfa2V5XzMyYl8='

def check_offline_activation() -> Optional[Dict[str, Any]]:
    """
    检查并校验本地离线激活码。
    """
    offline_key_file = CONFIG_DIR / "activation.dat"
    if not offline_key_file.exists() or not HAS_CRYPTOGRAPHY:
        return None
        
    try:
        raw_code = offline_key_file.read_text(encoding='utf-8').strip()
        encrypted_bytes = base64.b64decode(raw_code)
        
        fernet = Fernet(MASTER_KEY)
        decrypted_bytes = fernet.decrypt(encrypted_bytes)
        payload = json.loads(decrypted_bytes.decode('utf-8'))
        
        mac = payload.get("mac", "").upper()
        exp_str = payload.get("exp", "")
        
        local_mac = MachineMixin.get_machine_code().upper()
        
        if mac == local_mac:
            exp_dt = datetime.strptime(exp_str, "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if exp_dt >= today:
                days_remaining = (exp_dt - today).days
                # 返回最高权限的离线旗舰版授权
                return {
                    "valid": True,
                    "status": "active",
                    "mode": "offline_activation",
                    "plan_code": "flagship",
                    "plan_name": "旗舰版 (离线激活)",
                    "expires_at": exp_str + "T23:59:59Z",
                    "days_remaining": days_remaining,
                    "max_wechat": 10,
                    "ai_daily_limit": 10000,
                    "trial_starts_at": "",
                    "trial_ends_at": "",
                    "message": f"离线激活校验成功 (旗舰版，剩余 {days_remaining} 天)"
                }
            else:
                logger.warning("[授权] 离线激活码已过期。")
        else:
            logger.warning(f"[授权] 离线激活码设备不匹配 (授权: {mac}, 本机: {local_mac})")
    except Exception as e:
        logger.error(f"[授权] 离线激活码解析异常: {e}")
        
    return None
