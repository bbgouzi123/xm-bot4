import logging
import asyncio
import threading
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

def trigger_bonus_notification(
    plan_code: str,
    is_valid: bool,
    db,
    trial_bonus_sent: bool,
    base_bonus_sent: bool,
    professional_bonus_sent: bool,
    flagship_bonus_sent: bool
) -> Tuple[bool, bool, bool, bool]:
    """判定并异步触发不同订阅等级的专属额外福利赠送通知，并持久化更新发送标记。"""
    try:
        title = ""
        body = ""
        
        if plan_code == 'trial':
            if not trial_bonus_sent:
                trial_bonus_sent = True
                if db:
                    db.trial_bonus_sent = True
                    db._persist_snapshot()
                title = "🎉 试用版专属福利已送达！"
                body = "欢迎体验试用版！系统已为您自动赠送试用行业对应的销冠包话术服务。您可在【聊天知识库】的销冠共享市场中导入、启用专属成交话术！"
        elif plan_code == 'base':
            if not base_bonus_sent:
                base_bonus_sent = True
                if db:
                    db.base_bonus_sent = True
                    db._persist_snapshot()
                title = "🎉 基础版专属福利已送达！"
                body = "恭喜您升级至基础版！系统已为您自动赠送开通时选择的对应行业配置及销冠包话术服务。您可在【聊天知识库】的销冠共享市场中导入、启用，助您快速开启智能助理跟单服务！"
        elif plan_code == 'professional':
            if not professional_bonus_sent:
                professional_bonus_sent = True
                if db:
                    db.professional_bonus_sent = True
                    db._persist_snapshot()
                title = "🎉 专业版专属福利已送达！"
                body = "恭喜您升级至专业版！系统已为您自动赠送开通时选择行业对应的销冠包话术服务。您可在【聊天知识库】的销冠共享市场中一键导入、启用，助您跟单率大幅提升！"
        elif plan_code == 'flagship':
            if not flagship_bonus_sent:
                flagship_bonus_sent = True
                if db:
                    db.flagship_bonus_sent = True
                    db._persist_snapshot()
                title = "🎉 旗舰版专属福利已送达！"
                body = "恭喜您升级至尊贵旗舰版！系统已为您自动赠送价值 699 元的全部行业销冠包话术服务。您可在【聊天知识库】的销冠共享市场中任意免费导入、启用所有行业的成交话术，助您成单率飞跃提升！"

        if title and body:
            # 异步触发系统消息发送，避免阻塞主线程
            from src.utils.alert_notifier import alert_notifier
            
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(alert_notifier.send_user_notification(
                        title=title,
                        body=body,
                        category="system"
                    ))
                else:
                    loop.run_until_complete(alert_notifier.send_user_notification(
                        title=title,
                        body=body,
                        category="system"
                    ))
            except Exception:
                threading.Thread(target=lambda: asyncio.run(
                    alert_notifier.send_user_notification(
                        title=title,
                        body=body,
                        category="system"
                    )
                ), daemon=True).start()
    except Exception as e:
        logger.error(f"[订阅] 处理订阅额外福利赠送通知异常: {e}")

    return trial_bonus_sent, base_bonus_sent, professional_bonus_sent, flagship_bonus_sent
