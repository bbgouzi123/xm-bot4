"""针对 PyInstaller 打包环境下的 DLL 寻找路径补丁模块。"""
import os
import sys

def apply_dll_patch():
    """
    针对 PyInstaller 打包运行环境下的 DLL 寻找路径进行补丁，
    以彻底解决 pythonnet 加载 Python.Runtime.dll 时解析 runtime 符号失败的问题。
    """
    if getattr(sys, 'frozen', False):
        _exe_dir = os.path.dirname(sys.executable)
        _internal_dir = os.path.join(_exe_dir, "_internal")
        
        # 🌟 自动解除可能由网络下载/解压/拷贝导致的 DLL 锁定问题（Windows Mark of the Web 导致的加载失败）
        try:
            if os.path.exists(sys.executable + ":Zone.Identifier"):
                try:
                    os.remove(sys.executable + ":Zone.Identifier")
                except Exception:
                    pass
            for _root, _, _files in os.walk(_internal_dir):
                for _file in _files:
                    if _file.lower().endswith(".dll"):
                        _dll_path = os.path.join(_root, _file)
                        try:
                            os.remove(_dll_path + ":Zone.Identifier")
                        except Exception:
                            pass
        except Exception:
            pass

        # 扩充 PATH 环境变量，使得 clr_loader 能通过 LoadLibrary 找到主 Python DLL
        os.environ["PATH"] = _exe_dir + os.path.pathsep + _internal_dir + os.path.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(_exe_dir)
        except Exception:
            pass
        try:
            os.add_dll_directory(_internal_dir)
        except Exception:
            pass
        try:
            _runtime_dir = os.path.join(_internal_dir, "pythonnet", "runtime")
            if os.path.exists(_runtime_dir):
                os.environ["PATH"] = _runtime_dir + os.path.pathsep + os.environ["PATH"]
                os.add_dll_directory(_runtime_dir)
                # 🔧 设置 DEVPATH 让 .NET Framework Fusion 直接从此目录加载程序集
                os.environ["DEVPATH"] = _runtime_dir + os.path.pathsep + os.environ.get("DEVPATH", "")
        except Exception:
            pass

        # 🌟 显式锁定 PYTHONNET_PYDLL 环境变量，防止 pythonnet 的 clr_loader 在复杂环境下定位 python312.dll 错误
        try:
            _dll_name = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
            _candidates = [
                os.path.join(_exe_dir, _dll_name),
                os.path.join(_internal_dir, _dll_name),
                os.path.join(_internal_dir, "DLLs", _dll_name),
            ]
            for _path in _candidates:
                if os.path.exists(_path):
                    os.environ["PYTHONNET_PYDLL"] = _path
                    break
        except Exception:
            pass
