import time as _time
from datetime import datetime
import asyncio

_last_coze_activate_date = None

def periodic_tasks_loop():
    """后台轮询的定时任务，每隔 5 分钟执行一次云端配置与积分同步"""
    _time.sleep(60)
    global _last_coze_activate_date
    while True:
        try:
            from src.crm.profile_manager import ProfileManager
            ProfileManager().sync_all_to_cloud()
            from src.utils.cloud_sync import get_cloud_client
            from src.crm.account_data import get_active_account
            get_cloud_client().report_usage(get_active_account() or 'main')
        except Exception: 
            pass

        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            from src.api.config_api import _load_configs
            configs = _load_configs()
            
            if configs and configs.get("coze_auto_login") and configs.get("coze_cookie"):
                if _last_coze_activate_date != today_str:
                    from src.utils.coze_auth_helper import auto_activate_coze
                    res = asyncio.run(auto_activate_coze(configs.get("coze_cookie")))
                    if res and res.get("success"):
                        _last_coze_activate_date = today_str
                        print(f"[Coze 定时激活] 成功获取/维持今日 Coze 积分: {today_str}")
        except Exception as e:
            print(f"[Coze 定时激活] 自动轮询激活失败: {e}")

        _time.sleep(300)
