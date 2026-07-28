"""
安全 UIA 操作封装 — 防止 COM RPC 断连导致进程崩溃

背景
----
Windows UI Automation 底层通过 COM RPC 与目标窗口通信。
当目标窗口 / 控件被销毁时（如微信浮层自动关闭、列表刷新导致旧引用失效），
COM 调用会抛出 HRESULT 错误码：
  - 0x80010108 (RPC_E_DISCONNECTED) — 服务器断开
  - 0x8001010d (RPC_E_SERVER_DIED_DNE) — 服务器已终止
  - 0x800706BA — RPC server unavailable
  - access violation — 内存级崩溃

这些错误发生在 C 层面，Python 的 try/except 无法捕获。
faulthandler 只能记录堆栈但不能阻止进程被杀。

解决方案
--------
1. 在进程启动早期调用 `install_com_crash_guard()` 注册 Windows SEH 异常过滤器，
   将 COM 相关的致命异常降级为「继续执行」而非「终止进程」。
2. 提供 `safe_exists()` / `safe_get_children()` 等包装函数，
   在 Python 层面提前捕获 OSError / COMError。
3. 提供 `safe_walk_control()` 安全版 WalkControl 遍历。
"""
import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

# ==================== COM 致命异常防护 ====================

# Windows SEH exception codes
_COM_EXCEPTION_CODES = {
    0x80010108,  # RPC_E_DISCONNECTED
    0x8001010d,  # RPC_E_SERVER_DIED_DNE
    0x800706BA,  # RPC_S_SERVER_UNAVAILABLE
    0xC0020043,  # RPC_NT_INTERNAL_ERROR (Windows RPC internal failure)
    0x800706BE,  # RPC_S_CALL_FAILED (RPC call failed)
    0x80010105,  # RPC_E_SERVERFAULT (RPC server fault)
    0x80000003,  # STATUS_BREAKPOINT (breakpoint/assert crash 断点级崩溃)
    # 🌟 0xC0000005 (STATUS_ACCESS_VIOLATION) 不在 VEH 中强行忽略。
    # 因为强行忽略非法内存访问会导致 CPU 陷入无限死循环。应通过 safe_walk_control 纯 Python 遍历从源头杜绝。
}

# EXCEPTION_CONTINUE_SEARCH = 0  (让下一个 filter 处理)
# EXCEPTION_EXECUTE_HANDLER = 1  (交给对应的 __except 块)
# EXCEPTION_CONTINUE_EXECUTION = -1 (忽略异常，继续执行)
EXCEPTION_CONTINUE_SEARCH = 0
EXCEPTION_CONTINUE_EXECUTION = -1

_original_filter = None
_guard_installed = False


def install_com_crash_guard():
    """安装全局 SEH 异常过滤器与 VEH 向量化异常处理器，将 COM RPC 断连降级为非致命异常。

    必须在进程启动早期调用（在 faulthandler.enable 之后）。
    """
    global _original_filter, _guard_installed
    if _guard_installed:
        return
    if sys.platform != 'win32':
        return

    try:
        kernel32 = ctypes.windll.kernel32

        # 设置 SEM_NOGPFAULTERRORBOX: 不弹出 GPF 对话框
        # SEM_FAILCRITICALERRORS(0x1) | SEM_NOGPFAULTERRORBOX(0x2) | SEM_NOOPENFILEERRORBOX(0x8000)
        kernel32.SetErrorMode(0x1 | 0x2 | 0x8000)

        # 注册 Unhandled Exception Filter
        # 签名: LONG WINAPI filter(EXCEPTION_POINTERS *ep)
        _EXCEPTION_FILTER = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)

        @_EXCEPTION_FILTER
        def _com_crash_filter(exception_info_ptr):
            """将 COM RPC 异常降级，避免进程终止。"""
            try:
                if exception_info_ptr:
                    # EXCEPTION_POINTERS -> ExceptionRecord -> ExceptionCode
                    # ExceptionRecord 是第一个字段（指针）
                    record_ptr = ctypes.cast(
                        exception_info_ptr,
                        ctypes.POINTER(ctypes.c_void_p)
                    )[0]
                    if record_ptr:
                        # ExceptionCode 是 EXCEPTION_RECORD 的第一个 DWORD
                        code = ctypes.cast(
                            record_ptr,
                            ctypes.POINTER(ctypes.c_ulong)
                        )[0]
                        if code in _COM_EXCEPTION_CODES:
                            # 🌟 使用 Win32 OutputDebugStringW 替代 Python logger，避免非 Python 线程中触发 GIL 锁死冲突
                            try:
                                kernel32.OutputDebugStringW(
                                    f"[COM防护] 拦截致命 COM 异常 0x{code:08X}，已降级并忽略\n"
                                )
                            except Exception:
                                pass
                            return EXCEPTION_CONTINUE_EXECUTION
            except Exception:
                pass
            return EXCEPTION_CONTINUE_SEARCH

        # 保持引用，避免被 GC 回收
        install_com_crash_guard._filter_ref = _com_crash_filter

        # 1. 注册全局 SEH Unhandled Exception Filter
        _set_filter = kernel32.SetUnhandledExceptionFilter
        _set_filter.argtypes = [_EXCEPTION_FILTER]
        _set_filter.restype = _EXCEPTION_FILTER
        _original_filter = _set_filter(_com_crash_filter)

        # 2. 注册进程级 VEH (Vectored Exception Handler) 向量异常处理器作为第一顺位，
        # 彻底防范 .NET 和 WebView2 内部非主线程抛出的 RPC_E_DISCONNECTED (0x80010108) 崩溃
        try:
            kernel32.AddVectoredExceptionHandler.argtypes = [ctypes.c_ulong, _EXCEPTION_FILTER]
            kernel32.AddVectoredExceptionHandler.restype = ctypes.c_void_p
            # First=1 代表注册在第一顺位
            _veh_handle = kernel32.AddVectoredExceptionHandler(1, _com_crash_filter)
            install_com_crash_guard._veh_ref = _veh_handle
            kernel32.OutputDebugStringW("[COM防护] VEH 向量化异常拦截器注册成功\n")
        except Exception as veh_e:
            logger.warning(f"[COM防护] VEH 注册失败: {veh_e}")

        _guard_installed = True
        logger.info("[COM防护] 全局 COM 崩溃防护（SEH + VEH 双重防御）已安装")
    except Exception as e:
        logger.warning(f"[COM防护] 安装失败: {e}")


