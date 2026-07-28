import json
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class CloudSyncWorkerMixin:
    """同步服务后台工作线程 Mixin"""

    def start_background_sync(self):
        """启动后台同步线程"""
        if self._running:
            return
        self._running = True
        self._sync_thread = threading.Thread(
            target=self._background_worker,
            name="cloud-sync-worker",
            daemon=True
        )
        self._sync_thread.start()
        logger.info("[同步服务] 后台同步线程已启动")

    def stop_background_sync(self):
        """停止后台同步"""
        self._running = False
        self.flush_pending_events(max_batches=20)

    def _background_worker(self):
        """后台工作线程"""
        _tick = 0
        while self._running:
            try:
                # 网络自愈：如果初始同步尚未完成，说明启动时连接失败，在连接恢复时自动补做初始同步并启动轮询
                if not getattr(self, "_initial_sync_done", False):
                    try:
                        # initial_sync() 内部如果成功会设置 _initial_sync_done = True 并拉取所有配置
                        if self.initial_sync():
                            logger.info("[同步服务] 自愈成功：同步后端服务已恢复就绪，已成功补做初始同步")
                            try:
                                self.start_enterprise_command_poller(interval=5)
                            except Exception as pe:
                                logger.warning(f"[同步服务] 自愈：启动企业命令轮询失败: {pe}")
                    except Exception as se:
                        logger.debug(f"[同步服务] 后台自愈检测异常: {se}")

                self._flush_event_queue()

                if _tick % 10 == 0:
                    self._sync_local_tables_to_cloud()
            except Exception as e:
                logger.error(f"[同步服务] 后台线程异常: {e}")
            time.sleep(30)
            _tick += 1

    def _flush_event_queue(self) -> int:
        """将队列中的事件批量上报到同步后端"""
        if not self.jwt_token:
            return 0

        with self._queue_lock:
            if not self._queue:
                return 0
            batch = self._queue[:100]
            self._queue = self._queue[100:]
            self._save_queue_to_disk()

        result = self._post("/api/v1/events", {"events": batch}, need_auth=True)
        if result:
            reported = result.get('reported', 0) if isinstance(result, dict) else len(batch)
            logger.info(f"[同步服务] 事件批量上报: {reported} 条")
            return len(batch)

        with self._queue_lock:
            self._queue = batch + self._queue
            self._save_queue_to_disk()
        return 0

    def flush_pending_events(self, max_batches: int = 20) -> int:
        """尽力清空待上传队列"""
        total_sent = 0
        for _ in range(max_batches):
            sent = self._flush_event_queue()
            if sent <= 0:
                break
            total_sent += sent
        return total_sent

    def _save_queue_to_disk(self):
        """持久化事件队列到本地"""
        try:
            self._queue_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self._queue_file.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps(self._queue, ensure_ascii=False),
                encoding="utf-8"
            )
            tmp_file.replace(self._queue_file)
        except Exception as e:
            logger.warning(f"[同步服务] 保存本地事件队列失败: {e}")

    def _sync_local_tables_to_cloud(self):
        """定期将内存中的通讯录数据推送到同步后端"""
        if not self.jwt_token:
            return

        try:
            from src.utils.contacts_cache import contacts_cache
            from src.crm.account_data import get_active_account
            account_id = get_active_account()

            friends = contacts_cache.get_friends(account_id)
            if friends:
                self.sync_contacts(friends)

            groups = contacts_cache.get_groups(account_id)
            if groups:
                self.sync_groups(groups)

            members = contacts_cache.get_group_members(account_id)
            if members:
                for i in range(0, len(members), 200):
                    self.sync_group_members(members[i:i+200])

            tags = contacts_cache.get_contact_tags(account_id)
            if tags:
                self.sync_contact_tags(tags)

            from src.crm.moment_planner_service import _schedules, _schedule_lock
            with _schedule_lock:
                schedules = list(_schedules)
            # 始终推送排期（含空列表），确保删除全部排期后同步后端也被清空
            self.sync_moment_schedules(schedules)

            total = len(friends) + len(groups) + len(members) + len(tags) + len(schedules)
            if total > 0:
                logger.info(
                    f"[同步服务] 📊 内存数据推送完成: "
                    f"好友 {len(friends)} / 群 {len(groups)} / "
                    f"群成员 {len(members)} / 标签 {len(tags)} / "
                    f"排期 {len(schedules)}"
                )
        except Exception as e:
            logger.warning(f"[同步服务] 数据推送异常: {e}")
