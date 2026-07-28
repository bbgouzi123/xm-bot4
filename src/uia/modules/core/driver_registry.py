"""UIA 驱动注册表 — 提供全局 driver 实例的统一访问入口。

被 uia_task_runner 的白屏预检（_pre_check_uia_health）使用，
避免预检逻辑直接依赖 app.state（防止循环导入）。
"""

def get_primary_driver():
    """获取当前主 WeChatDriver 实例（可能为 None）。

    优先从 MultiAccountManager 获取（多开场景），
    回退到全局单例 driver（单开场景）。
    """
    try:
        from app.state import account_manager
        drv = account_manager.primary_driver
        if drv and drv.is_connected():
            return drv
    except Exception:
        pass

    try:
        from app.state import driver
        if driver and driver.is_connected():
            return driver
    except Exception:
        pass

    return None