# ==================== 安全 UIA 调用封装 ====================

def safe_exists(ctrl, timeout=0.5) -> bool:
    """安全版 ctrl.Exists()，捕获 COM 断连异常。

    当底层 COM 对象已失效时返回 False 而非让进程崩溃。
    """
    if ctrl is None:
        return False
    try:
        return ctrl.Exists(timeout)
    except OSError:
        # COM 断连在某些版本的 uiautomation 中会抛 OSError
        return False
    except Exception:
        return False


def safe_get_children(ctrl):
    """安全版 ctrl.GetChildren()，捕获 COM 断连异常。

    返回空列表而非让进程崩溃。
    """
    if ctrl is None:
        return []
    try:
        return ctrl.GetChildren()
    except OSError:
        return []
    except Exception:
        return []


def safe_walk_control(root, max_depth=4):
    """安全版 WalkControl 遍历，完全摆脱 uia.WalkControl 底层迭代器的 C++ 崩溃风险。

    使用 Python 手写 DFS 深度优先遍历，每一步获取子节点均通过 safe_get_children() 保护。
    当某个控件的 COM 引用失效时跳过该控件继续遍历，
    而非让整个 WalkControl 生成器崩溃。
    """
    if root is None:
        return

    # 节点栈存储: (control, depth)
    stack = [(root, 0)]
    try:
        while stack:
            ctrl, depth = stack.pop()
            
            # 跳过根节点本身，对齐原本 uia.WalkControl(root) 的行为（不 yield 根节点）
            if depth > 0:
                try:
                    # 预先访问基础属性，如果 COM 引用失效，会在此处被捕获并安全跳过
                    _ = ctrl.ControlTypeName
                    yield ctrl, depth
                except (OSError, Exception):
                    continue

            if depth < max_depth:
                children = safe_get_children(ctrl)
                # 逆序压入栈中，以保持与原本 WalkControl 一致的从左到右 DFS 深度优先遍历顺序
                if children:
                    for child in reversed(children):
                        stack.append((child, depth + 1))
    except (OSError, Exception):
        return


def safe_bounding_rect(ctrl):
    """安全获取 BoundingRectangle，失败返回 None。"""
    if ctrl is None:
        return None
    try:
        return ctrl.BoundingRectangle
    except (OSError, Exception):
        return None


def safe_get_name(ctrl) -> str:
    """安全获取控件 Name 属性，失败返回空字符串。"""
    if ctrl is None:
        return ""
    try:
        return ctrl.Name or ""
    except (OSError, Exception):
        return ""


def safe_control_type(ctrl) -> str:
    """安全获取 ControlTypeName，失败返回空字符串。"""
    if ctrl is None:
        return ""
    try:
        return ctrl.ControlTypeName or ""
    except (OSError, Exception):
        return ""


