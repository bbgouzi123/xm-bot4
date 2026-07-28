"""
加密工具（移植自 xm-bot4 utils/crypto_utils.py — 82行部分反编译）

原始文件: utils/crypto_utils.py (PARTIAL(2), 82 lines)
使用 Fernet 对称加密保护敏感数据（API 密钥等）。
"""
import base64
import os
import json
from pathlib import Path
from typing import Optional

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


class CryptoManager:
    """加密管理器（完整移植自 xm-bot4）"""

    def __init__(self):
        # 安全规范第 12 条：沙盒数据持久化 → ~/.xm-products/xm-bot4/
        self.key_file = Path.home() / '.xm-products' / 'xm-bot4' / '.key'
        self._key = self._load_or_create_key()
        if HAS_CRYPTOGRAPHY and self._key:
            self._fernet = Fernet(self._key)
        else:
            self._fernet = None

    def _load_or_create_key(self) -> Optional[bytes]:
        """加载或创建加密密钥"""
        try:
            if self.key_file.exists():
                return self.key_file.read_bytes()
            if not HAS_CRYPTOGRAPHY:
                return None
            key = Fernet.generate_key()
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            self.key_file.write_bytes(key)
            return key
        except Exception as e:
            print(f'[加密] 密钥加载/创建失败: {e}')
            return None

    def encrypt_dict(self, data: dict) -> str:
        """加密字典数据"""
        if not self._fernet:
            return json.dumps(data, ensure_ascii=False)
        json_str = json.dumps(data)
        encrypted_data = self._fernet.encrypt(json_str.encode())
        return base64.b64encode(encrypted_data).decode()

    def decrypt_dict(self, encrypted_str: str) -> dict:
        """解密字典数据"""
        if not self._fernet:
            try:
                return json.loads(encrypted_str)
            except Exception:
                return {}
        try:
            encrypted_data = base64.b64decode(encrypted_str)
            decrypted_data = self._fernet.decrypt(encrypted_data)
            return json.loads(decrypted_data)
        except Exception:
            # 回退：可能是未加密的 JSON
            try:
                return json.loads(encrypted_str)
            except Exception:
                return {}

    def encrypt_text(self, text: str) -> str:
        """加密文本数据"""
        if not self._fernet:
            return text
        encrypted_data = self._fernet.encrypt(text.encode())
        return base64.b64encode(encrypted_data).decode()

    def decrypt_text(self, encrypted_str: str) -> str:
        """解密文本数据"""
        if not self._fernet:
            return encrypted_str
        try:
            encrypted_data = base64.b64decode(encrypted_str)
            decrypted_data = self._fernet.decrypt(encrypted_data)
            return decrypted_data.decode()
        except Exception:
            return encrypted_str


# 全局单例（移植自 xm-bot4）
_crypto_manager = None


def _get_crypto_manager() -> CryptoManager:
    global _crypto_manager
    if _crypto_manager is None:
        _crypto_manager = CryptoManager()
    return _crypto_manager


def encrypt_text(text: str) -> str:
    """加密文本的便捷函数"""
    return _get_crypto_manager().encrypt_text(text)


def decrypt_text(encrypted_str: str) -> str:
    """解密文本的便捷函数"""
    return _get_crypto_manager().decrypt_text(encrypted_str)


def encrypt_dict(data: dict) -> str:
    """加密字典的便捷函数"""
    return _get_crypto_manager().encrypt_dict(data)


def decrypt_dict(encrypted_str: str) -> dict:
    """解密字典的便捷函数"""
    return _get_crypto_manager().decrypt_dict(encrypted_str)
