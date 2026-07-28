import json
import logging
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCAL_SNAPSHOT_DIR = Path.home() / ".xm-ai-bot"
_LOCAL_SNAPSHOT_FILE = _LOCAL_SNAPSHOT_DIR / "contacts_snapshot.json"

class ContactsCacheSnapshotMixin:
    _snapshot_timer = None

    def _save_local_snapshot(self):
        """防抖落盘：将内存中的通讯录快照保存到本地 JSON 文件（5 秒内合并）"""
        if self._snapshot_timer:
            self._snapshot_timer.cancel()
        self._snapshot_timer = threading.Timer(5.0, self._do_save_snapshot)
        self._snapshot_timer.daemon = True
        self._snapshot_timer.start()

    def _force_save_snapshot(self):
        """即时落盘（跳过防抖）"""
        if self._snapshot_timer:
            self._snapshot_timer.cancel()
        self._do_save_snapshot()

    def _do_save_snapshot(self):
        """执行实际的快照落盘"""
        try:
            with self._rw_lock:
                snapshot = {
                    "friends": dict(self._friends),
                    "groups": dict(self._groups),
                    "group_members": dict(self._group_members),
                    "contact_tags": dict(self._contact_tags),
                    "saved_at": datetime.now().isoformat(),
                }
            _LOCAL_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            _LOCAL_SNAPSHOT_FILE.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            total = sum(len(v) for v in snapshot["friends"].values())
            logger.debug(f"[ContactsCache] 💾 本地快照已保存 ({total} 个联系人)")
        except Exception as e:
            logger.warning(f"[ContactsCache] 💾 本地快照保存失败: {e}")

    def _load_local_snapshot(self, account_id: str) -> bool:
        """从本地 JSON 快照恢复通讯录到内存"""
        try:
            if not _LOCAL_SNAPSHOT_FILE.exists():
                return False
            raw = json.loads(_LOCAL_SNAPSHOT_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return False

            friends_data = raw.get("friends", {})
            groups_data = raw.get("groups", {})
            members_data = raw.get("group_members", {})
            tags_data = raw.get("contact_tags", {})

            restored_friends = 0
            restored_groups = 0

            with self._rw_lock:
                for aid, friends in friends_data.items():
                    if isinstance(friends, list) and friends:
                        self._friends[aid] = friends
                        restored_friends += len(friends)
                for aid, groups in groups_data.items():
                    if isinstance(groups, list) and groups:
                        self._groups[aid] = groups
                        restored_groups += len(groups)
                for aid, members in members_data.items():
                    if isinstance(members, list) and members:
                        self._group_members[aid] = members
                for aid, tags in tags_data.items():
                    if isinstance(tags, list) and tags:
                        self._contact_tags[aid] = tags

            saved_at = raw.get("saved_at", "未知")
            logger.info(
                f"[ContactsCache] 💾 从本地快照恢复成功: "
                f"{restored_friends} 个好友, {restored_groups} 个群聊 "
                f"(快照时间: {saved_at})"
            )
            return True
        except Exception as e:
            logger.warning(f"[ContactsCache] 💾 本地快照加载失败: {e}")
            return False
