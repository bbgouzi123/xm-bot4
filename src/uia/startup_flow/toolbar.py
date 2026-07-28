import threading
from .utils import _log, _random_sleep
from .refresh import force_accessibility_refresh
from .window_ops import nudge_window, force_focus_window

try:
    import uiautomation as uia
except ImportError:
    uia = None

def find_nav_toolbar(hwnd: int, max_retries: int = 5):
    """
    在微信窗口中查找导航栏 ToolBar(Name='导航')。
    """
    if not uia:
        return None, None

    _result = [None, None]  # [root, nav_toolbar]

    def _work():
        try:
            import comtypes
            comtypes.CoInitialize()

            _root = uia.ControlFromHandle(hwnd)
            if not _root:
                _log("UIA", "ControlFromHandle 返回 None")
                return
            _result[0] = _root

            # 先发送 WM_GETOBJECT 初始化
            force_accessibility_refresh(hwnd, _root)
            _random_sleep(0.2, 0.4)

            for attempt in range(max_retries):
                # 尝试查找
                tb = _root.ToolBarControl(Name="导航")
                if tb.Exists(2, 0.5):
                    _log("UIA", f"✓ 第 {attempt + 1} 次找到导航栏")
                    _result[1] = tb
                    return

                # 备选：遍历子控件
                try:
                    for child in _root.GetChildren():
                        ct = getattr(child, 'ControlTypeName', '') or ''
                        if 'ToolBar' in ct:
                            _log("UIA", "✓ 通过遍历子控件找到工具栏")
                            _result[1] = child
                            return
                except Exception:
                    pass

                # 诊断（首次）
                if attempt == 0:
                    try:
                        import win32gui
                        ch_hwnds = []
                        def enum_child_callback(c_hwnd, _):
                            c_class = win32gui.GetClassName(c_hwnd)
                            c_title = win32gui.GetWindowText(c_hwnd)
                            ch_hwnds.append(f"hwnd={c_hwnd}({c_class}, '{c_title}')")
                            return True
                        try:
                            win32gui.EnumChildWindows(hwnd, enum_child_callback, None)
                        except Exception:
                            pass
                        _log("UIA", f"诊断 — 微信主窗口物理子句柄({len(ch_hwnds)}): {' | '.join(ch_hwnds[:8])}")
                    except Exception as ex:
                        _log("UIA", f"物理子句柄诊断失败: {ex}")

                    try:
                        ch = []
                        for child in _root.GetChildren():
                            ct = getattr(child, 'ControlTypeName', '') or ''
                            cn = getattr(child, 'Name', '') or ''
                            cc = getattr(child, 'ClassName', '') or ''
                            ch.append(f"{ct}('{cn}','{cc}')")
                        _log("UIA", f"诊断 — root UIA子控件({len(ch)}): {' | '.join(ch[:10])}")
                    except Exception:
                        pass

                _log("UIA", f"导航栏查找第 {attempt + 1} 次未命中")

                # 精简系统兼容：如果检测到没有子控件，每次都重新获取 root，防止 uiautomation 库缓存失效的 COM 对象
                try:
                    if not _root or len(_root.GetChildren()) == 0:
                        _root = uia.ControlFromHandle(hwnd)
                        if _root:
                            _result[0] = _root
                except Exception:
                    pass

                # 逐步升级刷新手段
                if attempt == 0:
                    force_accessibility_refresh(hwnd, _root)
                elif attempt == 1:
                    nudge_window(hwnd)
                    import time
                    time.sleep(0.3)
                    force_accessibility_refresh(hwnd, _root, escalate=True)
                elif attempt == 2:
                    force_focus_window(hwnd)
                    import time
                    time.sleep(0.5)
                    _root = uia.ControlFromHandle(hwnd)
                    if _root:
                        _result[0] = _root
                        force_accessibility_refresh(hwnd, _root, escalate=True)
                else:
                    force_focus_window(hwnd)
                    nudge_window(hwnd)
                    import time
                    time.sleep(1.0)
                    _root = uia.ControlFromHandle(hwnd)
                    if _root:
                        _result[0] = _root
                        force_accessibility_refresh(hwnd, _root, escalate=True)

                _random_sleep(0.6, 1.2)

        except Exception as e:
            import win32gui
            is_valid_win = False
            try:
                is_valid_win = win32gui.IsWindow(hwnd)
            except Exception:
                pass
            err_str = str(e)
            is_access_denied = "-2147024891" in err_str or "拒绝访问" in err_str
            if not is_valid_win or is_access_denied:
                # 微信窗口在此期间已被销毁/重启，静默忽略
                pass
            else:
                _log("UIA", f"导航栏查找异常: {e}")

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout=60)

    return _result[0], _result[1]
