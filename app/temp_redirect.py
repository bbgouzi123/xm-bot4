import os
import sys

# 💡 重定向打包运行环境下的临时缓存文件夹，避开管家类清理工具对 Temp 目录的 DLL 误删
if getattr(sys, 'frozen', False):
    import time
    import shutil
    import ctypes
    
    _temp_dir = os.path.join(os.environ.get('PUBLIC', 'C:\\Users\\Public'), 'xm_bot_temp')
    
    # 自动清理旧的 _MEI 临时目录以防磁盘膨胀 (保留当前和最近 24 小时内的)
    if os.path.isdir(_temp_dir):
        try:
            _now = time.time()
            _current_meipass = os.path.abspath(getattr(sys, '_MEIPASS', ''))
            for _item in os.listdir(_temp_dir):
                if _item.startswith('_MEI'):
                    _item_path = os.path.join(_temp_dir, _item)
                    if os.path.isdir(_item_path):
                        _abs_path = os.path.abspath(_item_path)
                        if _current_meipass and _abs_path == _current_meipass:
                            continue
                        if _now - os.path.getmtime(_item_path) > 86400:
                            shutil.rmtree(_item_path, ignore_errors=True)
        except Exception:
            pass

    try:
        os.makedirs(_temp_dir, exist_ok=True)
        os.environ['TEMP'] = _temp_dir
        os.environ['TMP'] = _temp_dir
        ctypes.windll.kernel32.SetEnvironmentVariableW('TEMP', _temp_dir)
        ctypes.windll.kernel32.SetEnvironmentVariableW('TMP', _temp_dir)
    except Exception:
        pass
