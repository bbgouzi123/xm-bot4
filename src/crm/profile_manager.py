"""
客户画像管理器 — 360° 全景客户画像持久化管理

数据存储：xm-core（xm-bot4 后端）/ AccountDatabaseManager (SQLite)
核心方法：
- get_profile(wxid): 获取/创建客户画像
- update_tags(wxid, new_tags): 合并新标签到画像
- get_all_profiles(): 获取所有客户画像列表
- search_by_tag(category, subcategory, value): 按标签搜索客户
"""
import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from urllib.parse import quote

from .tag_manager import TagManager, TagEntry
from .customer_profile import CustomerProfile
from .profile_snapshot import save_local_snapshot, load_local_snapshot, is_standard_wxid, heal_profiles

logger = logging.getLogger(__name__)


class ProfileManager:
    """客户画像管理器（纯内存缓存 + 同步后端持久化） - 单例模式共享状态"""

    _instances = {}
    _lock = __import__("threading").Lock()

    # 账号尚未确定时的占位哨兵 ID
    _PLACEHOLDER_ID = "default"

    def __new__(cls, data_dir: str = None, account_id: str = "main"):
        if not account_id or account_id in ("main", "default"):
            from src.crm.account_data import get_active_account
            account_id = get_active_account()

        with cls._lock:
            if account_id not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[account_id] = instance
        return cls._instances[account_id]

    @classmethod
    def _evict_placeholder(cls):
        """驱逐 'default' 占位单例槽，防止内存泄漏。
        由 set_active_account() 在确定真实 wxid 后调用。
        """
        with cls._lock:
            placeholder = cls._instances.pop(cls._PLACEHOLDER_ID, None)
        if placeholder is not None:
            logger.debug("[CRM] 已清理 ProfileManager 'default' 占位单例槽")

    def __init__(self, data_dir: str = None, account_id: str = "main"):
        """初始化画像管理器"""
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.account_id = account_id
        if not account_id or account_id in ("main", "default"):
            from src.crm.account_data import get_active_account
            self.account_id = get_active_account()

        self._cache: Dict[str, CustomerProfile] = {}  # 内存缓存（唯一读取源）
        self.loaded_event = threading.Event()  # 线程事件：追踪加载状态

        # 账号尚未确定（仍为 default）时，跳过所有 I/O 直接标记就绪，
        # 避免快照 account_id 不一致警告以及无效云端请求
        if self.account_id == self._PLACEHOLDER_ID:
            logger.debug("[CRM] ProfileManager 处于 'default' 占位状态，跳过数据加载（等待登录后重建）")
            self.loaded_event.set()
            return

        # 启动时异步从同步后端加载
        threading.Thread(target=self._init_load, daemon=True, name=f"profile-init-{self.account_id}").start()
        logger.debug(f"[CRM] ProfileManager 初始化 (Account: {self.account_id})")

    def _init_load(self):
        """启动时优先从本地 JSON 快照恢复，随后从同步后端拉取最新画像进行增量合并更新"""
        try:
            # 1. 优先无延迟从本地恢复，防止网络阻塞导致初始画像为空
            self._load_local_snapshot()

            cloud_loaded = False
            try:
                from src.utils.cloud_sync import get_cloud_client
                cloud = get_cloud_client()
                bot = quote(self.account_id or "", safe="")
                data = cloud._get(f"/api/v1/crm/profiles?bot_wxid={bot}", need_auth=True)
                if data and isinstance(data, list):
                    for item in data:
                        try:
                            profile = CustomerProfile.from_dict(item)
                            if profile.wxid:
                                # 🌟 安全合并：若本地缓存已存在此画像，保留本地特有或更完整的字段（如已同步标签和备注）
                                if profile.wxid in self._cache:
                                    local_p = self._cache[profile.wxid]
                                    if local_p.wx_synced_tags and not profile.wx_synced_tags:
                                        profile.wx_synced_tags = local_p.wx_synced_tags
                                    if local_p.notes and not profile.notes:
                                        profile.notes = local_p.notes
                                    if local_p.remark and not profile.remark:
                                        profile.remark = local_p.remark
                                    if local_p.region and not profile.region:
                                        profile.region = local_p.region
                                    if local_p.signature and not profile.signature:
                                        profile.signature = local_p.signature
                                self._cache[profile.wxid] = profile
                        except Exception:
                            continue
                    if self._cache:
                        logger.info(f"[CRM] ☁️ 从同步后端合并更新了 {len(self._cache)} 条画像")
                        cloud_loaded = True
                        # 合并完成后异步防抖落盘
                        self._save_local_snapshot()
            except Exception as e:
                logger.debug(f"[CRM] 画像同步后端加载跳过或异常: {e}")

            # 3. 运行画像自愈清洗与合并
            try:
                self._heal_profiles()
            except Exception as he:
                logger.warning(f"[CRM] 画像自愈清理异常: {he}")
        finally:
            self.loaded_event.set()

    def get_profile(self, wxid: str, nickname: str = None) -> CustomerProfile:
        """获取客户画像（不存在则创建，支持昵称到 wxid 的平滑迁移）"""
        if wxid in self._cache:
            profile = self._cache[wxid]
            # 🌟 自愈合并：若此时 cache 中还旧存有以 nickname 为 key 的临时非标准画像，将其合并至此标准画像
            if nickname and nickname != wxid and nickname in self._cache:
                try:
                    tp = self._cache.pop(nickname)
                    logger.info(f"[CRM自愈] 在 get_profile 中将残留临时画像 '{nickname}' 合并至标准画像 '{wxid}'")
                    if tp.tags:
                        profile.tags = TagManager.merge_tags(profile.tags, tp.tags)
                    if tp.notes:
                        for note in tp.notes:
                            if note not in profile.notes:
                                profile.notes.append(note)
                    if tp.chat_count:
                        profile.chat_count += tp.chat_count
                    self._save_local_snapshot()
                except Exception as ex:
                    logger.warning(f"[CRM自愈] get_profile 合并临时画像异常: {ex}")
            if nickname and nickname != profile.nickname:
                profile.nickname = nickname
            return profile

        # 如果提供了 nickname，并且当前的 wxid 长得像标准 ID 而不是中文昵称
        if nickname and wxid != nickname:
            # 查找是否存在按 nickname 建立的临时画像（比如刚通过好友时还没来得及同步 wxid）
            if nickname in self._cache:
                logger.info(f"[CRM] 发现临时画像 '{nickname}'，平滑迁移到真实 ID: {wxid}")
                profile = self._cache.pop(nickname)
                profile.wxid = wxid
                self._cache[wxid] = profile
                return profile

        # 创建新画像
        profile = CustomerProfile(wxid)
        if nickname:
            profile.nickname = nickname
        profile.first_contact = __import__("datetime").datetime.now().isoformat()
        self._cache[wxid] = profile
        return profile

    def save_profile(self, profile: CustomerProfile):
        """保存客户画像到内存 + 异步推同步后端 + 本地落盘 + 第三方 CRM 同步分发"""
        self._cache[profile.wxid] = profile
        self._async_cloud_sync(profile)
        self._save_local_snapshot()
        
        # 异步分发至配置的第三方 CRM 适配器
        try:
            from src.crm.connector_factory import CRMConnectorFactory
            CRMConnectorFactory.dispatch_sync(profile.to_dict())
        except Exception as factory_err:
            logger.error(f"[CRM] 适配器工厂分发画像同步失败: {factory_err}")

    def _async_cloud_sync(self, profile: CustomerProfile):
        """异步推送单个画像到同步后端（静默失败，不影响本地）"""
        import threading
        def _push():
            try:
                from src.utils.cloud_sync import get_cloud_client
                cloud = get_cloud_client()
                intent_tag = profile.get_tag("intent")
                cloud.sync_crm_profiles([{
                    "wxid": profile.wxid,
                    "nickname": profile.nickname,
                    "intent_level": intent_tag.value if intent_tag else "",
                    "tags": json.dumps([t.to_dict() for t in profile.tags], ensure_ascii=False),
                    "summary": profile.conversation_summary,
                }])
            except Exception as e:
                logger.debug(f"[CRM·同步后端] 画像推送失败 {profile.wxid}: {e}")
        threading.Thread(target=_push, daemon=True, name=f"cloud-push-{profile.wxid[:8]}").start()

    def sync_all_to_cloud(self) -> int:
        """全量快照：将所有本地画像推送到同步后端（定时任务调用）"""
        try:
            from src.utils.cloud_sync import get_cloud_client
            cloud = get_cloud_client()
            profiles = self.get_all_profiles()
            if not profiles:
                return 0

            batch = []
            for p in profiles:
                intent_tag = p.get_tag("intent")
                batch.append({
                    "wxid": p.wxid,
                    "nickname": p.nickname,
                    "intent_level": intent_tag.value if intent_tag else "",
                    "tags": json.dumps([t.to_dict() for t in p.tags], ensure_ascii=False),
                    "summary": p.conversation_summary,
                })

            # 分批推送（每批 50 条）
            synced = 0
            for i in range(0, len(batch), 50):
                chunk = batch[i:i+50]
                if cloud.sync_crm_profiles(chunk):
                    synced += len(chunk)

            logger.info(f"[CRM·同步后端] 全量快照完成: {synced}/{len(profiles)} 条画像已推送")
            return synced
        except Exception as e:
            logger.error(f"[CRM·同步后端] 全量快照失败: {e}")
            return 0

    def update_tags(self, wxid: str, new_tags: List[TagEntry], source: str = "chat", nickname: str = "") -> CustomerProfile:
        profile = self.get_profile(wxid, nickname)
        for t in new_tags:
            t.source = source
        profile.tags = TagManager.merge_tags(profile.tags, new_tags)
        profile.last_active = __import__("datetime").datetime.now().isoformat()
        if source == "chat":
            profile.chat_count += 1
        self.save_profile(profile)
        tag_strs = [f"{t.subcategory}={t.value}" for t in new_tags]
        logger.info(f"[CRM] 更新标签 {wxid}: {', '.join(tag_strs)}")
        return profile

    def update_from_ai_tags(self, wxid: str, raw_tags: dict, source: str = "chat", nickname: str = "") -> CustomerProfile:
        tag_entries = TagManager.normalize_ai_tags(raw_tags)
        if not tag_entries:
            return self.get_profile(wxid, nickname)
        return self.update_tags(wxid, tag_entries, source, nickname)

    def get_all_profiles(self) -> List[CustomerProfile]:
        return list(self._cache.values())

    def search_by_tag(self, category: str = None, subcategory: str = None, value_contains: str = None) -> List[CustomerProfile]:
        results = []
        for p in self.get_all_profiles():
            for t in p.tags:
                if (not category or t.category == category) and \
                   (not subcategory or t.subcategory == subcategory) and \
                   (not value_contains or value_contains in t.value):
                    results.append(p)
                    break
        return results

    def get_tags_needing_sync(self, wxid: str, max_tags: int = 3) -> List[str]:
        profile = self.get_profile(wxid)
        from .profile_sync_rules import get_tags_needing_sync
        return get_tags_needing_sync(profile, max_tags)

    def mark_tags_synced(self, wxid: str, synced_labels: List[str]):
        profile = self.get_profile(wxid)
        for label in synced_labels:
            if label not in profile.wx_synced_tags:
                profile.wx_synced_tags.append(label)
        self.save_profile(profile)

    def add_note(self, wxid: str, note: str, nickname: str = "") -> Optional[CustomerProfile]:
        profile = self.get_profile(wxid, nickname)
        if note:
            profile.notes.append(f"[{__import__('datetime').datetime.now().strftime('%m-%d %H:%M')}] {note}")
        self.save_profile(profile)
        return profile

    def delete_profile(self, wxid: str) -> bool:
        self._cache.pop(wxid, None)
        logger.info(f"[CRM] 删除画像: {wxid}")
        return True

    def get_profile_stats(self) -> Dict[str, Any]:
        profiles = self.get_all_profiles()
        intent_stats: Dict[str, int] = {}
        for p in profiles:
            val = p.get_tag("intent").value if p.get_tag("intent") else "未分类"
            intent_stats[val] = intent_stats.get(val, 0) + 1
        return {
            "total_customers": len(profiles),
            "intent_distribution": intent_stats,
            "tagged_count": sum(1 for p in profiles if p.tags),
            "untagged_count": sum(1 for p in profiles if not p.tags),
        }

    # ==================== 本地快照持久化 ====================

    _snapshot_timer = None

    def _save_local_snapshot(self):
        """防抖落盘：将内存中的客户画像快照保存到本地 JSON 文件（10 秒内合并）"""
        save_local_snapshot(self)

    def _load_local_snapshot(self):
        """从本地 JSON 快照恢复客户画像到内存"""
        load_local_snapshot(self)

    def _is_standard_wxid(self, wxid: str) -> bool:
        """委托给 profile_snapshot.is_standard_wxid 模块级函数（已拆离以满足 300 行限额）。"""
        return is_standard_wxid(wxid)

    def _heal_profiles(self):
        """委托给 profile_snapshot.heal_profiles 模块级函数（已拆离以满足 300 行限额）。"""
        heal_profiles(self)
