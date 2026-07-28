import asyncio
import time
import logging
from typing import Dict, Set, Optional, List
try:
    from src.uia.driver import WeChatDriver
except ImportError:
    WeChatDriver = None
from src.ai.base import AIServiceBase
from src.utils.daily_counter import DailyCounter
from .message_db import FingerprintsDict, LastReplyTimeDict
from .session_lock_helper import SessionLockMixin

logger = logging.getLogger(__name__)
_chat_daily_counter = DailyCounter()

class ChatMonitorBase(SessionLockMixin):
    """聊天监控器基础状态与生命周期"""
    
    def __init__(self, driver: WeChatDriver, ai_service: AIServiceBase):
        self.driver = driver
        if driver:
            driver._chat_monitor = self
        self.ai_service = ai_service
        self.persona: Optional[dict] = None

        # 运行控制
        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None
        self._check_interval = 0.8  # 优化：从 2s 降至 0.8s，加速 UIA 降级下的未读检测响应
        self._cooldown = 10  # 同一好友回复后的冷却期（10秒，竞品标准）

        # V3 分区缓存
        self._partitions: Dict[str, 'AccountPartition'] = {}
        self._mass_sending_cache: Dict[str, dict] = {}

        # V2 兼容的指纹去重
        self._fingerprints = FingerprintsDict(self)
        self._last_seen_msg: Dict[str, str] = {}
        self._manual_interventions: Dict[str, float] = {}
        self._human_takeover_sessions: Set[str] = set()
        self._intervention_cooldown = 300
        self._suspicious_pending: Dict[str, dict] = {}
        self._last_unread_snapshot: Dict[str, int] = {}
        self._last_reply_time = LastReplyTimeDict(self)
        self._initialized: Set[str] = set()
        self._processing: Set[str] = set()
        self._message_buffer: Dict[str, dict] = {}
        self._peek_attempts: Dict[str, int] = {}
        self._startup_checked = False
        self._first_scan_cycle_done = False

        # WCDB 双引擎协调器（可选，DLL 不存在时静默跳过）
        self._wcdb_session_monitor = None
        # 永久失败熔断标志：密钥已被 KeyStore 清理且 Hook 注入也失败，停止后续所有重试
        self._wcdb_key_failed_permanently = False

        # 配置
        self._whitelist_enabled = False
        self._whitelist: List[str] = []
        self._group_at_only = True
        self._colleague_names: List[str] = []

        # CRM 系统初始化
        try:
            import win32gui
        except ImportError:
            win32gui = None

        if win32gui is None:
            self._tag_sync = None
        else:
            from src.uia.tag_sync import WeChatTagSync
            self._tag_sync = WeChatTagSync(driver)
        self._chat_round_counter: Dict[str, int] = {}
        self._tag_sync_interval = 5
        self._tag_syncing = False
        self._stats = {
            "detected": 0,
            "replied": 0,
            "skipped": 0,
            "errors": 0,
            "start_time": None,
            "tags_extracted": 0,
        }

        # 将当前监控实例注册到 MessageScannerLogic 的全局实例列表中，以便被外部通知并清理缓存/指纹
        try:
            from src.monitor.chat_monitor.message_scanner import MessageScannerLogic
            if not hasattr(MessageScannerLogic, "_all_scanner_instances"):
                MessageScannerLogic._all_scanner_instances = []
            MessageScannerLogic._all_scanner_instances.append(self)
        except Exception:
            pass

    @property
    def account_id(self) -> str:
        """获取当前监控实例对应的微信号 ID"""
        wxid = getattr(self.driver, 'bot_wxid', None) or getattr(self.driver, '_wxid', None)
        if not wxid or wxid == 'default':
            from src.crm.account_data import get_active_account
            active = get_active_account()
            if active and active != 'default':
                return active
        return wxid or 'default'


    @property
    def db(self):
        """延迟加载并获取当前账号隔离的消息数据库"""
        if not hasattr(self, "_message_db_instances"):
            self._message_db_instances = {}
        aid = self.account_id
        if aid not in self._message_db_instances:
            from .message_db import MessageDatabase
            self._message_db_instances[aid] = MessageDatabase(aid)
        return self._message_db_instances[aid]

    @property
    def _profile_manager(self):
        """延迟加载当前账号独占的画像管理器，实现多开数据隔离"""
        from src.crm.profile_manager import ProfileManager
        return ProfileManager(account_id=self.account_id)

    @property
    def _industry_config(self):
        """延迟加载当前账号独占的行业配置管理器，实现多开数据隔离"""
        from src.crm.industry_config import IndustryConfigManager
        return IndustryConfigManager(account_id=self.account_id)

    def reset_session_caches(self):
        """清空所有跟微信号会话相关的缓存，用于账号切换或重新连接的场景"""
        self._initialized.clear()
        self._fingerprints.clear()
        self._last_seen_msg.clear()
        self._suspicious_pending.clear()
        self._message_buffer.clear()
        self._last_reply_time.clear()
        self._last_unread_snapshot.clear()
        self._processing.clear()
        self._peek_attempts.clear()
        self._startup_checked = False
        self._first_scan_cycle_done = False
        if hasattr(self, '_preheated_accounts'):
            self._preheated_accounts.clear()
        if hasattr(self, '_account_wait_counts'):
            self._account_wait_counts.clear()
        for partition in self._partitions.values():
            partition.suspended_sessions.clear()
        if hasattr(self, '_wcdb_session_monitor') and self._wcdb_session_monitor:
            try:
                self._wcdb_session_monitor.stop()
            except Exception:
                pass
            self._wcdb_session_monitor = None
        logger.info(f"[ChatMonitor] 已重置当前监控实例的会话缓存 ({self.account_id})")



    async def start(self):
        if self._running:
            logger.info('聊天监控器已在运行')
            return

        # 强制清空当前驱动器的旧账号信息，防止重连后内存遗留产生解密和缓存污染
        if hasattr(self, 'driver') and self.driver:
            # 只有在未连接或者没有有效 wxid 时才重置，避免抹除已提取的有效微信号
            if not self.driver.is_connected() or not (getattr(self.driver, '_wxid', '') or getattr(self.driver, 'bot_wxid', '')):
                self.driver._wxid = ""
                self.driver._nickname = ""
                if hasattr(self.driver, "bot_wxid"):
                    self.driver.bot_wxid = ""

            # 如果此时已连接但是 _wxid 仍为空，尝试通过缓存或数据库快速补充
            if self.driver.is_connected() and not (getattr(self.driver, '_wxid', '') or getattr(self.driver, 'bot_wxid', '')):
                try:
                    self.driver._try_restore_from_cache()
                    if not self.driver._wxid and self.driver.hwnd:
                        from src.wechat_4x.db_profile_extractor import extract_profile_from_db
                        res = extract_profile_from_db(self.driver.hwnd)
                        if res:
                            self.driver._wxid, self.driver._nickname = res
                except Exception as e_restore:
                    logger.debug(f"[监控] 启动时尝试补充微信账号信息异常: {e_restore}")

        if not self.ai_service.is_configured():
            print("[监控] AI 未配置，无法启动")
            return

        from src.utils.license_validator import LicenseValidator
        features = LicenseValidator.check_features()
        if not features.get("auto_chat", False):
            print("[监控] ❌ 当前版本不支持自动回复，请升级套餐")
            return

        # 🌟 强健性自动绑定：只要在启动监控时，能获取到合法的当前接管 wechat_id，即静默发起一次绑定动作，确保旗舰版正常授权
        # 解决冷启动/定时自愈重启时，由于未触发手动点火路由导致微信号没有自动绑定上旗舰版、额度被拦截的严重漏洞
        try:
            wxid = self.account_id
            if wxid and wxid != 'default':
                logger.info(f"[监控] 发现微信 '{wxid}' 已登录接管，正在静默为您绑定到星码主账号订阅...")
                # 放在线程池中静默执行，确保不阻塞主 asyncio 循环
                await asyncio.get_running_loop().run_in_executor(
                    None, 
                    lambda: LicenseValidator.bind_wechat(wxid)
                )
        except Exception as e:
            logger.warning(f"[监控] 启动时静默自动绑定微信号发生异常: {e}")

        # 清理上一次运行的所有临时缓存和冷却时间，保证开关切换后能立刻重新触发检测与自动回复
        self.reset_session_caches()

        self._running = True
        self._paused = False
        self._stats["start_time"] = time.time()
        self._task = asyncio.create_task(self._loop())
        print(f"[监控] 已启动 (间隔{self._check_interval}秒, 冷却{self._cooldown}秒)")

        # 启动 WCDB 双引擎（后台异步，不阻塞主启动流程）
        asyncio.create_task(self._start_wcdb_engine())

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for partition in self._partitions.values():
            partition.suspended_sessions.clear()
        # 停止 WCDB 双引擎
        if self._wcdb_session_monitor:
            try:
                self._wcdb_session_monitor.stop()
            except Exception:
                pass
            self._wcdb_session_monitor = None
        print("[监控] 已停止")

    def pause(self):
        if self._running:
            self._paused = True
            logger.info('聊天监控器已暂停')

    def resume(self):
        if self._running:
            self._paused = False
            logger.info('聊天监控器已恢复')

    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        from src.utils.license_validator import LicenseValidator
        sub_info = LicenseValidator.check_subscription()
        ai_limit = sub_info.get("ai_daily_limit", 30)
        account_id = getattr(self.driver, 'bot_wxid', 'main') or 'main'
        ai_used = _chat_daily_counter.get_count("auto_reply", account_id)

        return {
            "running": self._running,
            "paused": self._paused,
            "ai_configured": self.ai_service.is_configured(),
            "sessions": len(self._initialized),
            "suspended_count": sum(
                len(p.suspended_sessions) for p in self._partitions.values()),
            "stats": self._stats.copy(),
            "degraded": LicenseValidator.is_degraded(),
            "quota": {
                "ai_used": ai_used,
                "ai_limit": ai_limit,
                "ai_remaining": max(0, ai_limit - ai_used) if ai_limit > 0 else -1,
                "exhausted": ai_limit > 0 and ai_used >= ai_limit,
            },
        }

    def update_config(self, config: dict):
        if 'check_interval' in config:
            self._check_interval = max(2, config['check_interval'])  # 最小 2s，不再强制 3s 下限
        if 'cooldown' in config:
            self._cooldown = max(5, config['cooldown'])
        if 'whitelist_enabled' in config:
            self._whitelist_enabled = config['whitelist_enabled']
        if 'whitelist' in config:
            self._whitelist = config['whitelist']
        if 'group_at_only' in config:
            self._group_at_only = config['group_at_only']
        elif 'auto_chat_group_at_only' in config:
            self._group_at_only = config['auto_chat_group_at_only']
        if 'colleague_names' in config:
            self._colleague_names = config['colleague_names']

    async def _start_wcdb_engine(self):
        """
        后台启动 WCDB 双引擎感知层。
        延迟等待 UIA 提取微信号初始化完成后再启动，避免定位到错误的数据库或使用过期缓存密钥。
        完全静默，不影响主 UIA 监控流程。
        """
        # 永久失败熔断：密钥已被证实无法通过 Hook 提取（已登录微信数据库早已打开），停止重试
        if getattr(self, "_wcdb_key_failed_permanently", False):
            return
        if getattr(self, "_wcdb_starting", False) or getattr(self, "_wcdb_session_monitor", None) is not None:
            return
        self._wcdb_starting = True

        import asyncio
        try:
            # 持续等待直到微信账号 ID 提取成功（即不为 'default' 且不为空）
            wait_seconds = 0.0
            while (not self.account_id or self.account_id == "default") and wait_seconds < 15.0:
                await asyncio.sleep(0.5)
                wait_seconds += 0.5
            
            # 再额外等待 1.0 秒以确保所有的缓存和 context 已刷写完毕
            await asyncio.sleep(1.0)

            from src.wechat_4x.wcdb_session_monitor import WcdbSessionMonitor
            monitor = WcdbSessionMonitor(self)  # type: ignore
            self._wcdb_session_monitor = monitor
            loop = asyncio.get_running_loop()
            ok = await asyncio.get_running_loop().run_in_executor(
                None, lambda: monitor.start(loop)
            )
            if ok:
                print("[ChatMonitor] ✅ WCDB 双引擎已激活，消息感知层就绪。")
                logger.info("[ChatMonitor] WCDB 双引擎已接入，消息感知层已激活")
            else:
                print("[ChatMonitor] ⚠️ WCDB 双引擎未启动，已回退到纯 UIA 模式")
                logger.info("[ChatMonitor] WCDB 双引擎未启动（DLL/数据库不可用），继续纯 UIA 模式")
                # 设置永久失败熔断：密钥已确认无法通过 Hook 提取，停止后续所有轮询重试
                self._wcdb_key_failed_permanently = True
        except Exception as e:
            print(f"[ChatMonitor] ⚠️ WCDB 双引擎启动发生异常: {e}")
            logger.debug(f"[ChatMonitor] WCDB 双引擎启动异常（不影响主流程）: {e}")
        finally:
            self._wcdb_starting = False