def safe_class_name(ctrl) -> str:
    """安全获取 ClassName，失败返回空字符串。"""
    if ctrl is None:
        return ""
    try:
        return ctrl.ClassName or ""
    except (OSError, Exception):
        return ""


def find_active_input_control_safely(root_ctrl, hwnd=None) -> str:
    """
    安全且快速地定位当前活跃的输入框并返回当前活跃会话名称。
    【终极自愈方案】100% 仅使用 Win32 API 预读取微信窗口标题，彻底剔除 UIA 控件遍历，
    从源头上 100% 避免任何 COM 底层超时卡死与挂起风险。
    """
    try:
        import win32gui
        import re
        if not hwnd:
            hwnd = win32gui.FindWindow("WeChatMainWndForPC", None) or win32gui.FindWindow("Qt51514QWindowIcon", None)
        
        if not hwnd or not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd):
            return ""
            
        title = win32gui.GetWindowText(hwnd)
        if title and title.strip():
            clean_title = title.strip()
            if clean_title not in ("微信", "WeChat", "登录", "Login"):
                # 自动剔除群聊人数后缀如 (99) 或 （5）
                clean_title = re.sub(r'[\(\uff08]\d+[\uff09\)]$', '', clean_title).strip()
                if clean_title:
                    return clean_title
    except Exception as win_ex:
        logger.debug(f"[安全UIA] 预读取微信窗口标题异常: {win_ex}")
    return ""


def safe_control_from_handle(hwnd):
    """安全地从窗口句柄创建 UIA Control，避免句柄失效引起的 COMError 崩溃。"""
    if not hwnd:
        return None
    try:
        import win32gui
        if not win32gui.IsWindow(hwnd):
            return None
        import uiautomation as auto
        return auto.ControlFromHandle(hwnd)
    except OSError:
        return None
    except Exception:
        return None


# ==================== SendKeys Monkey Patch ====================
def _patch_send_keys():
    try:
        import uiautomation as uia
        from src.utils.stop_signal import stop_signal
        
        _orig_send_keys = uia.SendKeys
        
        def safe_send_keys(text: str, interval: float = 0.01, waitTime: float = 0.05, charMode: bool = True, debug: bool = False) -> None:
            # 1. 检查停止信号和 ESC 键中断
            if stop_signal.is_stopped:
                raise RuntimeError("Keyboard input aborted due to stop signal")
            try:
                from src.uia.input_guard import uia_lock as physical_lock
                physical_lock.check_interrupt()
            except Exception:
                pass
            
            # 2. 默认缩短 waitTime 以防在 COM 单线程套间中发生长 sleep 导致的消息泵挂起
            # 如果是默认的 0.5s，我们强制缩短为 0.05s
            if waitTime == 0.5:
                waitTime = 0.05
                
            return _orig_send_keys(text, interval=interval, waitTime=waitTime, charMode=charMode, debug=debug)
            
        uia.SendKeys = safe_send_keys
        logger.info("[安全UIA] 成功注入 safe_SendKeys 猴子补丁，强制设置低操作延时 0.05s 并挂载中断检测")
    except Exception as patch_err:
        logger.error(f"[安全UIA] 注入 SendKeys 猴子补丁失败: {patch_err}")

_patch_send_keys()


def get_chat_container_safely(root_ctrl):
    """
    安全且兼容地获取聊天详情容器。
    兼容微信 4.1.9+ 的 QWidget/XView 结构与 4.1.7 以下的 mmui::ChatDetailView。
    """
    if root_ctrl is None:
        return None
    try:
        # 1. 尝试使用传统的 mmui::ChatDetailView
        container = root_ctrl.GroupControl(ClassName='mmui::ChatDetailView', searchDepth=12)
        if container.Exists(0.05):
            return container
            
        # 2. 如果不存在，通过消息列表 "chat_message_list" 向上找其 Parent
        import uiautomation as uia
        msg_list = root_ctrl.ListControl(AutomationId="chat_message_list", searchDepth=16)
        if msg_list.Exists(0.05):
            parent = msg_list.GetParentControl()
            if parent:
                return parent
                
        # 3. 如果还不存在，通过输入框 "chat_input_field" 向上找其 Parent
        input_field = root_ctrl.EditControl(AutomationId="chat_input_field", searchDepth=16)
        if input_field.Exists(0.05):
            parent = input_field.GetParentControl()
            if parent:
                return parent
    except Exception:
        pass
        
    # 4. 极端降级：不再直接返回 root_ctrl（防止触发全窗扫描挂起），直接返回 None
    return None



