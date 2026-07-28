"""
wcdb_mem_scanner.py
WCDB 数据库密钥内存扫描兜底模块（方案 C）

原理（参考 wechat-decrypt/key_scan_common.py）：
  WCDB 在打开数据库后，会将 enc_key 和 salt 以
  x'<64hex_enc_key><32hex_salt>' 格式缓存在进程内存中。
  直接扫描进程所有可读内存页，用正则匹配此模式，
  再与本地 DB 文件的 salt 做 HMAC-SHA512 交叉验证，
  确认后即为有效密钥。

适用场景：
  Hook 拦截超时（sqlite3_key 已被调用完毕），微信已正常登录。
  此时无需重启微信，直接扫内存即可拿到密钥，彻底消除封号风险。

依赖：标准库 ctypes，无第三方依赖。
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import struct
import hashlib
import hmac as hmac_mod
import logging
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# Windows API 常量
_MEM_COMMIT = 0x1000
_READABLE_PROTECTS = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}
_PAGE_SZ = 4096
_KEY_SZ = 32
_SALT_SZ = 16

# WCDB 内存特征：x'<64~192个十六进制字符>'
_HEX_RE = re.compile(rb"x'([0-9a-fA-F]{64,192})'")


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD),
        ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("_pad2", wt.DWORD),
    ]


def _verify_enc_key(enc_key: bytes, page1: bytes) -> bool:
    """用 HMAC-SHA512 验证 enc_key 是否能解密 page1（参考 wechat-decrypt/key_scan_common.py）"""
    if len(page1) < _PAGE_SZ:
        return False
    salt = page1[:_SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=_KEY_SZ)
    hmac_data = page1[_SALT_SZ: _PAGE_SZ - 80 + 16]
    stored_hmac = page1[_PAGE_SZ - 64: _PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def _collect_db_files(db_storage_dirs: List[str]) -> List[Tuple[str, str, bytes]]:
    """递归收集 db_storage 目录下所有 .db 文件的 (salt_hex, abs_path, page1)
    兼容两种目录结构：
      db_storage/*.db          （旧版微信）
      db_storage/db/*.db       （新版 xwechat 4.x）
    """
    results = []
    for db_dir in db_storage_dirs:
        if not os.path.isdir(db_dir):
            continue
        for root, _dirs, files in os.walk(db_dir):
            for name in files:
                if not name.endswith(".db"):
                    continue
                path = os.path.join(root, name)
                try:
                    sz = os.path.getsize(path)
                    if sz < _PAGE_SZ:
                        continue
                    with open(path, "rb") as f:
                        page1 = f.read(_PAGE_SZ)
                    if len(page1) < _PAGE_SZ:
                        continue
                    salt_hex = page1[:_SALT_SZ].hex()
                    results.append((salt_hex, path, page1))
                except OSError:
                    pass
    return results



def _enum_readable_regions(kernel32, h_process) -> List[Tuple[int, int]]:
    """枚举进程所有可读的内存区域"""
    regions = []
    addr = 0
    mbi = _MBI()
    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h_process, ctypes.c_uint64(addr),
                                   ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        if (mbi.State == _MEM_COMMIT and
                mbi.Protect in _READABLE_PROTECTS and
                0 < mbi.RegionSize < 500 * 1024 * 1024):
            regions.append((int(mbi.BaseAddress), int(mbi.RegionSize)))
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    return regions


def _scan_pid(kernel32, pid: int, db_files: List[Tuple[str, str, bytes]]) -> Optional[str]:
    """扫描单个 PID 进程内存，返回匹配的 64 字符 enc_key hex，或 None"""
    h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)  # VM_READ | QUERY_INFO
    if not h:
        logger.warning(f"[WCDB内存扫描] 无法打开进程 PID={pid}（权限不足？）")
        return None
    try:
        regions = _enum_readable_regions(kernel32, h)
        salt_to_db = {s: (path, page1) for s, path, page1 in db_files}
        remaining_salts = set(salt_to_db.keys())

        for base, size in regions:
            buf = ctypes.create_string_buffer(size)
            n_read = ctypes.c_size_t(0)
            ok = kernel32.ReadProcessMemory(h, ctypes.c_uint64(base), buf, size, ctypes.byref(n_read))
            if not ok or n_read.value < 64:
                continue
            data = buf.raw[:n_read.value]

            for m in _HEX_RE.finditer(data):
                hex_str = m.group(1).decode()
                hex_len = len(hex_str)

                # 96 字符：前 64 是 enc_key，后 32 是 salt（最精准的模式）
                if hex_len == 96:
                    enc_key_hex = hex_str[:64]
                    salt_hex = hex_str[64:]
                    if salt_hex in remaining_salts:
                        enc_key = bytes.fromhex(enc_key_hex)
                        _, page1 = salt_to_db[salt_hex]
                        if _verify_enc_key(enc_key, page1):
                            logger.info(f"[WCDB内存扫描] 命中 96字符模式 salt={salt_hex[:8]}... PID={pid}")
                            return enc_key_hex

                # 64 字符：纯 enc_key，逐一对所有 DB 验证
                elif hex_len == 64:
                    enc_key = bytes.fromhex(hex_str)
                    for salt_hex in list(remaining_salts):
                        _, page1 = salt_to_db[salt_hex]
                        if _verify_enc_key(enc_key, page1):
                            logger.info(f"[WCDB内存扫描] 命中 64字符模式 salt={salt_hex[:8]}... PID={pid}")
                            return hex_str

                # 超长字符串：取首 64 为 enc_key，末 32 为 salt
                elif hex_len > 96 and hex_len % 2 == 0:
                    enc_key_hex = hex_str[:64]
                    salt_hex = hex_str[-32:]
                    if salt_hex in remaining_salts:
                        enc_key = bytes.fromhex(enc_key_hex)
                        _, page1 = salt_to_db[salt_hex]
                        if _verify_enc_key(enc_key, page1):
                            logger.info(f"[WCDB内存扫描] 命中长字符模式({hex_len}) salt={salt_hex[:8]}... PID={pid}")
                            return enc_key_hex

            if not remaining_salts:
                break
    finally:
        kernel32.CloseHandle(h)
    return None


def scan_wechat_key(pid: int, db_storage_dirs: List[str]) -> Optional[str]:
    """
    主入口：扫描指定微信进程的内存，提取 WCDB enc_key（64 字符 hex）。

    Args:
        pid: 微信进程 PID（必须是已登录且打开过数据库的主进程）
        db_storage_dirs: 该账号的 db_storage 目录列表（用于 salt/HMAC 验证）

    Returns:
        64 字符 hex 密钥，或 None（扫描失败/进程权限不足/未登录）
    """
    if not pid or not db_storage_dirs:
        logger.warning("[WCDB内存扫描] PID 或 db_storage_dirs 为空，跳过")
        return None

    db_files = _collect_db_files(db_storage_dirs)
    if not db_files:
        logger.warning(f"[WCDB内存扫描] 未找到任何可用 .db 文件，路径: {db_storage_dirs}")
        return None

    kernel32 = ctypes.windll.kernel32
    logger.info(f"[WCDB内存扫描] 开始扫描 PID={pid}，共 {len(db_files)} 个 DB 文件参与验证...")
    print(f"[WCDB内存扫描] 开始扫描 PID={pid}，共 {len(db_files)} 个 DB 文件...")

    key = _scan_pid(kernel32, pid, db_files)
    if key:
        print(f"[WCDB内存扫描] ✅ 扫描成功，enc_key={key[:8]}...{key[-8:]}")
    else:
        print(f"[WCDB内存扫描] ❌ 未能从 PID={pid} 的内存中找到有效密钥")
    return key
