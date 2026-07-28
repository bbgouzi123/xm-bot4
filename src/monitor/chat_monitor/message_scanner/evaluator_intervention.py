import logging
import time
import asyncio
from .utils import is_acknowledgement_message

logger = logging.getLogger(__name__)

class EvaluatorInterventionMixin:
    """人工干预接管挂起与异常群发/熔断避让规则"""

    def _check_manual_intervention_and_acknowledgement(self, name: str, last_msg: str, fp: str, reply_cfg: dict, account_id: str, unread_count: int = 0) -> bool:
        # 1. 优先检查是否处于永久人工接管模式
        if hasattr(self, "_human_takeover_sessions") and name in self._human_takeover_sessions:
            msg_text = "处于永久人工接管模式，已安全挂起并完全屏蔽自动回复"
            print(f"[监控] 会话 '{name}' {msg_text}")
            try:
                from src.utils.websocket_manager import ws_manager
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    asyncio.ensure_future(ws_manager.broadcast_task_update(
                        task_id=f"auto_reply_{name}",
                        task_type="自动回复",
                        status="completed",
                        progress=100,
                        total=100,
                        message=f"人工接管避让：{msg_text}",
                        friend_name=name,
                        incoming_msg=last_msg
                    ))
            except Exception:
                pass
            return False

        try:
            from src.utils.rest_time import get_rest_config
            rest_cfg = get_rest_config(account_id)
            suspend_secs = int(rest_cfg.get("manual_suspend_minutes", 30)) * 60
        except Exception:
            suspend_secs = 30 * 60

        last_intervention = self._manual_interventions.get(name, 0)
        if time.time() - last_intervention < suspend_secs:
            msg_text = f"处于人工干预挂起期间，距离上次干预 {(time.time() - last_intervention):.1f} 秒，跳过自动回复"
            print(f"[监控] 会话 '{name}' {msg_text}")
            try:
                from src.utils.websocket_manager import ws_manager
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    asyncio.ensure_future(ws_manager.broadcast_task_update(
                        task_id=f"auto_reply_{name}",
                        task_type="自动回复",
                        status="completed",
                        progress=100,
                        total=100,
                        message=f"人工干预避让：{msg_text}",
                        friend_name=name,
                        incoming_msg=last_msg
                    ))
            except Exception:
                pass
            return False

        try:
            ignore_whitelist = rest_cfg.get("ignore_reply_whitelist", False)
        except Exception:
            ignore_whitelist = False

        if ignore_whitelist and is_acknowledgement_message(last_msg):
            print(f"[监控] 会话 '{name}' 收到防爆免回复/结束语词汇 '{last_msg}'，跳过")
            try:
                from src.utils.websocket_manager import ws_manager
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    asyncio.ensure_future(ws_manager.broadcast_task_update(
                        task_id=f"auto_reply_{name}",
                        task_type="自动回复",
                        status="completed",
                        progress=100,
                        total=100,
                        message=f"收到防爆免回复词 '{last_msg}'，已自动忽略",
                        friend_name=name,
                        incoming_msg=last_msg
                    ))
            except Exception:
                pass
            self._fingerprints.setdefault(name, set()).add(fp)
            return False
            
        if any(last_msg.startswith(p) for p in self.SKIP_PREFIXES) and unread_count == 0:
            logger.info(f"[监控] 会话 '{name}' 消息以系统前缀开头且未读数为 0，跳过")
            self._fingerprints.setdefault(name, set()).add(fp)
            return False

        return True

    def _ignore_suspended_or_mass_sent(self, name: str, last_msg: str, fp: str):
        reason = "会话被安全隔离（熔断中）" if self.is_session_suspended(name) else "检测为群发/广播消息"
        print(f"[监控] 会话 '{name}' 被熔断或属于群发消息，跳过: {reason}")
        try:
            from src.utils.websocket_manager import ws_manager
            loop = asyncio.get_running_loop()
            if loop and loop.is_running():
                asyncio.ensure_future(ws_manager.broadcast_task_update(
                    task_id=f"auto_reply_{name}",
                    task_type="自动回复",
                    status="completed",
                    progress=100,
                    total=100,
                    message=f"安全隔离避让：{reason}，已忽略自动回复",
                    friend_name=name,
                    incoming_msg=last_msg
                ))
        except Exception:
            pass
        self._fingerprints.setdefault(name, set()).add(fp)
