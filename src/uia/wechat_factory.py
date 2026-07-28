"""
微信驱动工厂（移植自 xm-bot4 core/wechat_factory.py — 23行完整反编译）

原始文件: core/wechat_factory.py (COMPLETE, 23 lines)
根据微信版本创建对应的驱动实例。
"""
import os
from typing import Optional
from src.uia.version_detector import detect_version, WeChatVersion


def create_driver(window_handle: int = None):
    """Create a driver instance based on env override or detected version.

    Returns an instance of a driver exposing `initialize` and `initialize_multi`.
    """
    version = detect_version(window_handle)
    
    # Check if enhanced 4x driver is enabled
    use_enhanced = os.environ.get("WECHAT_ENHANCED_4X", "0") == "1"
    if not use_enhanced:
        try:
            from src.utils.config_cache import config_cache
            use_enhanced = config_cache.get("enable_enhanced_4x", False)
        except Exception:
            pass

    if use_enhanced:
        print('[工厂] 正在初始化 4.1.7 (新 NT 架构) 数据库解密/Hook 增强型驱动 WeChat4xDriver...')
        try:
            from src.wechat_4x.driver import WeChat4xDriver
            
            scheme = os.environ.get("WECHAT_4X_SCHEME", "A")
            db_path = os.environ.get("WECHAT_4X_DB_PATH", "")
            key_hex = os.environ.get("WECHAT_4X_KEY_HEX", "")
            ws_port = int(os.environ.get("WECHAT_4X_WS_PORT", "9001"))
            
            driver = WeChat4xDriver(scheme=scheme, db_path=db_path, key_hex=key_hex, ws_port=ws_port)
            if driver.initialize_4x():
                return driver
            else:
                print('[工厂] 增强型驱动通道初始化失败，降级回退到标准 UIA 驱动')
        except Exception as e:
            print(f'[工厂] 增强型驱动导入或启动异常: {e}，降级回退到标准 UIA 驱动')

    from src.uia.driver import WeChatDriver
    return WeChatDriver()

