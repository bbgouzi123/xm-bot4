import logging
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)

class TaskMixin:
    """WeChatDBManager 的任务、标签、话术与群发管理混入类 (Mixin)"""

    # ==================== 自动跟单长期策略 (SDR Auto Follow) ====================

    def add_auto_follow_task(self, task_data: Dict) -> bool:
        """注册一个新的自动跟单任务到持久化队列"""
        self._auto_follow_queue.append(task_data)
        self._sync_auto_follow_to_cloud()
        return True

    def get_auto_follow_tasks(self) -> List[Dict]:
        """获取所有激活的自动跟单长程任务"""
        return self._auto_follow_queue

    def get_auto_follow_task(self, task_id: str):
        """根据 ID 获取单个自动跟单长程任务"""
        for task in self._auto_follow_queue:
            if task.get("task_id") == task_id:
                return task
        return None

    def update_auto_follow_task(self, task_id: str, updates: Dict) -> bool:
        """更新跟单任务进度状态"""
        for task in self._auto_follow_queue:
            if task.get("task_id") == task_id:
                task.update(updates)
                task["last_updated"] = datetime.now().isoformat()
                self._sync_auto_follow_to_cloud()
                return True
        return False

    def stop_all_active_auto_follow_tasks(self) -> int:
        """将队列中所有 status==active 的跟单任务标记为 stopped"""
        changed = False
        n = 0
        for task in self._auto_follow_queue:
            if task.get("status", "active") == "active":
                task["status"] = "stopped"
                task["stopped_at"] = datetime.now().isoformat()
                n += 1
                changed = True
        if changed:
            self._sync_auto_follow_to_cloud()
        return n

    def _sync_auto_follow_to_cloud(self):
        """先落盘本地快照，再异步推送自动跟进任务到同步后端"""
        import threading
        self._persist_snapshot()
        data = list(self._auto_follow_queue)
        def _push():
            try:
                from src.utils.cloud_sync import get_cloud_client
                get_cloud_client().sync_follow_tasks(data)
            except Exception as e:
                logger.debug(f"[自动跟单] 同步后端推送失败: {e}")
        threading.Thread(target=_push, daemon=True, name="follow-tasks-push").start()

    # ==================== 系统独立标签池管理 (Customer Tags) ====================

    def get_all_tags(self) -> List[Dict]:
        """获取系统所有预设标签"""
        return self._tags_queue

    def add_tag(self, name: str, color: str = "brand") -> Dict:
        """新增标签"""
        import uuid
        tag_id = f"tag_{uuid.uuid4().hex[:6]}"
        new_tag = {
            "id": tag_id,
            "name": name,
            "color": color,
            "created_at": datetime.now().isoformat()
        }
        self._tags_queue.append(new_tag)
        self._persist_snapshot()
        return new_tag

    def delete_tag(self, tag_id: str) -> bool:
        """删除标签"""
        initial_len = len(self._tags_queue)
        self._tags_queue = [t for t in self._tags_queue if t["id"] != tag_id]
        changed = len(self._tags_queue) < initial_len
        if changed:
            self._persist_snapshot()
        return changed

    # ==================== 话术组（Script Groups）CRUD ====================

    def get_all_script_groups(self) -> List[Dict]:
        """获取所有自定义与预设话术组"""
        return self._script_groups

    def add_script_group(self, group_data: Dict) -> Dict:
        """新增话术组"""
        import uuid
        sg_id = f"sg_{uuid.uuid4().hex[:6]}"
        group_data["id"] = sg_id
        group_data["created_at"] = datetime.now().isoformat()
        if "greetings" not in group_data:
            group_data["greetings"] = []
        self._script_groups.append(group_data)
        self._sync_script_groups_to_cloud()
        return group_data

    def update_script_group(self, group_id: str, updates: Dict) -> bool:
        """更新话术组"""
        for group in self._script_groups:
            if group.get("id") == group_id:
                group.update(updates)
                self._sync_script_groups_to_cloud()
                return True
        return False

    def delete_script_group(self, group_id: str) -> bool:
        """删除指定的话术组"""
        initial_len = len(self._script_groups)
        self._script_groups = [g for g in self._script_groups if g.get("id") != group_id]
        changed = len(self._script_groups) < initial_len
        if changed:
            self._sync_script_groups_to_cloud()
        return changed

    def _sync_script_groups_to_cloud(self):
        """先落盘本地快照，再异步推送话术组到同步后端"""
        import threading
        self._persist_snapshot()
        data = list(self._script_groups)
        def _push():
            try:
                from src.utils.cloud_sync import get_cloud_client
                get_cloud_client().save_setting("script_groups", data)
            except Exception as e:
                logger.debug(f"[话术组] 同步后端推送失败: {e}")
        threading.Thread(target=_push, daemon=True, name="script-groups-push").start()

    # ==================== 大面积安全群发（Mass Sending） ====================

    def add_mass_send_job(self, job: dict):
        """添加一个群发任务"""
        self._mass_send_jobs.append(job)
        self._persist_snapshot()

    def update_mass_send_job(self, job_id: str, updates: dict) -> bool:
        """更新群发任务"""
        for job in self._mass_send_jobs:
            if job.get("id") == job_id:
                job.update(updates)
                self._persist_snapshot()
                return True
        return False

    def get_mass_send_jobs(self) -> List[dict]:
        """获取所有群发任务"""
        return self._mass_send_jobs

    def add_mass_send_queues(self, items: List[dict]):
        """批量添加群发子任务队列"""
        self._mass_send_queues.extend(items)
        self._persist_snapshot()

    def update_mass_send_queue_item(self, item_id: str, updates: dict) -> bool:
        """更新群发队列子项"""
        for item in self._mass_send_queues:
            if item.get("id") == item_id:
                item.update(updates)
                self._persist_snapshot()
                return True
        return False

    def get_mass_send_queues(self, job_id: str = None) -> List[dict]:
        """获取群发队列项"""
        if job_id:
            return [x for x in self._mass_send_queues if x.get("job_id") == job_id]
        return self._mass_send_queues

    # ==================== 承诺业务任务池 (Promise Tasks) ====================

    def add_promise_task(self, task: dict) -> dict:
        """添加一个承诺任务到任务池"""
        import uuid
        if "id" not in task:
            task["id"] = f"pt_{uuid.uuid4().hex[:8]}"
        if "status" not in task:
            task["status"] = "pending"
        if "created_at" not in task:
            task["created_at"] = datetime.now().isoformat()
        if "retry_count" not in task:
            task["retry_count"] = 0
        self._promise_tasks.append(task)
        self._persist_snapshot()
        return task

    def get_promise_tasks(self) -> List[dict]:
        """获取所有承诺任务"""
        return self._promise_tasks

    def update_promise_task(self, task_id: str, updates: dict) -> bool:
        """更新承诺任务状态或元数据"""
        for task in self._promise_tasks:
            if task.get("id") == task_id:
                task.update(updates)
                task["last_updated"] = datetime.now().isoformat()
                self._persist_snapshot()
                return True
        return False

    def delete_promise_task(self, task_id: str) -> bool:
        """物理删除已记录的承诺任务"""
        initial_len = len(self._promise_tasks)
        self._promise_tasks = [t for t in self._promise_tasks if t.get("id") != task_id]
        changed = len(self._promise_tasks) < initial_len
        if changed:
            self._persist_snapshot()
        return changed

    # ==================== 自动履约能力管理 (Fulfillment Capabilities) ====================

    def get_fulfillment_capabilities(self) -> List[Dict]:
        """获取所有已注册的自动履约能力选项"""
        return self._fulfillment_capabilities

    def update_fulfillment_capability(self, key: str, updates: Dict) -> bool:
        """更新履约能力开关或配置细节"""
        for capability in self._fulfillment_capabilities:
            if capability.get("key") == key:
                if "enabled" in updates:
                    capability["enabled"] = bool(updates["enabled"])
                if "config" in updates and isinstance(updates["config"], dict):
                    if "config" not in capability:
                        capability["config"] = {}
                    capability["config"].update(updates["config"])
                self._persist_snapshot()
                return True
        return False

    def add_fulfillment_capability(self, capability: Dict) -> bool:
        """新增自定义的履约能力"""
        key = capability.get("key")
        if not key:
            return False
        for c in self._fulfillment_capabilities:
            if c.get("key") == key:
                return False
        
        new_cap = {
            "key": key,
            "name": capability.get("name", "自定义物理能力"),
            "safety_level": int(capability.get("safety_level", 3)),
            "enabled": bool(capability.get("enabled", True)),
            "config": capability.get("config", {}),
            "is_custom": True
        }
        self._fulfillment_capabilities.append(new_cap)
        self._persist_snapshot()
        return True

    def delete_fulfillment_capability(self, key: str) -> bool:
        """删除指定的自定义履约能力（内置能力不可删除）"""
        target = None
        for c in self._fulfillment_capabilities:
            if c.get("key") == key:
                if c.get("is_custom"):
                    target = c
                    break
        if target:
            self._fulfillment_capabilities.remove(target)
            self._persist_snapshot()
            return True
        return False

