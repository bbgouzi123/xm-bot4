# -*- coding: utf-8 -*-
"""xm-bot4 统一 HTTP 客户端（零依赖轻量版，支持 E2E 加密）"""
import io
import json
import logging
import os
import hashlib
import hmac as hmac_mod
import urllib.request
import urllib.error
from typing import Optional, Any

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

logger = logging.getLogger("xm-http-client")

# 加密常量（与 xm-server 一致）
IV_LENGTH = 12
SIG_LENGTH = 16
PBKDF2_SALT = b"xm-core-salt"
PBKDF2_ITERATIONS = 100_000
KEY_LENGTH = 32
OCTET_STREAM = "application/octet-stream"


class _MockResponse:
    """Mock 过的 urllib.request.urlopen 响应对象，支持 with 语句和 getheader/read 接口"""
    def __init__(self, data: bytes, content_type: str):
        self.data = data
        self.content_type = content_type

    def read(self) -> bytes:
        return self.data

    def getheader(self, name: str, default: Any = None) -> Any:
        if name.lower() == "content-type":
            return self.content_type
        return default

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def _request_via_curl(method: str, url: str, headers: dict, data: Optional[bytes], timeout: int = 15) -> tuple[int, bytes, str]:
    """使用 Windows/Linux 系统自带的 curl.exe 跨进程发送 HTTPS 请求，彻底避免 native SSL 冲突崩溃"""
    import tempfile
    import os
    import subprocess
    import re

    req_file_path = None
    resp_body_path = None
    resp_header_path = None
    try:
        # 1. 准备请求体临时文件
        if data is not None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".bin", prefix="curl_req_") as f:
                f.write(data)
                req_file_path = f.name
        # 2. 准备响应体和响应头临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin", prefix="curl_resp_body_") as f:
            resp_body_path = f.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", prefix="curl_resp_head_") as f:
            resp_header_path = f.name
        # 3. 构建 curl 命令行
        cmd = [
            "curl", "-s", "-S",
            "-X", method,
            "--max-time", str(timeout),
            "-D", resp_header_path,
            "-o", resp_body_path,
        ]
        # 注入 Header 头
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])

        # 注入 Data
        if req_file_path:
            cmd.extend(["--data-binary", f"@{req_file_path}"])

        cmd.append(url)

        # 4. 执行命令 (在 Windows 下后台隐藏执行，防止闪黑框)
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            timeout=timeout + 5
        )

        # 5. 解析状态码与 Content-Type
        status_code = 0
        content_type = ""
        if os.path.exists(resp_header_path):
            with open(resp_header_path, "r", encoding="utf-8", errors="ignore") as f:
                header_text = f.read()
                m = re.match(r"^HTTP/\S+\s+(\d+)", header_text)
                if m:
                    status_code = int(m.group(1))

                m_ct = re.search(r"(?i)^content-type:\s*(.+)", header_text, re.MULTILINE)
                if m_ct:
                    content_type = m_ct.group(1).strip()

        # 6. 读取二进制返回数据
        resp_body = b""
        if os.path.exists(resp_body_path):
            with open(resp_body_path, "rb") as f:
                resp_body = f.read()

        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="ignore") or "curl 进程返回非零"
            raise RuntimeError(f"CURL 跨进程异常 (code={proc.returncode}): {err_msg}")

        return status_code, resp_body, content_type

    finally:
        # 清理临时文件
        for path in (req_file_path, resp_body_path, resp_header_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass


class XMClient:
    """xm-bot4统一 HTTP 客户端（零依赖轻量版，支持 E2E 加密）"""

    def __init__(self, base_url: str, token: str = "", timeout: int = 15, encryption: bool = False):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.encryption = encryption
        # 生产环境 xm-user 强制要求加密通信 (406 拦截)
        if not self.encryption and "xmcore.top" in self.base_url and "xm-user" in self.base_url:
            self.encryption = True

        self._aes_key = None

        if self.encryption:
            if not CRYPTO_AVAILABLE:
                logger.error("加密已启用但未安装 cryptography 库，将回退到明文传输")
                self.encryption = False
            else:
                password = os.getenv("XM_CRYPTO_KEY", "xm-dev-key-2026")
                self._aes_key = hashlib.pbkdf2_hmac(
                    "sha256", password.encode(), PBKDF2_SALT, PBKDF2_ITERATIONS, dklen=KEY_LENGTH
                )

    def set_token(self, token: str):
        """设置认证 Token"""
        self.token = token

    def get(self, path: str, params: dict = None) -> Optional[dict]:
        """GET 请求"""
        if params:
            import urllib.parse
            qs = urllib.parse.urlencode(params)
            path = f"{path}?{qs}" if "?" not in path else f"{path}&{qs}"
        return self._request("GET", path)


    def post(self, path: str, body: dict = None) -> Optional[dict]:
        """POST 请求"""
        return self._request("POST", path, body)

    def _request(
        self, method: str, path: str, body: dict = None
    ) -> Optional[dict]:
        """核心请求方法"""
        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-XM-App-ID": "xm-bot4",
        }

        # 如果开启加密且不是 health 探活
        use_enc = self.encryption and not path.endswith("/health")

        if use_enc:
            if body is not None:
                headers["Content-Type"] = OCTET_STREAM
            headers["Accept"] = OCTET_STREAM

        # Token 自动注入
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        data = None
        if body is not None:
            plaintext = json.dumps(body, ensure_ascii=False)
            if use_enc:
                # AES-GCM 加密
                iv = os.urandom(IV_LENGTH)
                aesgcm = AESGCM(self._aes_key)
                ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
                payload = iv + ciphertext
                # HMAC 签名
                sig = hmac_mod.new(self._aes_key, payload, hashlib.sha256).digest()[:SIG_LENGTH]
                data = sig + payload
            else:
                data = plaintext.encode("utf-8")

        try:
            # 🌟 HTTPS 走系统 curl 物理跨进程发送以避开打包 _ssl 冲突的 native access violation
            if url.startswith("https://"):
                status_code, resp_body, ct = _request_via_curl(method, url, headers, data, self.timeout)
                if status_code >= 400:
                    # ⚠️ 保留响应体：将 resp_body 包装成可 read() 的 fileobj 传给 HTTPError，
                    # 确保后续 e.read() 能拿到业务错误信息（如「目标用户不存在」），
                    # 防止 fileobj=None 导致 read() 抛 AttributeError 并回退为「未知错误」。
                    raise urllib.error.HTTPError(
                        url, status_code, f"HTTP Error {status_code}",
                        {"Content-Type": ct or "application/json"},
                        io.BytesIO(resp_body)
                    )
                resp_ctx = _MockResponse(resp_body, ct)
            else:
                req = urllib.request.Request(
                    url, data=data, method=method, headers=headers
                )
                resp_ctx = urllib.request.urlopen(req, timeout=self.timeout)

            with resp_ctx as resp:
                resp_data = resp.read()
                
                if use_enc and resp.getheader("Content-Type") == OCTET_STREAM:
                    # 解密响应
                    if len(resp_data) < SIG_LENGTH + IV_LENGTH + 1:
                        raise ValueError("加密响应体太短")
                    
                    sig_received = resp_data[:SIG_LENGTH]
                    payload_received = resp_data[SIG_LENGTH:]
                    
                    # 验证签名
                    sig_expected = hmac_mod.new(self._aes_key, payload_received, hashlib.sha256).digest()[:SIG_LENGTH]
                    if not hmac_mod.compare_digest(sig_received, sig_expected):
                        raise ValueError("响应 HMAC 校验失败")
                    
                    iv_received = payload_received[:IV_LENGTH]
                    ciphertext_received = payload_received[IV_LENGTH:]
                    aesgcm = AESGCM(self._aes_key)
                    decrypted_bytes = aesgcm.decrypt(iv_received, ciphertext_received, None)
                    result = json.loads(decrypted_bytes.decode("utf-8"))
                else:
                    result = json.loads(resp_data.decode("utf-8"))

                # 黄金信封日志
                if isinstance(result, dict) and "code" in result:
                    if result["code"] != 20000 and result.get("code") != 0:
                        logger.warning(
                            f"[{method} {path}] 业务错误 [{result['code']}]: "
                            f"{result.get('msg', '')}"
                        )

                return result

        except urllib.error.HTTPError as e:
            try:
                error_body = e.read()
                error_ct = e.headers.get("Content-Type", "")
                # 4xx 客户端错误（如 404 用户不存在）对于通知类接口是良性状态，降为 debug
                _log_fn = logger.debug if 400 <= e.code < 500 else logger.warning
                # 如果错误响应也被加密了（生产环境强制），先解密再解析
                if use_enc and "octet-stream" in error_ct and len(error_body) > SIG_LENGTH + IV_LENGTH + 1:
                    sig_r = error_body[:SIG_LENGTH]
                    payload_r = error_body[SIG_LENGTH:]
                    iv_r = payload_r[:IV_LENGTH]
                    ct_r = payload_r[IV_LENGTH:]
                    aesgcm = AESGCM(self._aes_key)
                    decrypted = aesgcm.decrypt(iv_r, ct_r, None)
                    error_json = json.loads(decrypted.decode("utf-8"))
                    _log_fn(f"[{method} {path}] HTTP {e.code}: {error_json}")
                    return error_json
                else:
                    error_json = json.loads(error_body.decode("utf-8"))
                    _log_fn(f"[{method} {path}] HTTP {e.code}: {error_json}")
                    return error_json
            except Exception:
                logger.error(f"[{method} {path}] HTTP {e.code} {e.reason}")
                return None
        except urllib.error.URLError as e:
            logger.debug(f"[{method} {path}] 连接失败: {e.reason} (请确保本地授权服务 42001 已启动)")
            return None
        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str or "refused" in err_str or "connection" in err_str:
                logger.debug(f"[{method} {path}] 连接或超时异常 (降级处理): {e}")
            else:
                logger.warning(f"[{method} {path}] 请求异常 (已降级为 warning): {e}")
            return None
