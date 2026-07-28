"""
端到端加密中间件 — 对齐 xm-server::encryption + @xm/crypto

安全规范第 22 条实现：
- 检测请求头 X-XM-Encrypted: true → AES-256-GCM 解密请求体 → 处理 → 加密响应体
- 算法完全对齐 @xm/crypto (前端 Web Crypto API):
  - AES-256-GCM，IV 12 bytes
  - PBKDF2(password, salt="xm-core-salt", iterations=100000, SHA-256)
  - 密文格式: Base64(IV + ciphertext)
"""
import base64
import hashlib
import json
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("encryption")

# ═══════════════════════════════════════
# 常量（必须与 @xm/crypto 完全一致）
# ═══════════════════════════════════════
IV_LENGTH = 12
PBKDF2_SALT = b"xm-core-salt"
PBKDF2_ITERATIONS = 100_000
KEY_LENGTH = 32  # AES-256 → 32 bytes


def _get_crypto_password() -> str:
    """从环境变量获取加密密钥（对齐 xm-server get_crypto_password）"""
    return os.getenv("XM_CRYPTO_KEY", "xm-dev-key-2026")


def _derive_aes_key(password: str) -> bytes:
    """从密码字符串派生 AES-256 密钥（PBKDF2-HMAC-SHA256）"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=PBKDF2_SALT,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def aes_encrypt(plaintext: str, password: str) -> str:
    """AES-256-GCM 加密（对齐 @xm/crypto 的 encrypt）"""
    key = _derive_aes_key(password)
    iv = os.urandom(IV_LENGTH)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # IV + 密文 → Base64
    combined = iv + ciphertext
    return base64.b64encode(combined).decode("ascii")


def aes_decrypt(base64_data: str, password: str) -> str:
    """AES-256-GCM 解密（对齐 @xm/crypto 的 decrypt）"""
    key = _derive_aes_key(password)
    combined = base64.b64decode(base64_data)
    if len(combined) < IV_LENGTH:
        raise ValueError("密文太短")
    iv = combined[:IV_LENGTH]
    ciphertext = combined[IV_LENGTH:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return plaintext.decode("utf-8")


async def encryption_middleware(request: Request, call_next):
    """
    端到端加密中间件（对齐 xm-server::encryption::encryption_middleware）

    协议：
    - 前端带 X-XM-Encrypted: true 头 → 请求体为 {"_encrypted": true, "payload": "base64..."}
    - 后端解密请求体 → 正常处理 → 加密响应体 → 返回时带 X-XM-Encrypted: true
    - 未带加密头 → 直接放行（向下兼容）
    """
    is_encrypted = request.headers.get("X-XM-Encrypted", "") == "true"

    if not is_encrypted:
        # 未加密请求 → 直接放行
        return await call_next(request)

    password = _get_crypto_password()

    # 读取并解密请求体
    body = await request.body()
    body_str = body.decode("utf-8") if body else ""

    if body_str:
        try:
            envelope = json.loads(body_str)
            if envelope.get("_encrypted") is True and "payload" in envelope:
                decrypted = aes_decrypt(envelope["payload"], password)
                # 重写请求体为解密后的明文 JSON
                request._body = decrypted.encode("utf-8")
            # else: 非加密信封格式，保持原样
        except Exception as e:
            logger.error(f"[加密] 解密请求体失败: {e}")
            return Response(
                content=json.dumps({"code": 40000, "msg": "请求解密失败"}),
                status_code=400,
                media_type="application/json",
            )

    # 执行下游 handler
    response = await call_next(request)

    # 加密响应体
    resp_body = b""
    async for chunk in response.body_iterator:
        if isinstance(chunk, str):
            resp_body += chunk.encode("utf-8")
        else:
            resp_body += chunk

    resp_str = resp_body.decode("utf-8")

    try:
        encrypted_payload = aes_encrypt(resp_str, password)
        encrypted_body = json.dumps({
            "_encrypted": True,
            "payload": encrypted_payload,
        })
        return Response(
            content=encrypted_body,
            status_code=response.status_code,
            headers={
                "Content-Type": "application/json",
                "X-XM-Encrypted": "true",
            },
        )
    except Exception as e:
        logger.error(f"[加密] 加密响应体失败: {e}")
        # 降级为明文响应
        return Response(
            content=resp_str,
            status_code=response.status_code,
            media_type="application/json",
        )
