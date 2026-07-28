import os
import sys
import ctypes
import time
import subprocess
import logging
import json
import datetime
import hashlib
from typing import Optional

logger = logging.getLogger("WcdbKeyHelpers")

def _get_dynamic_token() -> bytes:
    """生成与 DLL 内部匹配的 0 成本动态暗号 Token"""
    today_str = datetime.date.today().strftime("%Y%m%d")
    salt = "XmCoreSecretSalt"
    token_src = today_str + salt
    return hashlib.md5(token_src.encode('utf-8')).hexdigest().encode('utf-8')


def _get_dll_path(dll_name: str) -> str:
    # 环境变量最高优先级（方便调试和特殊部署）
    env_path = os.environ.get("WX_KEY_DLL_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    # PyInstaller 打包环境：兼容根目录、assets 子目录及 _internal/assets 子目录下的 DLL 定位
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        candidates_frozen = [
            os.path.join(os.path.dirname(sys.executable), dll_name),
            os.path.join(meipass, dll_name),
            os.path.join(meipass, 'assets', dll_name),
            os.path.join(os.path.dirname(sys.executable), '_internal', 'assets', dll_name),
            os.path.join(os.path.dirname(sys.executable), 'assets', dll_name),
        ]
        for p in candidates_frozen:
            if os.path.exists(p):
                return p

    # 开发环境：从项目目录查找
    here = os.path.dirname(os.path.abspath(__file__))                     # src/wechat_4x
    src_dir = os.path.dirname(here)                                        # src
    backend_dir = os.path.dirname(src_dir)                                 # backend-python
    product_dir = os.path.dirname(backend_dir)                             # xm-bot4

    candidates = [
        os.path.join(backend_dir, "assets", dll_name),
        os.path.join(product_dir, "wx", "WeFlow", "resources", dll_name),
        os.path.join(backend_dir, "resources", dll_name),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]  # 返回第一个作为错误信息用


def _find_wechat_pid() -> Optional[int]:
    """通过 tasklist 查找微信进程的 PID"""
    for image in ("Weixin.exe", "WeChat.exe"):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            output = result.stdout.strip()
            if output and not "没有运行的任务" in output and not "No tasks" in output:
                lines = output.split('\n')
                for line in lines:
                    parts = line.split(',')
                    if len(parts) >= 2:
                        pid_str = parts[1].replace('"', '').strip()
                        if pid_str.isdigit():
                            return int(pid_str)
        except Exception:
            pass
    return None


def _find_wxid_by_pid(pid: int) -> Optional[str]:
    """通过遍历窗口句柄与进程 PID 的关联反查微信专属的 WXID 标识"""
    try:
        import win32gui
        import win32process
        from src.utils.instance_manager import InstanceManagerV2
        
        all_inst = InstanceManagerV2.get_instance().get_all_instances()
        if not all_inst:
            return None
            
        hwnds = []
        def enum_cb(hwnd, extra):
            _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
            if win_pid == pid:
                hwnds.append(hwnd)
            return True
        win32gui.EnumWindows(enum_cb, None)
        
        for hwnd in hwnds:
            for inst_id, inst_data in all_inst.items():
                if inst_data.get("window_handle") == hwnd:
                    wxid = inst_data.get("wxid")
                    if wxid and not wxid.startswith("wx_"):
                        return wxid
    except Exception:
        pass
    return None


def _validate_key_for_pid(key: str, pid: int) -> bool:
    """验证密钥是否能够成功解密指定 PID 对应的微信 session.db 数据库"""
    try:
        from src.wechat_4x.db_match_helper import get_wechat_base_dirs, match_db_storage_by_key
        import psutil
        
        # 1. 优先通过该 PID 进程当前打开的文件句柄定位其 db_storage 目录，100% 精准
        proc = psutil.Process(pid)
        open_files = []
        try:
            for f in proc.open_files():
                open_files.append(f.path.lower())
        except Exception:
            pass
            
        target_db_storage = None
        for fpath in open_files:
            if "session.db" in fpath and "db_storage" in fpath:
                target_db_storage = os.path.dirname(fpath)
                break
                
        if target_db_storage:
            matched = match_db_storage_by_key(key, [target_db_storage])
            if matched:
                return True
                
        # 2. 兜底扫描：若没拿到文件句柄，先根据 PID 获取微信号，只允许验证与此进程关联的微信号数据目录，严禁跨账号解密校验！
        target_wxid = _find_wxid_by_pid(pid)
        if target_wxid:
            from src.utils.wechat_key_store import clean_wxid
            target_wxid_clean = clean_wxid(target_wxid)
            import hashlib
            target_wxid_md5 = hashlib.md5(target_wxid_clean.encode('utf-8')).hexdigest()
            
            base_dirs = get_wechat_base_dirs()
            all_db_storage_dirs = []
            for base_dir in base_dirs:
                if os.path.isdir(base_dir):
                    for entry in os.listdir(base_dir):
                        entry_clean = clean_wxid(entry)
                        # 强安全限制：兜底时，必须和当前进程 PID 关联的微信号目录一致
                        if entry_clean == target_wxid_clean or entry_clean == target_wxid_md5:
                            db_storage = os.path.join(base_dir, entry, "db_storage")
                            if os.path.isdir(db_storage):
                                all_db_storage_dirs.append(db_storage)
                                
            if all_db_storage_dirs:
                matched = match_db_storage_by_key(key, all_db_storage_dirs)
                return bool(matched)
    except Exception as e:
        logger.debug(f"[WCDB密钥] 校验 PID={pid} 的密钥有效性异常: {e}")
    return False
