# -*- coding: utf-8 -*-
"""
微信 4.x 数据库解密核心引擎（纯 Python 实现）
基于 SQLCipher 4.0 的解密规范：
- AES-256-CBC
- PBKDF2-SHA512
- 4096 字节页面大小
- 80 字节保留空间 (16 字节 IV + 64 字节 HMAC)
"""

import os
import hmac
import struct
import hashlib
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SQLITE_HEADER = b"SQLite format 3\x00"
PAGE_SIZE = 4096
KEY_SIZE = 32
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE_SIZE = IV_SIZE + HMAC_SIZE  # 80 字节


def _derive_mac_key(enc_key: bytes, salt: bytes) -> bytes:
    """计算 SQLCipher HMAC 密钥。"""
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SIZE)


def _derive_sqlcipher_enc_key(key_material: bytes, salt: bytes) -> bytes:
    """从原始密码派生 AES 密钥。"""
    return hashlib.pbkdf2_hmac("sha512", key_material, salt, 256000, dklen=KEY_SIZE)


def _compute_page_hmac(mac_key: bytes, page: bytes, page_num: int) -> bytes:
    """计算单页的 HMAC-SHA512。"""
    offset = SALT_SIZE if page_num == 1 else 0
    data_end = PAGE_SIZE - RESERVE_SIZE + IV_SIZE
    mac = hmac.new(mac_key, digestmod=hashlib.sha512)
    mac.update(page[offset:data_end])
    mac.update(page_num.to_bytes(4, "little"))
    return mac.digest()


