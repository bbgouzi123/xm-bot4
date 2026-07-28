"""wcdb_diagnostics.py — WCDB 初始化失败诊断与前端广播。"""
import ctypes
import logging

logger = logging.getLogger(__name__)


def diagnose_wcdb_init_failure(rc: int, dll_funcs: dict, free_fn) -> None:
    """wcdb_init 失败时：获取内部诊断 → 记录日志 → 广播到前端告警面板和终端日志。"""
    internal_diag = ""
    try:
        fn = (dll_funcs or {}).get("wcdb_get_logs")
        if fn:
            out_ptr = ctypes.c_void_p(0)
            fn(ctypes.byref(out_ptr))
            if out_ptr.value:
                internal_diag = ctypes.string_at(out_ptr.value).decode("utf-8", errors="ignore").strip()
                try:
                    free_fn(out_ptr)
                except Exception:
                    pass
    except Exception:
        pass

    win_code, win_msg = 0, ""
    try:
        win_code = ctypes.windll.kernel32.GetLastError()
        win_msg = ctypes.FormatError(win_code).strip()
    except Exception:
        pass

    logger.warning(
        f"[WCDB监听] wcdb_init 初始化降级 rc={rc}, "
        f"Windows GetLastError={win_code} ({win_msg})。"
        f"{' 内部诊断: ' + internal_diag if internal_diag else ''}"
        f"系统将自动降级为 Python 影子拷贝监听机制（不影响正常运行）"
    )

    is_blocked = (
        "SecurityStatus:2" in internal_diag
        or "securitystatus:2" in internal_diag.lower()
        or win_code == 1813
    )
    if is_blocked:
        reason = (
            "安全软件拦截了数据通道的初始化（SecurityStatus=2）。"
            "这是已知的系统环境兼容性问题，不影响机器人的正常回复（已自动切换至备用通道），"
            "但若要恢复最高性能的实时感知，建议将程序所在目录添加到杀毒软件白名单后重启机器人。"
        )
    elif win_code == 1813:
        reason = "系统找不到数据通道所需的内部资源（1813），请检查程序目录文件完整性或安全软件是否删除相关文件。"
    else:
        reason = (
            f"数据通道初始化异常（内部码 {rc}），已自动切换至备用通道，正常功能不受影响。"
            + (f"诊断详情：{internal_diag}" if internal_diag else "")
        )

    try:
        import asyncio
        from src.utils.websocket_manager import ws_manager
        _txt = f"⚠️ [数据同步引擎] 初始化降级，已自动切换至备用通道。{reason}"

        loop = getattr(ws_manager, "loop", None)
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(ws_manager.broadcast({"type": "sys_log", "data": _txt}), loop)
            asyncio.run_coroutine_threadsafe(ws_manager.broadcast_alert(level="warning", title="数据同步引擎降级", content=reason), loop)
        else:
            if not hasattr(ws_manager, "sys_log_cache"):
                ws_manager.sys_log_cache = []
            ws_manager.sys_log_cache.append({"type": "sys_log", "data": _txt})
    except Exception:
        pass
