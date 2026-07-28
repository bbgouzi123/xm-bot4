"""
xm-core · 跨产品 SSO 文件共享模块 (xm-bot4 本地桥接)

此文件代理了公共服务包中统一的 `xm_py_server.sso_bridge` 实现，
并挂载了本产品专用的“新用户旗舰版体验礼包”业务回调，保持向后兼容性。
"""

import os
from xm_py_server.sso_bridge import (
    read_sso_session,
    write_sso_session,
    clear_sso_session,
    register_on_write_session,
    _sso_file_path,
    refresh_sso_token,
)


def _async_claim_trial_callback(access_token: str, refresh_token: str, new_acc: dict):
    """
    本地专属逻辑：向 xm-user 上报机器码，领取新用户旗舰版 3 天体验礼包。
    """
    import threading

    def _do_claim():
        try:
            import logging
            logger = logging.getLogger(__name__)

            from src.utils.license_validator.machine import MachineMixin
            from src.utils.license_validator.env import license_client

            app_key = os.environ.get("SA_APP_KEY", "xm-bot4-python")

            machine_code = MachineMixin.get_machine_code()
            result = license_client.post("/api/v1/auth/claim-trial", {
                "app_key": app_key,
                "machine_code": machine_code,
                "access_token": access_token,
            })
            if result:
                outcome = result.get("data", {}).get("result", "unknown")
                if outcome == "granted":
                    logger.info("[新用户礼包] 🎉 旗舰版 3 天体验礼包已开通！")
                elif outcome == "already_claimed":
                    logger.debug("[新用户礼包] 该机器已领取过，跳过")
                else:
                    logger.debug(f"[新用户礼包] 服务端响应: {outcome}")
        except Exception as ex:
            import logging
            logging.getLogger(__name__).warning(f"[新用户礼包] 上报失败（不影响登录）: {ex}")

    threading.Thread(target=_do_claim, daemon=True, name="claim-trial").start()


# 注册本地业务专属的回调到全局 SSO 写入事件中
register_on_write_session(_async_claim_trial_callback)
