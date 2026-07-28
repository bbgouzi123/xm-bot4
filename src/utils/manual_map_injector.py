"""
Manual Map DLL 注入引擎
=======================
将 DLL 以手动映射方式注入到目标进程中，包括 PE 解析、重定位修正、IAT 导入表修正，
以及通过 Shellcode 调用 DllMain 的完整流程。

与 xm-isolate-container Tauri 端 inject_dll_to_wechat 逻辑完全一致。
lpReserved = 1 触发 DllMain 同步初始化路径，确保挂起进程恢复前 hooks 绝对生效。
"""

import struct
import ctypes
import logging
from ctypes import wintypes

from src.utils.isolate_win32 import (
    MEM_COMMIT, MEM_RESERVE, INFINITE, kernel32,
)

logger = logging.getLogger(__name__)

# Manual Map 专用常量
PAGE_EXECUTE_READWRITE = 0x40


def manual_map_inject(process_handle: int, dll_path: str) -> bool:
    """Manual Map 注入 DLL 到目标进程（与 Tauri 端完全一致）"""
    try:
        with open(dll_path, "rb") as f:
            dll_bytes = f.read()
    except Exception as e:
        logger.error(f"[隔离舱] 无法读取 DLL 文件: {e}")
        return False

    if len(dll_bytes) < 64 or dll_bytes[0:2] != b"MZ":
        logger.error("[隔离舱] 不合法的 DLL 文件（MZ 签名校验失败）")
        return False

    # 1. 解析 PE 头
    e_lfanew = struct.unpack_from("<I", dll_bytes, 0x3C)[0]
    if dll_bytes[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        logger.error("[隔离舱] 不合法的 NT 签名（PE 校验失败）")
        return False

    file_header = e_lfanew + 4
    num_sections = struct.unpack_from("<H", dll_bytes, file_header + 2)[0]
    opt_hdr = file_header + 20
    magic = struct.unpack_from("<H", dll_bytes, opt_hdr)[0]
    if magic != 0x20B:
        logger.error("[隔离舱] DLL 不是 64 位 PE32+ 格式")
        return False

    entry_rva = struct.unpack_from("<I", dll_bytes, opt_hdr + 16)[0]
    image_base = struct.unpack_from("<Q", dll_bytes, opt_hdr + 24)[0]
    size_of_image = struct.unpack_from("<I", dll_bytes, opt_hdr + 56)[0]
    size_of_headers = struct.unpack_from("<I", dll_bytes, opt_hdr + 60)[0]

    # 2. 在目标进程分配 SizeOfImage 内存
    remote_base = kernel32.VirtualAllocEx(
        process_handle, None, size_of_image,
        MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE
    )
    if not remote_base:
        logger.error("[隔离舱] VirtualAllocEx 分配远程内存失败")
        return False

    # 3. 构造本地镜像映射
    image = bytearray(size_of_image)
    image[0:min(size_of_headers, len(dll_bytes))] = dll_bytes[0:min(size_of_headers, len(dll_bytes))]

    # 拷贝节区
    sec_start = opt_hdr + 240
    for i in range(num_sections):
        off = sec_start + i * 40
        va = struct.unpack_from("<I", image, off + 12)[0]
        raw_sz = struct.unpack_from("<I", image, off + 16)[0]
        raw_ptr = struct.unpack_from("<I", image, off + 20)[0]
        if raw_sz > 0 and raw_ptr > 0:
            src_end = min(raw_ptr + raw_sz, len(dll_bytes))
            dest_end = va + (src_end - raw_ptr)
            if dest_end <= size_of_image:
                image[va:dest_end] = dll_bytes[raw_ptr:src_end]

    # 4. 重定位修正
    reloc_off = opt_hdr + 112
    reloc_rva = struct.unpack_from("<I", image, reloc_off)[0]
    reloc_sz = struct.unpack_from("<I", image, reloc_off + 4)[0]
    delta = (remote_base - image_base) & 0xFFFFFFFFFFFFFFFF

    if reloc_rva > 0 and reloc_sz > 0 and delta != 0:
        cur = reloc_rva
        end = reloc_rva + reloc_sz
        while cur < end and cur + 8 <= size_of_image:
            pg_rva = struct.unpack_from("<I", image, cur)[0]
            blk_sz = struct.unpack_from("<I", image, cur + 4)[0]
            if blk_sz < 8 or cur + blk_sz > end:
                break
            for j in range((blk_sz - 8) // 2):
                eo = cur + 8 + j * 2
                if eo + 2 > size_of_image:
                    break
                ev = struct.unpack_from("<H", image, eo)[0]
                if ev >> 12 == 10:  # IMAGE_REL_BASED_DIR64
                    tgt = pg_rva + (ev & 0x0FFF)
                    if tgt + 8 <= size_of_image:
                        cv = struct.unpack_from("<Q", image, tgt)[0]
                        struct.pack_into("<Q", image, tgt, (cv + delta) & 0xFFFFFFFFFFFFFFFF)
            cur += blk_sz

    # 5. IAT 导入表修正
    _fix_imports(image, size_of_image, opt_hdr)

    # 6. 写入镜像到目标进程
    written = ctypes.c_size_t(0)
    ok = kernel32.WriteProcessMemory(
        process_handle, remote_base, bytes(image), size_of_image, ctypes.byref(written)
    )
    if not ok:
        logger.error("[隔离舱] WriteProcessMemory 写入镜像失败")
        return False

    # 7. 擦除 MZ/PE 签名（无模块隐身）
    z2 = (ctypes.c_ubyte * 2)(0, 0)
    z4 = (ctypes.c_ubyte * 4)(0, 0, 0, 0)
    kernel32.WriteProcessMemory(process_handle, remote_base, z2, 2, None)
    kernel32.WriteProcessMemory(process_handle, remote_base + e_lfanew, z4, 4, None)

    # 8. 构造 Shellcode 并执行
    return _execute_shellcode(process_handle, remote_base, entry_rva)


def _fix_imports(image: bytearray, size_of_image: int, opt_hdr: int):
    """修正 PE 导入表（IAT）"""
    import_rva = struct.unpack_from("<I", image, opt_hdr + 104)[0]
    if import_rva == 0 or import_rva + 20 > size_of_image:
        return

    desc_off = import_rva
    while desc_off + 20 <= size_of_image:
        name_rva = struct.unpack_from("<I", image, desc_off + 12)[0]
        if name_rva == 0:
            break
        first_thunk = struct.unpack_from("<I", image, desc_off + 16)[0]
        orig_thunk = struct.unpack_from("<I", image, desc_off)[0]

        dll_name = b""
        cn = name_rva
        while cn < size_of_image and image[cn] != 0:
            dll_name += bytes([image[cn]])
            cn += 1

        h_mod = kernel32.GetModuleHandleW(dll_name.decode("ascii", errors="ignore"))
        if not h_mod:
            h_k32 = kernel32.GetModuleHandleW("kernel32.dll")
            if h_k32:
                lp = kernel32.GetProcAddress(h_k32, b"LoadLibraryW")
                if lp:
                    fn = ctypes.CFUNCTYPE(wintypes.HMODULE, wintypes.LPCWSTR)
                    h_mod = fn(lp)(dll_name.decode("ascii", errors="ignore"))

        if h_mod:
            ts = orig_thunk if orig_thunk > 0 else first_thunk
            to, io = ts, first_thunk
            while to + 8 <= size_of_image and io + 8 <= size_of_image:
                tv = struct.unpack_from("<Q", image, to)[0]
                if tv == 0:
                    break
                if tv & 0x8000000000000000:
                    addr = kernel32.GetProcAddress(h_mod, tv & 0xFFFF)
                else:
                    nirva = tv & 0xFFFFFFFF
                    an = b""
                    ao = nirva + 2
                    while ao < size_of_image and image[ao] != 0:
                        an += bytes([image[ao]])
                        ao += 1
                    an += b"\x00"
                    addr = kernel32.GetProcAddress(h_mod, an)
                if addr:
                    struct.pack_into("<Q", image, io, addr)
                to += 8
                io += 8
        desc_off += 20


def _execute_shellcode(process_handle: int, remote_base: int, entry_rva: int) -> bool:
    """构造并执行调用 DllMain 的 Shellcode"""
    dll_main = remote_base + entry_rva
    sc = bytearray([
        0x48, 0x83, 0xEC, 0x28,                                 # sub rsp, 28h
        0x48, 0xB9, 0, 0, 0, 0, 0, 0, 0, 0,                    # mov rcx, remote_base
        0x48, 0xC7, 0xC2, 0x01, 0x00, 0x00, 0x00,               # mov rdx, 1 (DLL_PROCESS_ATTACH)
        0x41, 0xFF, 0xC0,                                       # inc r8d (lpReserved=1 → 同步模式)
        0x48, 0xB8, 0, 0, 0, 0, 0, 0, 0, 0,                    # mov rax, dll_main
        0xFF, 0xD0,                                             # call rax
        0x48, 0x83, 0xC4, 0x28,                                 # add rsp, 28h
        0xC3                                                    # ret
    ])
    struct.pack_into("<Q", sc, 6, remote_base)
    struct.pack_into("<Q", sc, 27, dll_main)

    rsc = kernel32.VirtualAllocEx(
        process_handle, None, len(sc),
        MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE
    )
    if not rsc:
        logger.error("[隔离舱] 无法为 Shellcode 分配远程内存")
        return False

    ok = kernel32.WriteProcessMemory(process_handle, rsc, bytes(sc), len(sc), None)
    if not ok:
        logger.error("[隔离舱] 写入 Shellcode 失败")
        return False

    tid = wintypes.DWORD(0)
    rt = kernel32.CreateRemoteThread(process_handle, None, 0, rsc, None, 0, ctypes.byref(tid))
    if not rt:
        logger.error("[隔离舱] CreateRemoteThread 执行 Shellcode 失败")
        return False

    kernel32.WaitForSingleObject(rt, INFINITE)
    kernel32.CloseHandle(rt)
    logger.info("[隔离舱] ✅ Manual Map 注入完成，hooks 已同步生效")
    return True