def _decrypt_page(enc_key: bytes, page: bytes, page_num: int) -> bytes:
    """解密单个页面数据。"""
    iv = page[PAGE_SIZE - RESERVE_SIZE : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    offset = SALT_SIZE if page_num == 1 else 0
    encrypted_page = page[offset : PAGE_SIZE - RESERVE_SIZE]

    cipher = Cipher(
        algorithms.AES(enc_key),
        modes.CBC(iv),
        backend=default_backend(),
    )
    decryptor = cipher.decryptor()
    decrypted_page = decryptor.update(encrypted_page) + decryptor.finalize()

    if page_num == 1:
        return SQLITE_HEADER + decrypted_page + (b"\x00" * RESERVE_SIZE)
    return decrypted_page + (b"\x00" * RESERVE_SIZE)


_RESOLVED_KEY_CACHE = {}  # 全局密钥派生缓存 (key_material, salt) -> (enc_key, mac_key, key_mode)


def _resolve_key_material(key_material: bytes, page1: bytes):
    """验证密钥并确定派生模式。"""
    if len(page1) < PAGE_SIZE:
        return None

    salt = page1[:SALT_SIZE]
    cache_key = (key_material, salt)
    if cache_key in _RESOLVED_KEY_CACHE:
        return _RESOLVED_KEY_CACHE[cache_key]

    stored_hmac = page1[PAGE_SIZE - HMAC_SIZE : PAGE_SIZE]

    # 尝试一：密钥直接是 AES 密钥
    mac_key = _derive_mac_key(key_material, salt)
    if hmac.compare_digest(stored_hmac, _compute_page_hmac(mac_key, page1, 1)):
        res = (key_material, mac_key, "raw_enc_key")
        _RESOLVED_KEY_CACHE[cache_key] = res
        return res

    # 尝试二：密钥是原始 SQLCipher passphrase，需要派生 AES 密钥
    derived_enc_key = _derive_sqlcipher_enc_key(key_material, salt)
    derived_mac_key = _derive_mac_key(derived_enc_key, salt)
    if hmac.compare_digest(stored_hmac, _compute_page_hmac(derived_mac_key, page1, 1)):
        res = (derived_enc_key, derived_mac_key, "sqlcipher_passphrase")
        _RESOLVED_KEY_CACHE[cache_key] = res
        return res

    return None


def _read_file_with_sharing(file_path: str) -> bytes:
    """
    在 Windows 下使用 win32file 以共享读写删除模式打开文件，
    能够强行读取处于锁定状态的 SQLite 数据库文件（如网络共享盘或运行中的微信文件）。
    """
    if os.name != 'nt':
        with open(file_path, "rb") as f:
            return f.read()
            
    import win32file
    import win32con
    
    try:
        # 使用共享读、共享写、共享删除的模式打开文件，防止被微信独占时 PermissionError
        handle = win32file.CreateFile(
            file_path,
            win32file.GENERIC_READ,
            win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE | win32file.FILE_SHARE_DELETE,
            None,
            win32con.OPEN_EXISTING,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None
        )
    except Exception as e:
        # 兜底：如果共享模式也报错，尝试常规 open
        with open(file_path, "rb") as f:
            return f.read()
            
    try:
        chunks = []
        while True:
            # 每次读取 64KB
            err, data = win32file.ReadFile(handle, 65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)
    finally:
        try:
            handle.Close()
        except:
            pass


class WeChatDatabaseDecryptor:
    """精简版微信4.x数据库解密工具"""

    def __init__(self, key_hex: str):
        if len(key_hex) != 64:
            raise ValueError("密钥必须是64位十六进制字符串")
        self.key_bytes = bytes.fromhex(key_hex)
        self.last_result = {
            "success": False,
            "error": "",
            "diagnostic_status": "not_run",
            "successful_pages": 0,
        }

    def decrypt_database(self, db_path: str, output_path: str) -> bool:
        """解密微信4.x数据库到输出路径"""
        self.last_result = {
            "success": False,
            "error": "",
            "diagnostic_status": "failed",
            "successful_pages": 0,
        }
        try:
            if not os.path.exists(db_path):
                self.last_result["error"] = f"文件不存在: {db_path}"
                return False

            try:
                encrypted_data = _read_file_with_sharing(db_path)
            except Exception as pe:
                self.last_result["error"] = (
                    f"读取文件失败: {pe}。提示：如果微信数据目录位于局域网共享盘（如 UNC 路径 \\\\ip\\share 或网络映射驱动器），"
                    f"且该微信客户端在对应机器上正在运行，Windows 网络共享协议会强制对文件施加独占锁定，导致本程序无法读取数据库。"
                    f"请务必将微信数据目录迁移配置在本地物理磁盘（如本地 C 盘或 D 盘），或关闭被占用的微信进程后重试。"
                )
                return False

            if len(encrypted_data) < PAGE_SIZE:
                self.last_result["error"] = "文件过小"
                return False

            # 如果已经是 SQLite 明文头部，直接复制
            if encrypted_data.startswith(SQLITE_HEADER):
                with open(output_path, "wb") as f:
                    f.write(encrypted_data)
                self.last_result.update({
                    "success": True,
                    "diagnostic_status": "ok",
                    "successful_pages": len(encrypted_data) // PAGE_SIZE,
                })
                return True

            total_pages = len(encrypted_data) // PAGE_SIZE
            page1 = encrypted_data[:PAGE_SIZE]

            resolved = _resolve_key_material(self.key_bytes, page1)
            if not resolved:
                self.last_result["error"] = "数据库校验未通过，密钥可能不匹配当前账号"
                return False

            enc_key, mac_key, key_mode = resolved

            with open(output_path, "wb") as out_f:
                for i in range(1, total_pages + 1):
                    offset = (i - 1) * PAGE_SIZE
                    page = encrypted_data[offset : offset + PAGE_SIZE]
                    dec_page = _decrypt_page(enc_key, page, i)
                    out_f.write(dec_page)

            self.last_result.update({
                "success": True,
                "diagnostic_status": "ok",
                "successful_pages": total_pages,
            })
            return True
        except Exception as e:
            self.last_result["error"] = f"解密发生异常: {e}"
            return False
