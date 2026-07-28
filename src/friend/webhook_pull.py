import threading
import time
import httpx
import json
import logging
from pathlib import Path
from typing import Any, List
from src.friend import friend_queue

logger = logging.getLogger(__name__)

class WebhookPullManager:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._running = False
        self._thread = None
        self._state_file = Path.home() / ".xm-ai-bot" / "webhook_pull_state.json"
        self._state = self._load_state()

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _load_state(self):
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "last_pull_time": 0,
            "last_status": "idle",
            "last_count": 0,
            "last_error": "",
            "logs": []
        }

    def _save_state(self):
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"[WebhookPull] Save state failed: {e}")

    def add_log(self, msg: str, level: str = "info"):
        from datetime import datetime
        log_entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": msg
        }
        self._state.setdefault("logs", [])
        self._state["logs"].insert(0, log_entry)
        self._state["logs"] = self._state["logs"][:50]  # keep 50
        self._save_state()

    def get_state(self):
        return self._state

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="WebhookPullThread")
            self._thread.start()
            logger.info("[WebhookPull] Background daemon started")

    def stop(self):
        with self._lock:
            self._running = False
            logger.info("[WebhookPull] Background daemon stopping")

    def _loop(self):
        while self._running:
            try:
                from src.api.config_api import _load_configs
                configs = _load_configs()
                settings = configs.get("webhook_pull_settings", {})
                if settings.get("enabled"):
                    url = settings.get("url")
                    interval = int(settings.get("interval_minutes", 10))
                    if url:
                        now = time.time()
                        last_pull = self._state.get("last_pull_time", 0)
                        if now - last_pull >= interval * 60:
                            # Trigger pull
                            # Run synchronously in this thread
                            import asyncio
                            try:
                                loop = asyncio.new_event_loop()
                                loop.run_until_complete(self.trigger_pull_sync(settings))
                                loop.close()
                            except Exception as e:
                                logger.exception(f"[WebhookPull] Sync pull run failed: {e}")
            except Exception as e:
                logger.error(f"[WebhookPull] Loop error: {e}")
            
            # check every 10 seconds
            for _ in range(10):
                if not self._running:
                    break
                time.sleep(1.0)

    async def trigger_pull_sync(self, settings: dict):
        url = settings.get("url")
        headers_str = settings.get("headers", "{}")
        response_path = settings.get("response_path", "data")
        phone_field = settings.get("phone_field", "phone")
        company_field = settings.get("company_field", "company_name")
        name_field = settings.get("name_field", "legal_person")
        tags_str = settings.get("tags", "Webhook自动拉取")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]

        headers = {}
        if headers_str:
            try:
                headers = json.loads(headers_str)
            except Exception as e:
                self.add_log(f"解析Headers JSON失败: {e}", "error")
                headers = {}

        self.add_log(f"开始自动拉取线索: {url}", "info")
        self._state["last_pull_time"] = time.time()
        self._save_state()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url, headers=headers)
            
            if r.status_code != 200:
                err_msg = f"HTTP 错误码: {r.status_code}"
                self._state["last_status"] = "failed"
                self._state["last_error"] = err_msg
                self.add_log(err_msg, "error")
                self._save_state()
                return

            try:
                resp_data = r.json()
            except Exception as e:
                err_msg = f"响应并非 JSON 格式: {e}"
                self._state["last_status"] = "failed"
                self._state["last_error"] = err_msg
                self.add_log(err_msg, "error")
                self._save_state()
                return

            raw_items = self._extract_by_path(resp_data, response_path)
            if not isinstance(raw_items, list):
                err_msg = f"响应解析失败：提取路径 [{response_path}] 结果不是数组格式"
                self._state["last_status"] = "failed"
                self._state["last_error"] = err_msg
                self.add_log(err_msg, "error")
                self._save_state()
                return

            contacts = []
            for idx, item in enumerate(raw_items):
                if not isinstance(item, dict):
                    continue
                phone = item.get(phone_field)
                company = item.get(company_field)
                name = item.get(name_field)
                if phone:
                    phone_str = "".join(c for c in str(phone) if c.isdigit())
                    if len(phone_str) == 11 and phone_str.startswith("1"):
                        contacts.append({
                            "primary_phone": phone_str,
                            "phones": [phone_str],
                            "company_name": str(company or "").strip(),
                            "legal_person": str(name or "").strip(),
                            "row_index": idx
                        })

            if not contacts:
                self._state["last_status"] = "success"
                self._state["last_count"] = 0
                self._state["last_error"] = ""
                self.add_log("拉取完成：未发现有效的新手机号/联系人数据", "info")
                self._save_state()
                return

            # Import to friend queue
            import uuid
            from src.crm.industry_config import IndustryConfigManager
            from src.api.instance_settings_api import load_instance_settings
            
            industry_id, industry_name = "", ""
            try:
                active_wxid = None
                try:
                    from src.utils.instance_manager import InstanceManagerV2
                    manager = InstanceManagerV2.get_instance()
                    active_inst_id = manager.get_active_instance_id()
                    if active_inst_id and active_inst_id in manager.get_all_instances():
                        active_wxid = active_inst_id
                except Exception:
                    pass

                mgr = IndustryConfigManager(account_id="global")
                active_profile = None
                if active_wxid:
                    try:
                        cfg = load_instance_settings(active_wxid)
                        profile_id = cfg.get("industry_profile_id")
                        if profile_id:
                            active_profile = mgr.get_profile_by_id(profile_id)
                    except Exception:
                        pass
                if not active_profile:
                    active_profile = mgr.get_active_profile()
                if active_profile:
                    industry_id, industry_name = active_profile.id, active_profile.name
            except Exception:
                pass

            import_batch_id = f"wh_{uuid.uuid4().hex[:8]}"
            import_result = friend_queue.import_contacts(
                contacts,
                source_file="webhook_pull",
                original_filename="Webhook自动拉取",
                tags=tags,
                import_batch_id=import_batch_id,
                industry_profile_id=industry_id,
                industry_profile_name=industry_name,
            )

            imported = import_result.get("imported", 0)
            skipped = import_result.get("skipped", 0)

            self._state["last_status"] = "success"
            self._state["last_count"] = imported
            self._state["last_error"] = ""
            self.add_log(f"成功拉取并导入: 导入 {imported} 条, 跳过 {skipped} 条", "info")
            self._save_state()

            # sync with cloud
            try:
                import threading as t
                from src.utils.cloud_sync import get_cloud_client
                client = get_cloud_client()
                if imported > 0:
                    try:
                        snapshot = []
                        for rec in import_result.get("records", []):
                            snapshot.append({
                                "phone": rec.get("phone") or "",
                                "company_name": rec.get("company_name") or "",
                                "legal_person": rec.get("legal_person") or "",
                            })
                        client.create_import_batch(
                            source_type="webhook",
                            source_label="Webhook自动拉取",
                            total_count=imported,
                            session_id=import_batch_id,
                            data_snapshot=snapshot
                        )
                    except Exception as ex:
                        logger.warning(f"[WebhookPull] 同步云端批次失败: {ex}")
                t.Thread(
                    target=client.sync_friend_queue,
                    args=(),
                    daemon=True
                ).start()
            except Exception:
                pass

        except Exception as e:
            err_msg = f"拉取异常: {e}"
            self._state["last_status"] = "failed"
            self._state["last_error"] = err_msg
            self.add_log(err_msg, "error")
            self._save_state()

    def _extract_by_path(self, data: Any, path: str) -> List[Any]:
        if not path or path == ".":
            return data if isinstance(data, list) else []
        parts = path.strip().split(".")
        curr = data
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                return []
        return curr if isinstance(curr, list) else []
