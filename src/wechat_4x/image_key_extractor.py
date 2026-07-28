"""
image_key_extractor.py
微信图片解密密钥自动提取器（主调度器）

优先级（从高到低）：
  A. wx_key.dll → GetImageKey()：读 kvcomm 缓存 + MD5 本地派生（无需管理员）
  B. 纯文件扫描 kvcomm 目录：手动解析 code → 同样 MD5 派生（无需管理员）
  C. image_key_mem_scanner：进程内存扫描（需管理员，需用户先看图片）

成功后自动写入 config.json，供 dat_decryptor.py 读取。
"""
import os
import json
import struct
import hashlib
import logging
import ctypes
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 路径 & 配置 helpers
# ──────────────────────────────────────────────

def _get_config_json_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    product_dir = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(product_dir, "wx", "wechat-decrypt", "config.json")


def _load_config() -> dict:
    try:
        cfg_path = _get_config_json_path()
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_keys_to_config(aes_key: str, xor_key: int) -> None:
    cfg_path = _get_config_json_path()
    try:
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        cfg = _load_config()
        cfg["image_aes_key"] = aes_key
        cfg["image_xor_key"] = xor_key
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        logger.info(f"[ImageKey] 已保存 aes={aes_key} xor=0x{xor_key:02x} → {cfg_path}")
    except Exception as e:
        logger.warning(f"[ImageKey] 写入 config.json 失败: {e}")


def _clean_wxid(wxid: str) -> str:
    """截断到第二个下划线: wxid_g4psh_da6c → wxid_g4psh"""
    if not wxid:
        return wxid
    first = wxid.find("_")
    if first == -1:
        return wxid
    second = wxid.find("_", first + 1)
    return wxid[:second] if second != -1 else wxid


def _get_wxid_from_db_path(db_path: str) -> str:
    """从数据库路径中提取 wxid（取前两段 wxid_xxxx）"""
    try:
        parts = db_path.replace("\\", "/").split("/")
        for part in reversed(parts):
            if part.lower().startswith("wxid_"):
                segs = part.split("_")
                return "_".join(segs[:2]) if len(segs) >= 2 else part
    except Exception:
        pass
    return ""


def _get_attach_dir(db_path: str) -> Optional[str]:
    """从 db_path 推算 msg/attach 目录"""
    if not db_path:
        return None
    account_dir = os.path.dirname(os.path.dirname(os.path.dirname(db_path)))
    attach = os.path.join(account_dir, "msg", "attach")
    return attach if os.path.isdir(attach) else None


# ──────────────────────────────────────────────
# 方案 A：wx_key.dll GetImageKey（WeFlow 算法）
# ──────────────────────────────────────────────

def _try_dll_get_image_key(wxid: str) -> Optional[Tuple[str, int]]:
    """
    调用 wx_key.dll.GetImageKey()，读 kvcomm 缓存，本地 MD5 派生密钥。
    算法：xorKey = code & 0xFF；aesKey = MD5(str(code) + cleanedWxid)[:16]
    """
    try:
        from src.wechat_4x.key_service import get_dll_path
        dll_path = get_dll_path()
    except Exception:
        return None
    if not os.path.exists(dll_path):
        return None
    try:
        dll = ctypes.CDLL(dll_path)
        fn = dll._x_image_session
        fn.argtypes = [ctypes.c_char_p, ctypes.c_int]
        fn.restype = ctypes.c_bool
        buf = ctypes.create_string_buffer(8192)
        if not fn(buf, 8192):
            return None
        parsed = json.loads(buf.value.decode("utf-8", errors="ignore").strip())
        accounts = parsed.get("accounts", [])
        if not accounts or not accounts[0].get("keys"):
            return None
        code = accounts[0]["keys"][0]["code"]
        cleaned = _clean_wxid(wxid or accounts[0].get("wxid", ""))
        xor_key = code & 0xFF
        aes_key = hashlib.md5((str(code) + cleaned).encode()).hexdigest()[:16]
        logger.info(f"[ImageKey-DLL] 成功 aes={aes_key} xor=0x{xor_key:02x}")
        return aes_key, xor_key
    except Exception as e:
        logger.debug(f"[ImageKey-DLL] 异常: {e}")
        return None


# ──────────────────────────────────────────────
# 方案 B：扫描 kvcomm 目录（纯文件）
# ──────────────────────────────────────────────

def _possible_attach_dirs() -> list:
    dirs = []
    for base in [
        os.path.join(os.path.expanduser("~"), "Documents", "xwechat_files"),
        os.path.join(os.path.expanduser("~"), "Documents", "WeChat Files"),
    ]:
        if not os.path.isdir(base):
            continue
        try:
            for wd in os.listdir(base):
                a = os.path.join(base, wd, "msg", "attach")
                if os.path.isdir(a):
                    dirs.append(a)
        except OSError:
            pass
    return dirs


