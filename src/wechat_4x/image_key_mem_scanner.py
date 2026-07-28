"""
image_key_mem_scanner.py
图片 AES 密钥进程内存扫描（方案 C，仅在 DLL 和 kvcomm 方案均失败时使用）
需要管理员权限，且用户须在微信中先点击查看过图片。
"""
import re
import glob
import os
import ctypes
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_V2_MAGIC = b'\x07\x08V2\x08\x07'
_MEM_COMMIT = 0x1000
_PAGE_NOACCESS = 0x01
_PAGE_GUARD = 0x100
_PAGE_RW_FLAGS = 0x04 | 0x08 | 0x40 | 0x80
_RE_KEY32 = re.compile(rb'(?<![a-zA-Z0-9])[a-zA-Z0-9]{32}(?![a-zA-Z0-9])')
_RE_KEY16 = re.compile(rb'(?<![a-zA-Z0-9])[a-zA-Z0-9]{16}(?![a-zA-Z0-9])')


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_uint32),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_uint32),
        ("Protect", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
    ]


def find_v2_ciphertext(attach_dir: str) -> Optional[bytes]:
    """从 V2 缩略图中提取第一个 AES 密文块（offset=15, 16 bytes）"""
    pattern = os.path.join(attach_dir, "*", "*", "Img", "*_t.dat")
    dat_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for fpath in dat_files[:100]:
        try:
            with open(fpath, "rb") as f:
                header = f.read(31)
            if header[:6] == _V2_MAGIC and len(header) >= 31:
                return header[15:31]
        except OSError:
            pass
    return None


def find_xor_key(attach_dir: str) -> Optional[int]:
    """从 V2 缩略图尾部推导 XOR key（JPEG 结尾 FF D9 → key = last_byte ^ 0xD9）"""
    pattern = os.path.join(attach_dir, "*", "*", "Img", "*_t.dat")
    dat_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    tail_counts: dict = {}
    for fpath in dat_files[:32]:
        try:
            sz = os.path.getsize(fpath)
            with open(fpath, "rb") as f:
                head = f.read(6)
                f.seek(sz - 2)
                tail = f.read(2)
            if head == _V2_MAGIC and len(tail) == 2:
                k = (tail[0], tail[1])
                tail_counts[k] = tail_counts.get(k, 0) + 1
        except OSError:
            pass
    if not tail_counts:
        return None
    most_common = max(tail_counts, key=tail_counts.get)
    return most_common[0] ^ 0xFF


def verify_aes_key(key_bytes: bytes, ciphertext: bytes) -> bool:
    """验证 AES-128-ECB 解密后是否为已知图片格式"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        cipher = Cipher(algorithms.AES(key_bytes[:16]), modes.ECB(), backend=default_backend())
        dec = cipher.decryptor()
        decrypted = dec.update(ciphertext) + dec.finalize()
        return (decrypted[:3] == b'\xFF\xD8\xFF' or
                decrypted[:4] == bytes([0x89, 0x50, 0x4E, 0x47]) or
                decrypted[:4] == b'RIFF' or
                decrypted[:4] == b'wxgf' or
                decrypted[:3] == b'GIF')
    except Exception:
        return False


def _scan_pid_memory(kernel32, h_process, ciphertext: bytes) -> Optional[str]:
    """扫描单个进程 RW 内存区域，寻找能解密的 AES key"""
    mbi = _MEMORY_BASIC_INFORMATION()
    address = 0
    regions = []
    while address < 0x7FFFFFFFFFFF:
        ret = kernel32.VirtualQueryEx(h_process, ctypes.c_void_p(address),
                                      ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ret:
            break
        is_rw = (mbi.Protect & _PAGE_RW_FLAGS) != 0
        if (mbi.State == _MEM_COMMIT and
                mbi.Protect != _PAGE_NOACCESS and
                not (mbi.Protect & _PAGE_GUARD) and
                mbi.RegionSize <= 50 * 1024 * 1024 and is_rw):
            regions.append((mbi.BaseAddress, mbi.RegionSize))
        next_addr = address + mbi.RegionSize
        if next_addr <= address:
            break
        address = next_addr

    for base_addr, region_size in regions:
        buf = ctypes.create_string_buffer(region_size)
        bytes_read = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(h_process, ctypes.c_void_p(base_addr),
                                        buf, region_size, ctypes.byref(bytes_read))
        if not ok or bytes_read.value < 32:
            continue
        data = buf.raw[:bytes_read.value]
        for m in _RE_KEY32.finditer(data):
            kb = m.group()
            if verify_aes_key(kb[:16], ciphertext):
                return kb[:16].decode("ascii")
            if verify_aes_key(kb, ciphertext):
                return kb.decode("ascii")
        for m in _RE_KEY16.finditer(data):
            kb = m.group()
            if verify_aes_key(kb, ciphertext):
                return kb.decode("ascii")
    return None


def scan(attach_dir: str) -> Optional[Tuple[str, int]]:
    """
    主入口：扫描微信进程内存提取 image_aes_key。
    需管理员权限，需用户先在微信中点击查看图片。
    返回 (aes_key_str, xor_key_int) 或 None。
    """
    ciphertext = find_v2_ciphertext(attach_dir)
    if not ciphertext:
        logger.warning("[ImageKey-Mem] 未找到 V2 .dat 缩略图，无法内存扫描")
        return None

    import subprocess
    pids = []
    for image in ("Weixin.exe", "WeChat.exe"):
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            for line in res.stdout.splitlines():
                if image in line:
                    parts = line.strip('"').split('","')
                    if len(parts) >= 2:
                        pids.append(int(parts[1]))
        except Exception:
            pass
    if not pids:
        logger.warning("[ImageKey-Mem] 微信未运行")
        return None

    kernel32 = ctypes.windll.kernel32
    for pid in pids:
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            continue
        try:
            key = _scan_pid_memory(kernel32, h, ciphertext)
            if key:
                xor_key = find_xor_key(attach_dir) or 0x88
                logger.info(f"[ImageKey-Mem] 内存扫描成功 PID={pid} aes={key}")
                return key, xor_key
        finally:
            kernel32.CloseHandle(h)
    return None
