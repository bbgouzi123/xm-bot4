"""
星码安全隔离舱 (xm-isolate-container) 自动化管理器
===================================================

本模块替换了原 Sandboxie-Plus 沙箱，直接集成我们自研的物理隔离舱底层。
通过启动挂起的微信进程，使用 Manual Map（手动映射）注入 Rust 内核 DLL，
实现文件重定向、硬件指纹伪装、透明 Socks5 一号一 IP 分流等黑盒安全隔离。

重要：注入方式必须为 Manual Map，而非 LoadLibraryW。
  原因：DllMain 通过 lpReserved 参数区分注入模式:
  - lpReserved == 1（Manual Map）→ 同步安装 hooks，挂起进程恢复前生效
  - lpReserved == NULL（LoadLibrary）→ 异步延迟初始化，挂起进程中无法生效
"""

import os
import sys
import time
import logging
import ctypes
from ctypes import wintypes
from typing import Dict, Tuple, Optional

# 确保 backend-python 根目录在 sys.path 中
_backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

logger = logging.getLogger(__name__)

from src.utils.isolate_win32 import (
    CREATE_SUSPENDED, INFINITE, STARTUPINFOW, PROCESS_INFORMATION, kernel32,
)
from src.utils.manual_map_injector import manual_map_inject


class IsolateContainerManager:
    _instance = None

    @classmethod
    def get_instance(cls) -> "IsolateContainerManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def find_kernel_dll(self) -> Optional[str]:
        """寻找大仓中编译生成的 xm_isolate_kernel.dll"""
        possible_paths = []
        if getattr(sys, "frozen", False):
            # 优先从可执行文件所在同级目录（安装目录）查找
            exe_dir = os.path.dirname(sys.executable)
            possible_paths.append(os.path.join(exe_dir, "xm_isolate_kernel.dll"))
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                possible_paths.append(os.path.join(meipass, "xm_isolate_kernel.dll"))

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../../"))
        possible_paths.extend([
            os.path.join(base_dir, "target", "debug", "xm_isolate_kernel.dll"),
            os.path.join(base_dir, "target", "release", "xm_isolate_kernel.dll"),
            os.path.abspath("./target/debug/xm_isolate_kernel.dll"),
            os.path.abspath("./target/release/xm_isolate_kernel.dll"),
            os.path.abspath("./xm_isolate_kernel.dll"),
        ])
        for p in possible_paths:
            if os.path.exists(p):
                logger.info(f"[隔离舱] 找到内核 DLL 路径: {p}")
                return p
        logger.error("[隔离舱] 未能定位到 xm_isolate_kernel.dll，请确保已执行 cargo build")
        return None

    def is_available(self) -> bool:
        """检查隔离舱方案是否就绪"""
        return self.find_kernel_dll() is not None

    def launch_wechat_in_container(
        self, wechat_path: str, index: int, socks_port: Optional[int] = None
    ) -> Tuple[bool, str]:
        """在隔离舱中拉起并注入微信"""
        dll_path = self.find_kernel_dll()
        if not dll_path:
            return False, "未找到隔离舱底层内核 DLL (xm_isolate_kernel.dll)"

        # 1. 确保并创建隔离的数据目录
        from src.utils.mutex_killer import ensure_data_isolation
        data_dir = ensure_data_isolation(index)

        # 2. 装载专属进程环境变量（子进程继承时 DLL 读取这些变量）
        os.environ["XM_WECHAT_DATA_DIR"] = data_dir
        os.environ["XM_WECHAT_INSTANCE"] = str(index)
        os.environ["XM_ISOLATE_KERNEL_PATH"] = dll_path
        if socks_port:
            os.environ["XM_WECHAT_SOCKS_PORT"] = str(socks_port)
            os.environ["XM_ISOLATE_SOCKS_PORT"] = str(socks_port)
        else:
            os.environ.pop("XM_WECHAT_SOCKS_PORT", None)
            os.environ.pop("XM_ISOLATE_SOCKS_PORT", None)
        os.environ["QT_ACCESSIBILITY"] = "1"

        # 3. 启动挂起微信进程
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        pi = PROCESS_INFORMATION()
        cmd_line = f'"{wechat_path}"'
        wechat_dir = os.path.dirname(wechat_path)

        success = kernel32.CreateProcessW(
            None, cmd_line, None, None, False,
            CREATE_SUSPENDED, None, wechat_dir,
            ctypes.byref(si), ctypes.byref(pi)
        )
        if not success:
            err_code = kernel32.GetLastError()
            return False, f"创建微信挂起进程失败 (Win32 Error: {err_code})"

        process_handle = pi.hProcess
        thread_handle = pi.hThread

        try:
            # 4. 使用 Manual Map 注入（与 Tauri 端完全一致）
            injected = manual_map_inject(process_handle, dll_path)
            if not injected:
                kernel32.TerminateProcess(process_handle, 1)
                return False, "Manual Map 注入隔离舱 Rust 内核 DLL 失败"

            # 5. 唤醒微信主线程
            kernel32.ResumeThread(thread_handle)
            logger.info(f"[隔离舱] 成功以隔离模式唤醒进程 PID={pi.dwProcessId}")
            return True, f"隔离实例 {index} 启动成功 (PID={pi.dwProcessId})"
        finally:
            os.environ.pop("XM_ISOLATE_KERNEL_PATH", None)
            kernel32.CloseHandle(process_handle)
            kernel32.CloseHandle(thread_handle)

    def multi_open_wechat(self, wechat_path: str, count: int, start_index: int = 1) -> Dict:
        """批量启动并隔离微信实例 (对接 multi-open 统一响应)"""
        result = {
            "success": False, "method": "安全隔离舱",
            "instances_started": 0, "sandbox_names": [], "details": [], "error": None,
        }
        if not self.is_available():
            result["error"] = "隔离舱底层内核 DLL 未就绪，请先编译项目"
            return result

        result["details"].append(f"星码安全隔离舱就绪，准备拉起 {count} 个物理隔离实例")

        from src.uia.startup_flow.narrator import start_narrator, stop_narrator
        narrator_started = False
        try:
            logger.info("[隔离舱] 启动讲述人以激活无障碍树...")
            start_narrator()
            narrator_started = True
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"[隔离舱] 启动讲述人失败: {e}")

        started_count = 0
        try:
            for i in range(count):
                idx = start_index + i
                socks_port = 10800 + idx
                ok, msg = self.launch_wechat_in_container(wechat_path, idx, socks_port=socks_port)
                result["details"].append(msg)
                if ok:
                    started_count += 1
                    result["sandbox_names"].append(f"instance_{idx}")
                if i < count - 1:
                    time.sleep(1.5)
        finally:
            if narrator_started:
                try:
                    logger.info("[隔离舱] 关闭讲述人...")
                    stop_narrator()
                except Exception:
                    pass

        result["instances_started"] = started_count
        result["success"] = started_count > 0
        if started_count == count:
            result["details"].append(f"✅ 全部 {count} 个隔离微信实例已就绪")
        elif started_count > 0:
            result["details"].append(f"⚠ 部分隔离实例启动成功 ({started_count}/{count})")
        else:
            result["details"].append("❌ 隔离实例启动全部失败")
        return result

    def get_full_status(self) -> Dict:
        """获取完整的隔离舱状态描述"""
        available = self.is_available()
        dll_path = self.find_kernel_dll() or "未找到"
        base_data_dir = r"D:\WeChatData"
        instances = []
        if os.path.exists(base_data_dir):
            for name in os.listdir(base_data_dir):
                if name.startswith("instance_"):
                    try:
                        idx = int(name.split("_")[1])
                        instances.append({
                            "name": name, "index": idx, "is_running": True,
                            "path": os.path.join(base_data_dir, name)
                        })
                    except ValueError:
                        pass
        return {
            "available": available, "installed": available,
            "service_running": available, "version": "1.0.0 (Rust)",
            "install_path": dll_path, "sandbox_count": len(instances),
            "sandboxes": instances
        }


def get_isolate_container_manager() -> IsolateContainerManager:
    return IsolateContainerManager.get_instance()

def is_isolate_container_available() -> bool:
    return get_isolate_container_manager().is_available()