def _verify_aes_key(key_bytes: bytes, ciphertext: bytes) -> bool:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        cipher = Cipher(algorithms.AES(key_bytes[:16]), modes.ECB(), backend=default_backend())
        dec = cipher.decryptor()
        d = dec.update(ciphertext) + dec.finalize()
        return (d[:3] == b'\xFF\xD8\xFF' or d[:4] == bytes([0x89, 0x50, 0x4E, 0x47]) or
                d[:4] == b'RIFF' or d[:4] == b'wxgf' or d[:3] == b'GIF')
    except Exception:
        return False


def _try_codes_from_file(fpath: str, cleaned_wxid: str,
                         ciphertext: Optional[bytes]) -> Optional[Tuple[str, int]]:
    try:
        with open(fpath, "rb") as f:
            data = f.read()
        codes = [struct.unpack_from("<I", data, i)[0]
                 for i in range(0, len(data) - 3, 4)
                 if 0 < struct.unpack_from("<I", data, i)[0] < 0xFFFFFFFF]
        if not codes:
            return None
        for code in codes:
            xor_key = code & 0xFF
            aes_key = hashlib.md5((str(code) + cleaned_wxid).encode()).hexdigest()[:16]
            if ciphertext is None or _verify_aes_key(aes_key.encode(), ciphertext):
                return aes_key, xor_key
    except Exception:
        pass
    return None


def _try_kvcomm_scan(db_path: str) -> Optional[Tuple[str, int]]:
    if not db_path:
        return None
    account_dir = os.path.dirname(os.path.dirname(os.path.dirname(db_path)))
    wxid = _get_wxid_from_db_path(db_path)
    cleaned = _clean_wxid(wxid)

    kvdirs = [os.path.join(account_dir, "kvcomm"),
               os.path.join(account_dir, "kvcomm_img")]
    for kvdir in kvdirs:
        if not os.path.isdir(kvdir):
            continue
        # 寻找 V2 密文用于验证
        ciphertext = None
        for ad in _possible_attach_dirs():
            from src.wechat_4x.image_key_mem_scanner import find_v2_ciphertext
            ciphertext = find_v2_ciphertext(ad)
            if ciphertext:
                break
        # 遍历小文件
        candidates = []
        for root, _, files in os.walk(kvdir):
            for fname in files:
                fp = os.path.join(root, fname)
                try:
                    if 4 <= os.path.getsize(fp) <= 512:
                        candidates.append(fp)
                except OSError:
                    pass
        for fp in sorted(candidates, key=os.path.getmtime, reverse=True):
            result = _try_codes_from_file(fp, cleaned, ciphertext)
            if result:
                logger.info(f"[ImageKey-kvcomm] 成功 {fp}")
                return result
    return None


# ──────────────────────────────────────────────
# 公开入口
# ──────────────────────────────────────────────

def get_image_keys(db_path: str = "", wxid: str = "",
                   force: bool = False) -> Tuple[Optional[str], Optional[int]]:
    """
    自动提取微信 V2 图片解密密钥，返回 (aes_key_str, xor_key_int)。
    成功后写入 config.json 持久化；失败返回 (None, None)。
    """
    if not force:
        aes = os.getenv("WECHAT_IMAGE_AES_KEY") or os.getenv("IMAGE_AES_KEY")
        xor_raw = os.getenv("WECHAT_IMAGE_XOR_KEY") or os.getenv("IMAGE_XOR_KEY")
        if not aes or not xor_raw:
            cfg = _load_config()
            aes = aes or cfg.get("image_aes_key")
            xor_raw = xor_raw or cfg.get("image_xor_key")
        if aes:
            xv = 0x88
            try:
                xv = int(str(xor_raw), 0) if xor_raw else 0x88
            except ValueError:
                pass
            return aes, xv

    wxid = wxid or _get_wxid_from_db_path(db_path)
    logger.info("[ImageKey] 开始自动提取图片解密密钥...")

    # 方案 A：wx_key.dll GetImageKey（WeFlow 方案）
    result = _try_dll_get_image_key(wxid)
    if result:
        _save_keys_to_config(result[0], result[1])
        return result

    # 方案 B：kvcomm 文件扫描
    result = _try_kvcomm_scan(db_path)
    if result:
        _save_keys_to_config(result[0], result[1])
        return result

    # 方案 C：进程内存扫描（需管理员）
    attach_dir = _get_attach_dir(db_path)
    if not attach_dir:
        for ad in _possible_attach_dirs():
            attach_dir = ad
            break
    if attach_dir:
        logger.info("[ImageKey] 尝试进程内存扫描（需管理员权限）...")
        try:
            from src.wechat_4x.image_key_mem_scanner import scan
            result = scan(attach_dir)
            if result:
                _save_keys_to_config(result[0], result[1])
                return result
        except Exception as e:
            logger.debug(f"[ImageKey] 内存扫描异常: {e}")

    logger.warning("[ImageKey] 所有方案均失败，V2 图片将无法解密")
    return None, None
