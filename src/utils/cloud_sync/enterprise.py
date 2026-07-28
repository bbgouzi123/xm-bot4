"""
企业管理后台集成 Mixin（Boss Dashboard 员工端）

职责：
- 审计事件上报：自动收集关键操作并上报到企业审计日志
- 命令轮询：定时拉取老板下发的远程控制命令并执行
- 命令确认：将执行结果回报给同步后端

API 路径：
- POST /api/v1/enterprise/audit          — 批量上报审计事件
- GET  /api/v1/enterprise/commands/pending — 拉取待执行命令
- POST /api/v1/enterprise/commands/{id}/ack — 确认命令执行结果
"""
import logging
import platform
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class CloudSyncEnterpriseMixin:
    """企业管理后台集成 Mixin：审计上报 + 命令轮询"""

    _enterprise_running = False
    _enterprise_thread: Optional[threading.Thread] = None

    # ──────────────────────────────────────────────────
    # 审计事件上报
    # ──────────────────────────────────────────────────

    def report_audit_event(
        self,
        action: str,
        target: str = "",
        detail: dict = None,
        risk_level: str = "normal",
    ) -> bool:
        """
        上报一条审计事件到企业安全审计日志。

        Args:
            action: 操作类型，如 login / auto_reply / add_friend / moment_post
            target: 操作对象，如 wxid / 好友昵称 / 群名
            detail: 详情字典
            risk_level: 风险级别 normal / warning / critical
        """
        if getattr(self, "_enterprise_forbidden", False):
            return False
        try:
            user_id = self._resolve_user_id()
            if not user_id:
                return False

            event = {
                "user_id": user_id,
                "action": action,
                "target": target or "",
                "detail": detail or {},
                "risk_level": risk_level,
                "ip_address": self._get_local_ip(),
                "device": platform.node() or "unknown",
            }

            result = self._post(
                "/api/v1/enterprise/audit",
                {"events": [event]},
                need_auth=True,
            )
            if getattr(self, "last_status_code", None) == 403:
                logger.info("[企业审计] 接收到 403 权限不足，已自动挂起企业审计上报")
                self._enterprise_forbidden = True
                return False

            if result is not None:
                logger.debug(f"[企业审计] 上报成功: {action} → {target}")
                return True
            return False
        except Exception as e:
            logger.debug(f"[企业审计] 上报失败: {e}")
            return False

    def report_audit_batch(self, events: list) -> int:
        """批量上报审计事件（每条需包含 action 字段）。"""
        if getattr(self, "_enterprise_forbidden", False):
            return 0
        if not events:
            return 0
        try:
            user_id = self._resolve_user_id()
            if not user_id:
                return 0

            ip = self._get_local_ip()
            device = platform.node() or "unknown"
            payload = []
            for ev in events:
                payload.append({
                    "user_id": user_id,
                    "action": ev.get("action", "unknown"),
                    "target": ev.get("target", ""),
                    "detail": ev.get("detail", {}),
                    "risk_level": ev.get("risk_level", "normal"),
                    "ip_address": ip,
                    "device": device,
                })

            result = self._post(
                "/api/v1/enterprise/audit",
                {"events": payload},
                need_auth=True,
            )
            if getattr(self, "last_status_code", None) == 403:
                logger.info("[企业审计] 批量上报接收到 403 权限不足，已自动挂起企业审计上报")
                self._enterprise_forbidden = True
                return 0

            if result is not None:
                recorded = result.get("recorded", 0) if isinstance(result, dict) else len(payload)
                logger.info(f"[企业审计] 批量上报: {recorded}/{len(payload)}")
                return recorded
            return 0
        except Exception as e:
            logger.warning(f"[企业审计] 批量上报失败: {e}")
            return 0

    # ──────────────────────────────────────────────────
    # 命令轮询与执行
    # ──────────────────────────────────────────────────

    def start_enterprise_command_poller(self, interval: int = 30):
        """
        启动企业命令轮询后台线程。

        每隔 interval 秒拉取一次 pending 命令并执行。
        """
        if self._enterprise_running:
            logger.debug("[企业命令] 轮询线程已在运行")
            return

        self._enterprise_running = True
        self._enterprise_thread = threading.Thread(
            target=self._enterprise_poller_worker,
            args=(interval,),
            name="enterprise-cmd-poller",
            daemon=True,
        )
        self._enterprise_thread.start()
        logger.info(f"[企业命令] 🚀 命令轮询启动（间隔 {interval}s）")

    def stop_enterprise_command_poller(self):
        """停止企业命令轮询"""
        self._enterprise_running = False
        logger.info("[企业命令] 轮询已停止")

    def _enterprise_poller_worker(self, interval: int):
        """后台轮询线程主循环"""
        # 启动后等待 2 秒，让主初始化完成
        time.sleep(2)
        while self._enterprise_running:
            try:
                self._poll_and_execute_commands()
            except Exception as e:
                logger.warning(f"[企业命令] 轮询异常: {e}")
            time.sleep(interval)

    def _poll_and_execute_commands(self):
        """拉取待执行命令并逐条执行"""
        if getattr(self, "_enterprise_forbidden", False):
            return
        commands = self._get(
            "/api/v1/enterprise/commands/pending",
            need_auth=True,
        )
        if getattr(self, "last_status_code", None) == 403:
            logger.info("[企业命令] 接收到 403 权限不足，已自动挂起企业命令轮询")
            self._enterprise_forbidden = True
            return
        if not commands or not isinstance(commands, list):
            return

        logger.info(f"[企业命令] 📥 收到 {len(commands)} 条待执行命令")

        for cmd in commands:
            cmd_id = cmd.get("id")
            cmd_type = cmd.get("command_type", "")
            payload = cmd.get("command_payload", {})

            logger.info(f"[企业命令] ▶ 执行: id={cmd_id}, type={cmd_type}")

            try:
                from .enterprise_commands import execute_enterprise_command
                success, result = execute_enterprise_command(self, cmd_type, payload)
                status = "executed" if success else "failed"
                self._ack_command(cmd_id, status, result)
            except Exception as e:
                logger.error(f"[企业命令] 执行失败: {e}")
                self._ack_command(cmd_id, "failed", {"error": str(e)})

    def _ack_command(self, cmd_id: int, status: str, result: dict = None):
        """向同步后端确认命令执行结果"""
        try:
            self._post(
                f"/api/v1/enterprise/commands/{cmd_id}/ack",
                {"status": status, "result": result or {}},
                need_auth=True,
            )
            logger.info(f"[企业命令] ✅ 确认: cmd_id={cmd_id}, status={status}")
        except Exception as e:
            logger.warning(f"[企业命令] 确认失败: {e}")

    # ──────────────────────────────────────────────────
    # 工具方法
    # ──────────────────────────────────────────────────

    def _resolve_user_id(self) -> Optional[str]:
        """获取当前 SSO 用户 ID"""
        try:
            from src.sso_bridge import read_sso_session
            session = read_sso_session()
            if session:
                user = session.get("user", {})
                return user.get("id")
        except Exception:
            pass
        # 兜底从 JWT 中解析
        try:
            payload = self._decode_token_sub(self.jwt_token)
            return payload
        except Exception:
            return None

    @staticmethod
    def _get_local_ip() -> str:
        """获取本机 IP"""
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
