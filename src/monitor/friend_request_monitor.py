import threading
import time
import logging
from datetime import datetime

from src.uia.driver import WeChatDriver
from src.uia.accept_friend import AcceptFriendEngine
from src.utils.rest_time import is_rest_time
from src.utils.daily_counter import DailyCounter
from . import friend_request_store

logger = logging.getLogger(__name__)

# 全局日计数器
_friend_counter = DailyCounter()


class FriendRequestMonitor:
    """自动通过新好友监控模块（对标 V2 FriendRequestMonitor）"""

    def __init__(self, driver: WeChatDriver, ai_service=None):
        self.driver = driver
        self.ai_service = ai_service
        self._running = False
        self._thread = None
        self._logs = friend_request_store.load_fr_logs()
        self._last_fallback_check_time = 0.0

    def add_log(self, nickname: str, status: str, message: str, sync_cloud: bool = True, extra: dict | None = None):
        entry = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "nickname": nickname, "status": status, "message": message}
        self._logs.insert(0, entry)
        if len(self._logs) > 1000: self._logs = self._logs[:1000]
        friend_request_store.save_fr_logs(self._logs)
        if sync_cloud:
            payload = {"target_name": nickname, "status": status, "message": message, "created_at": entry["time"]}
            if extra: payload.update(extra)
            friend_request_store.report_cloud_event(payload)

    def get_logs(self):
        return self._logs

    def toggle(self):
        self.stop() if self._running else self.start()
        return self._running

    def start(self):
        if self._running or not self.driver: return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("[好友通过] 自动通过新朋友监控已启动")

    def stop(self):
        self._running = False
        if self._thread: self._thread.join(timeout=2.0)
        logger.info("自动通过新朋友监控已停止")

    def is_running(self):
        return self._running

    def _is_system_idle(self, account_id: str) -> bool:
        from src.utils.uia_lock import uia_lock
        from src.orchestrator.ui_bus import ui_bus
        from src.utils.user_activity import is_user_active
        if uia_lock.is_busy: return False
        try:
            with ui_bus._queues_lock:
                q = ui_bus._queues.get(account_id)
                if q and len(q) > 0: return False
            if ui_bus._metrics.get("current_running_cmd") is not None: return False
            if is_user_active(): return False
        except Exception: pass
        return True

    def _loop(self):
        engine = AcceptFriendEngine(self.driver)
        from src.monitor.greeting_manager import GreetingManager
        greeter = GreetingManager(self.driver, self.ai_service)
        
        while self._running:
            try:
                # 未连接时等待，避免在微信启动中就尝试 UIA 操作
                if not self.driver.is_connected():
                    time.sleep(10)
                    continue

                # 优先从 driver 中读取 wxid
                account_id = getattr(self.driver, 'bot_wxid', None) or getattr(self.driver, '_wxid', None)
                if not account_id:
                    try:
                        from app.state import account_manager as am
                        if am and self.driver.hwnd in am._instances:
                            account_id = am._instances[self.driver.hwnd].wxid
                    except Exception:
                        pass

                if not account_id:
                    logger.debug(f"[好友通过] 警告: 当前实例 (hwnd={self.driver.hwnd}) 尚未就绪 (wxid为空)，跳过本轮巡检")
                    time.sleep(5)
                    continue

                # 获取欢迎语与新朋友配置
                welcome_msg = ""
                auto_accept_friend = False

                auto_invite_group = False
                invite_group_name = ""
                try:
                    from src.crm.account_data import get_account_settings
                    settings = get_account_settings(account_id)
                    reply_settings = settings.get("reply", {})
                    welcome_msg = reply_settings.get("welcome_msg", "")
                    auto_accept_friend = reply_settings.get("auto_accept_friend", False)
                    auto_follow = reply_settings.get("auto_follow", False)
                except Exception:
                    pass

                try:
                    from src.api.config_api import _load_configs
                    global_configs = _load_configs()
                    fr_settings = global_configs.get("friend_request_settings", {})
                    auto_accept_friend = auto_accept_friend or fr_settings.get("auto_accept", False)
                    auto_invite_group = fr_settings.get("auto_invite_group", False)
                    invite_group_name = fr_settings.get("invite_group_name", "")
                    
                    remark_enabled = fr_settings.get("auto_remark_enabled", False)
                    remark_template = fr_settings.get("auto_remark_template", "") if remark_enabled else ""
                    tags_enabled = fr_settings.get("auto_tag_enabled", False)
                    wechat_tags = fr_settings.get("auto_tags", []) if tags_enabled else []
                    
                    # 读取动态打标规则与微信朋友权限/屏蔽隐私控制配置
                    keyword_tag_rules = fr_settings.get("keyword_tag_rules", [])
                    permission_type = fr_settings.get("permission_type", "all")
                    hide_my_moments = fr_settings.get("hide_my_moments", False)
                    hide_his_moments = fr_settings.get("hide_his_moments", False)
                except Exception:
                    pass

                if not auto_accept_friend:
                    time.sleep(5)
                    continue

                if is_rest_time("friend_request", account_id):
                    time.sleep(15)
                    continue

                # 避让与防冲突：仅在系统与当前账号完全空闲时，才进行无障碍红点检测和后续处理
                if not self._is_system_idle(account_id):
                    time.sleep(5)
                    continue

                # 检查通讯录是否有未读好友申请 (非侵入式，极其安静，不干扰用户)
                unread_count = 0
                try:
                    if hasattr(self.driver, "_check_contacts_unread"):
                        unread_count = self.driver._check_contacts_unread()
                except Exception as unread_ex:
                    logger.debug(f"[好友通过][{account_id}] 检查通讯录未读数异常: {unread_ex}")

                import time as time_mod
                now = time_mod.time()
                last_fallback = getattr(self, "_last_fallback_check_time", 0.0)
                # 当且仅当有新申请红点，或达到 30 分钟静默空闲兜底时间，才触发一次物理自动通过逻辑
                should_check = (unread_count > 0) or (now - last_fallback > 1800.0)

                if not should_check:
                    # 无未读且未到兜底时间，安静睡眠 5 秒，绝不频繁切微信主界面或执行 action 造成干扰
                    time.sleep(5)
                    continue

                self._last_fallback_check_time = now

                # 打印诊断日志，精准确认运行上下文
                logger.info(f"[好友通过][{account_id}] 检测到未读好友申请或达到兜底时间: unread={unread_count}, "
                            f"距上次巡检={int(now - last_fallback)}s, hwnd={self.driver.hwnd}")

                # 广播到自动化控制中心以反映我们的感知
                if unread_count > 0:
                    try:
                        from src.utils.websocket_manager import ws_manager
                        import asyncio
                        loop = None
                        try:
                            import app.state as app_state
                            if hasattr(app_state, "main_loop") and app_state.main_loop:
                                loop = app_state.main_loop
                        except Exception:
                            pass
                        if not loop:
                            try:
                                loop = asyncio.get_running_loop()
                            except RuntimeError:
                                pass
                        
                        coro = ws_manager.broadcast_task_update(
                            task_id="auto_accept_friend",
                            task_type="自动通过好友",
                            status="running",
                            progress=5,
                            total=100,
                            message=f"[{account_id}] 检测到通讯录有 {unread_count} 个新好友申请，准备进入自动化同意流程...",
                            friend_name="新朋友",
                            incoming_msg=f"未读好友申请数量: {unread_count}"
                        )
                        if loop and loop.is_running():
                            asyncio.run_coroutine_threadsafe(coro, loop)
                    except Exception as ws_ex:
                        logger.warning(f"[好友通过][{account_id}] 广播好友监控任务状态异常: {ws_ex}")

                try:
                    self.driver._ensure_chat_page()
                    time.sleep(0.5)
                except:
                    pass

                if not _friend_counter.can_do("add_friend", account_id):
                    logger.info(f"[防封][{account_id}] 今日自动通过好友已达上限，暂停")
                    time.sleep(300)
                    continue

                accepted_list = None
                bus_used = False
                try:
                    from src.orchestrator.ui_bus import (
                        ui_bus,
                        UICommand,
                        UICommandKind,
                        UICommandPriority,
                        UICommandStatus,
                    )
                    cmd_af = UICommand(
                        wxid=account_id,
                        kind=UICommandKind.ACCEPT_FRIEND,
                        payload={
                            "remark_template": remark_template,
                            "wechat_tags": wechat_tags,
                            "keyword_tag_rules": keyword_tag_rules,
                            "permission_type": permission_type,
                            "hide_my_moments": hide_my_moments,
                            "hide_his_moments": hide_his_moments
                        },
                        priority=UICommandPriority.NORMAL,
                        timeout=120.0,
                    )
                    ui_bus.submit(cmd_af)
                    finished = ui_bus.await_result(cmd_af.id, timeout=180.0)
                    if finished.status == UICommandStatus.SUCCESS:
                        accepted_list = finished.result
                        bus_used = True
                    else:
                        logger.warning(
                            f"[好友通过][{account_id}][UIBus] 回退直执行: "
                            f"status={finished.status.value} err={finished.error}"
                        )
                except Exception as e:
                    logger.warning(f"[好友通过][{account_id}][UIBus] 投递异常，回退直执行: {e}")

                if not bus_used:
                    logger.info(f"[好友通过][{account_id}] UIBus 未处理，回退直接使用实例 engine 执行 (hwnd={self.driver.hwnd})")
                    accepted_list = engine.accept_all(
                        remark_template=remark_template,
                        tags=wechat_tags,
                        keyword_tag_rules=keyword_tag_rules,
                        permission_type=permission_type,
                        hide_my_moments=hide_my_moments,
                        hide_his_moments=hide_his_moments
                    )
                if accepted_list:
                    for friend in accepted_list:
                        nick = friend.get("nickname", "未知")
                        self.add_log(nick, "accepted", "已自动通过请求", sync_cloud=False)
                        friend_request_store.report_cloud_event({
                            "target_name": nick,
                            "target_wxid": friend.get("wxid", ""),
                            "verify_message": friend.get("verify_message", ""),
                            "status": "accepted",
                        })

                        # 投递 new_friend 事件给客户 API 适配器
                        try:
                            from src.api.customer_api.adapter_factory import submit_event
                            submit_event("new_friend", {
                                "account_id": account_id,
                                "target_wxid": friend.get("wxid", ""),
                                "nickname": nick,
                                "verify_message": friend.get("verify_message", ""),
                                "timestamp": int(time.time())
                            })
                        except Exception as ce:
                            logger.error(f"[客户API][{account_id}] 投递新好友通过事件异常: {ce}")

                        _friend_counter.increment("add_friend", account_id)

                        # CRM 画像与跟单创建（委托给 store 模块）
                        tag_summary = friend_request_store.create_new_friend_profile(nick, friend)
                        if tag_summary:
                            self.add_log(nick, "profiled", f"画像已创建: {tag_summary}")
                        
                        # 发送欢迎语
                        if welcome_msg and nick and nick != "未知":
                            success = greeter.send_greeting_sync(nick, welcome_msg, friend.get("wxid"))
                            if success:
                                self.add_log(nick, "greeted", f"已发送欢迎语")
                            else:
                                self.add_log(nick, "error", "发送欢迎语失败", sync_cloud=False)
                                friend_request_store.report_cloud_event({
                                    "target_name": nick,
                                    "status": "error",
                                    "stage": "greeting",
                                    "error": "发送欢迎语失败",
                                })

                        # SDR 自动挂载
                        if auto_follow:
                            scenario = friend_request_store.auto_enroll_sdr(friend.get("wxid"), nick, friend.get("verify_message", ""))
                            if scenario:
                                self.add_log(nick, "sdr_enrolled", f"已自动开启SDR长程跟单: {scenario}")

                        # 自动邀群
                        if auto_invite_group and invite_group_name and nick and nick != "未知":
                            time.sleep(2)
                            invite_success = self.driver.invite_friend_to_group(invite_group_name, nick)
                            if invite_success:
                                self.add_log(nick, "invited", f"已自动邀入群聊 '{invite_group_name}'")
                                friend_request_store.report_cloud_event({
                                    "target_name": nick,
                                    "status": "success",
                                    "stage": "invite_group",
                                    "message": f"已自动邀入群聊 '{invite_group_name}'",
                                })
                            else:
                                self.add_log(nick, "error", f"邀请入群 '{invite_group_name}' 失败", sync_cloud=False)
                                friend_request_store.report_cloud_event({
                                    "target_name": nick,
                                    "status": "error",
                                    "stage": "invite_group",
                                    "error": f"邀请入群 '{invite_group_name}' 失败",
                                })

                        time.sleep(2)
                        
            except Exception as e:
                logger.error(f"[好友通过] 监控异常: {e}")
                friend_request_store.report_cloud_event({
                    "status": "error",
                    "stage": "monitor_loop",
                    "error": str(e),
                })
                
            for _ in range(15):
                if not self._running:
                    break
                time.sleep(1)
