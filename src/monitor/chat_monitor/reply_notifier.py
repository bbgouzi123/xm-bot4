import logging
from typing import Any
from src.utils.uia_task_runner import run_uia_with_timeout

logger = logging.getLogger(__name__)

async def send_high_intent_alerts(engine: Any, name: str, user_name: str, actual_message: str, intent: str, account_id: str):
    """发送高意向客户的相关外部通知 (微信、飞书、邮件)"""
    try:
        from datetime import datetime
        alert_msg = (
            f"🎯 发现意向客户通知\n"
            f"客户姓名: {user_name or name} ({name})\n"
            f"意向标签: {intent}\n"
            f"最新消息: {actual_message}\n"
            f"微信号: {account_id}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        # 优先读取隔离配置里的决策管理员微信号
        from src.api.config_api.privacy_shield import _get_reply_config_isolated
        reply_cfg = _get_reply_config_isolated(account_id)
        receiver = reply_cfg.get("delegated_admin_wxid", "").strip() or "文件传输助手"

        await run_uia_with_timeout(engine.driver.send_message, 15.0, receiver, alert_msg)
        logger.info(f"[意向通知] 微信通知成功: 对象={receiver}, 客户={name}")
    except Exception as we:
        logger.error(f"[意向通知] 微信发送失败: {we}")

    try:
        from src.api.config_api.base_config import _load_configs
        configs = _load_configs()

        fs_cfg = configs.get("alert_feishu_settings", {})
        if fs_cfg.get("enabled", False) and fs_cfg.get("webhook_url", "").strip():
            from src.utils.feishu_notifier import feishu_notifier
            fs_title = "🎯 发现意向客户通知"
            fs_content = (
                f"**微信号**: {account_id}\n"
                f"**客户姓名**: {user_name or name} ({name})\n"
                f"**意向标签**: {intent}\n"
                f"**最新消息**: {actual_message}\n"
                f"请及时跟进！"
            )
            await feishu_notifier.send_alert_card(fs_title, fs_content, level="info")
            logger.info(f"[意向通知] 飞书通道通知成功: {name}")

        email_cfg = configs.get("alert_email_settings", {})
        if email_cfg.get("enabled", False) and email_cfg.get("receiver_email", "").strip():
            from src.utils.alert_notifier import alert_notifier
            reason_content = (
                f"客户姓名: {user_name or name} ({name})\n"
                f"意向标签: {intent}\n"
                f"最新消息: {actual_message}"
            )
            await alert_notifier.send_alert_email(
                machine_code="XM-BOT4-CLIENT",
                account_id=account_id,
                title="🎯 发现意向客户通知",
                reason=reason_content
            )
            logger.info(f"[意向通知] 邮箱通道通知成功: {name}")
    except Exception as ne:
        logger.error(f"[意向通知] 外部推送通道异常: {ne}")


async def send_transfer_to_manual_alert(engine: Any, name: str, user_name: str, actual_message: str, account_id: str):
    """发送转人工通知，通知备用微信 + 前端声音提醒 + 外部告警"""
    try:
        from datetime import datetime
        alert_msg = (
            f"🚨 客户请求转人工通知\n"
            f"客户姓名: {user_name or name} ({name})\n"
            f"最新消息: {actual_message}\n"
            f"微信号: {account_id}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        # 获取备用微信
        from src.api.config_api.base_config import _load_configs
        configs = {}
        try:
            configs = _load_configs()
        except Exception:
            pass
        
        # 优先读取隔离配置里的决策管理员微信号
        from src.api.config_api.privacy_shield import _get_reply_config_isolated
        reply_cfg = _get_reply_config_isolated(account_id)
        admin_wxid = reply_cfg.get("delegated_admin_wxid", "").strip()

        wechat_alert_cfg = configs.get("alert_wechat_settings", {})
        receiver = admin_wxid or wechat_alert_cfg.get("receiver_contact", "").strip() or "文件传输助手"
        
        await run_uia_with_timeout(engine.driver.send_message, 15.0, receiver, alert_msg)
        logger.info(f"[转人工通知] 微信通知 {receiver} 发送成功: {name}")
    except Exception as we:
        logger.error(f"[转人工通知] 微信通知发送失败: {we}")

    # 前端声音提醒 (通过 WebSocket broadcast_alert)
    try:
        from src.utils.websocket_manager import ws_manager
        ws_content = f"客户 {user_name or name} ({name}) 发起转人工请求，消息内容：{actual_message}"
        await ws_manager.broadcast_alert(level="warning", title="🚨 客户请求转人工", content=ws_content)
        logger.info(f"[转人工通知] WebSocket 声音及警报广播成功")
    except Exception as wse:
        logger.error(f"[转人工通知] WebSocket 广播失败: {wse}")

    try:
        # 同时推送飞书/邮件
        if configs:
            fs_cfg = configs.get("alert_feishu_settings", {})
            if fs_cfg.get("enabled", False) and fs_cfg.get("webhook_url", "").strip():
                from src.utils.feishu_notifier import feishu_notifier
                fs_title = "🚨 客户请求转人工通知"
                fs_content = (
                    f"**微信号**: {account_id}\n"
                    f"**客户姓名**: {user_name or name} ({name})\n"
                    f"**最新消息**: {actual_message}\n"
                    f"请及时人工接管！"
                )
                await feishu_notifier.send_alert_card(fs_title, fs_content, level="warning")
                logger.info(f"[转人工通知] 飞书通道通知成功: {name}")

            email_cfg = configs.get("alert_email_settings", {})
            if email_cfg.get("enabled", False) and email_cfg.get("receiver_email", "").strip():
                from src.utils.alert_notifier import alert_notifier
                reason_content = (
                    f"客户姓名: {user_name or name} ({name})\n"
                    f"最新消息: {actual_message}"
                )
                await alert_notifier.send_alert_email(
                    machine_code="XM-BOT4-CLIENT",
                    account_id=account_id,
                    title="🚨 客户请求转人工通知",
                    reason=reason_content
                )
                logger.info(f"[转人工通知] 邮箱通道通知成功: {name}")
    except Exception as ne:
        logger.error(f"[转人工通知] 外部推送通道异常: {ne}")

