"""
WeChatDBManager — 已重构为纯内存代理层

原来直接操作 SQLite，现在委托给 contacts_cache 内存缓存。
保留原有 API 签名，确保调用方零改动。

获客名单 / 自动跟单 / 系统标签：内存 + ~/.xm-ai-bot/wechat_db_state.json 本地兜底。
"""
import copy
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

from .db_friend_mixin import FriendMixin
from .db_task_mixin import TaskMixin
from .db_plugin_market_mixin import PluginMarketMixin
from .db_keyword_reply_mixin import KeywordReplyMixin
from .db_defaults import (
    _default_tags_queue,
    _default_keyword_replies,
    _default_script_groups,
    _default_fulfillment_capabilities,
    wechat_db_flush_before_switch,
    wechat_db_reload_after_switch
)

logger = logging.getLogger(__name__)


def _wdb_state_path_for_active_bot() -> Path:
    """获客名单/跟单/标签池状态文件：按接管微信分文件，避免多微信串数据。"""
    from src.crm.account_data import get_account_data_dir, get_active_account
    wxid = get_active_account() or "default"
    return Path(get_account_data_dir(wxid)) / "wechat_db_state.json"


def _wdb_flush_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class WeChatDBManager(FriendMixin, TaskMixin, PluginMarketMixin, KeywordReplyMixin):
    """微信通讯录管理器（纯内存代理，继承功能 Mixins 保证单文件不超过300行）"""
    _instance = None

    def __new__(cls, db_path='data/wechat_contacts.db'):
        if cls._instance is None:
            cls._instance = super(WeChatDBManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path='data/wechat_contacts.db'):
        if self._initialized:
            return
        self._initialized = True
        # 获客名单（friend_list）内存存储
        self._friend_queue: List[dict] = []
        # 自动跟单（Auto Follow）长程任务池内存存储
        self._auto_follow_queue: List[dict] = []
        # 话术组（Script Groups）内存存储
        self._script_groups: List[dict] = []
        self._tags_queue: List[dict] = [
            {"id": "t1", "name": "高意向客户", "color": "red", "created_at": "2026-04-11T12:00:00"},
            {"id": "t2", "name": "待跟进", "color": "orange", "created_at": "2026-04-11T12:00:00"},
            {"id": "t3", "name": "已报价未成交", "color": "blue", "created_at": "2026-04-11T12:00:00"},
            {"id": "t4", "name": "沉默超过3天", "color": "gray", "created_at": "2026-04-11T12:00:00"},
            {"id": "t5", "name": "VIP重要高客单", "color": "purple", "created_at": "2026-04-11T12:00:00"},
            {"id": "t6", "name": "老客户复购", "color": "green", "created_at": "2026-04-11T12:00:00"},
        ]
        self._mass_send_jobs: List[dict] = []
        self._mass_send_queues: List[dict] = []
        self._keyword_replies: List[dict] = []
        self._promise_tasks: List[dict] = []
        self._fulfillment_capabilities: List[dict] = []
        self._market_plugins: List[dict] = []
        self._plugin_purchases: List[dict] = []
        self._withdrawal_records: List[dict] = []
        # 新友身份引导与拉群分流配置
        self._identity_routing: dict = {
            "enabled": False,
            "ask_prompt": "您好！请问您目前是我们的普通会员、高级会员，还是尊贵的销冠包用户？",
            "fallback_prompt": "抱歉，没能识别您的身份。您可以直接回复数字 1、2 或 3 哦。",
            "rules": [],
            "tag_mappings": []
        }
        self.flagship_bonus_sent = False
        self.base_bonus_sent = False
        self.professional_bonus_sent = False
        self.trial_bonus_sent = False
        self._load_snapshot_if_exists()
        logger.info("[WeChatDBManager] 初始化完毕（纯内存模式，无 SQLite）")

    def _load_snapshot_if_exists(self) -> None:
        path = _wdb_state_path_for_active_bot()
        legacy = Path.home() / ".xm-ai-bot" / "wechat_db_state.json"
        if not path.exists() and legacy.exists():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                legacy.replace(path)
                logger.info("[WeChatDBManager] 已迁移 wechat_db_state.json → 账号目录")
            except Exception as e:
                logger.warning(f"[WeChatDBManager] 迁移旧状态文件失败: {e}")
        if not path.exists():
            self._script_groups = _default_script_groups()
            self._keyword_replies = _default_keyword_replies()
            try:
                from src.utils.cloud_sync import get_cloud_client
                client = get_cloud_client()
                cloud_tasks = client.pull_follow_tasks()
                if cloud_tasks:
                    self._auto_follow_queue = cloud_tasks
                    logger.info(f"[WeChatDBManager] 🔄 从同步后端成功恢复 {len(cloud_tasks)} 个自动跟单任务")
                try:
                    cloud_sg = client._get("/api/v1/settings/script_groups", need_auth=True)
                    if cloud_sg and isinstance(cloud_sg, list):
                        self._script_groups = cloud_sg
                        logger.info(f"[WeChatDBManager] 🔄 从同步后端成功恢复 {len(cloud_sg)} 个话术组")
                except Exception:
                    pass
                
                # 新增：从同步客户端拉取云端分流拉群设置并合并还原
                try:
                    cloud_settings = client.pull_settings()
                    if cloud_settings:
                        for item in cloud_settings:
                            if item.get("setting_key") == "identity_routing":
                                val = item.get("setting_val")
                                if val and isinstance(val, dict):
                                    self._identity_routing = val
                                    logger.info("[WeChatDBManager] 🔄 从同步后端成功恢复新友智能分流拉群配置")
                                    break
                except Exception as ir_ex:
                    logger.debug(f"[WeChatDBManager] 从云端同步列表恢复分流配置失败: {ir_ex}")
            except Exception as e:
                logger.debug(f"[WeChatDBManager] 从同步后端备份拉取数据失败: {e}")
            self._persist_snapshot()
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            if "friend_queue" in raw and isinstance(raw["friend_queue"], list):
                self._friend_queue.clear()
                self._friend_queue.extend(copy.deepcopy(x) for x in raw["friend_queue"] if isinstance(x, dict))
            if "auto_follow_queue" in raw and isinstance(raw["auto_follow_queue"], list):
                self._auto_follow_queue.clear()
                for x in raw["auto_follow_queue"]:
                    if isinstance(x, dict):
                        # 热去重清洗：确保 targets 数组中没有重复，防止配置文件无限膨胀
                        if "targets" in x and isinstance(x["targets"], list):
                            x["targets"] = list(dict.fromkeys(str(t).strip() for t in x["targets"] if str(t).strip()))
                        self._auto_follow_queue.append(copy.deepcopy(x))
            if "script_groups" in raw and isinstance(raw["script_groups"], list):
                self._script_groups.clear()
                self._script_groups.extend(copy.deepcopy(x) for x in raw["script_groups"] if isinstance(x, dict))
                
                # 热净化过滤：如果本地缓存的话术组里依然保留有阿里 CDN 占位图片的无效节点，则自动将其净化移除
                has_sg_changed = False
                for sg in self._script_groups:
                    if sg.get("id") == "sg_welcome" and "greetings" in sg:
                        orig_len = len(sg["greetings"])
                        sg["greetings"] = [g for g in sg["greetings"] if not (g.get("type") == "media" and "img.alicdn.com/tfs/TB1" in str(g.get("content", "")))]
                        if len(sg["greetings"]) < orig_len:
                            logger.info("[WeChatDBManager] 🧼 检测到未被修改的破冰话术默认阿里 CDN 占位图节点，已自动执行热净化清除")
                            has_sg_changed = True
                if has_sg_changed:
                    self._persist_snapshot()
            else:
                self._script_groups = _default_script_groups()
            if "tags_queue" in raw and isinstance(raw["tags_queue"], list):
                self._tags_queue.clear()
                self._tags_queue.extend(copy.deepcopy(x) for x in raw["tags_queue"] if isinstance(x, dict))
            if "mass_send_jobs" in raw and isinstance(raw["mass_send_jobs"], list):
                self._mass_send_jobs.clear()
                self._mass_send_jobs.extend(copy.deepcopy(x) for x in raw["mass_send_jobs"] if isinstance(x, dict))
            if "mass_send_queues" in raw and isinstance(raw["mass_send_queues"], list):
                self._mass_send_queues.clear()
                self._mass_send_queues.extend(copy.deepcopy(x) for x in raw["mass_send_queues"] if isinstance(x, dict))
            if "promise_tasks" in raw and isinstance(raw["promise_tasks"], list):
                self._promise_tasks.clear()
                self._promise_tasks.extend(copy.deepcopy(x) for x in raw["promise_tasks"] if isinstance(x, dict))
            if "fulfillment_capabilities" in raw and isinstance(raw["fulfillment_capabilities"], list):
                self._fulfillment_capabilities.clear()
                self._fulfillment_capabilities.extend(copy.deepcopy(x) for x in raw["fulfillment_capabilities"] if isinstance(x, dict))
            else:
                self._fulfillment_capabilities = _default_fulfillment_capabilities()
            if "keyword_replies" in raw and isinstance(raw["keyword_replies"], list):
                self._keyword_replies.clear()
                self._keyword_replies.extend(copy.deepcopy(x) for x in raw["keyword_replies"] if isinstance(x, dict))
                
                # 热净化过滤：如果本地缓存中依然保留有未被用户修改过的星码行空招商和微信助手价格回复，则进行强制净化
                has_changed = False
                cleaned_replies = []
                for item in self._keyword_replies:
                    content = item.get("reply_content", "")
                    # 识别是否为默认招商内容
                    is_dirty_default = (
                        "微信助手基础版仅需299元/月" in content or 
                        "贴牌定制（OEM）" in content or 
                        "微信助手基础版" in content
                    )
                    # 如果是默认的特定招商字眼，则抛弃它
                    if is_dirty_default:
                        logger.info(f"[WeChatDBManager] 🧼 检测到未被修改的微信助手默认招商配置 '{item.get('id')}'，已自动执行热净化清除")
                        has_changed = True
                        continue
                    
                    # 兼容性替换：老旧数据库中若包含“星码行空”自动替换为“xm-bot4”
                    if "星码行空" in content:
                        item["reply_content"] = content.replace("星码行空", "xm-bot4")
                        has_changed = True

                    # 对默认“客服对接”智障话术的热净化升级
                    if item.get("id") == "kr_default_1" and ("对接了专属客服经理" in content or "人工" in item.get("keywords", [])):
                        default_one = _default_keyword_replies()[0]
                        item["reply_content"] = default_one["reply_content"]
                        item["keywords"] = default_one["keywords"]
                        logger.info("[WeChatDBManager] 🧼 对默认客服智障话术配置 'kr_default_1' 自动执行了热净化升级与关键词去噪")
                        has_changed = True

                    cleaned_replies.append(item)
                
                # 如果净化后变为空了，自动置入通用的客服中性模板
                if not cleaned_replies:
                    cleaned_replies = _default_keyword_replies()
                    has_changed = True
                
                self._keyword_replies = cleaned_replies
                if has_changed:
                    self._persist_snapshot()
            else:
                self._keyword_replies = _default_keyword_replies()

            # 恢复履约插件相关表数据
            if "market_plugins" in raw and isinstance(raw["market_plugins"], list):
                self._market_plugins.clear()
                self._market_plugins.extend(copy.deepcopy(x) for x in raw["market_plugins"] if isinstance(x, dict))
            if "plugin_purchases" in raw and isinstance(raw["plugin_purchases"], list):
                self._plugin_purchases.clear()
                self._plugin_purchases.extend(copy.deepcopy(x) for x in raw["plugin_purchases"] if isinstance(x, dict))
            if "withdrawal_records" in raw and isinstance(raw["withdrawal_records"], list):
                self._withdrawal_records.clear()
                self._withdrawal_records.extend(copy.deepcopy(x) for x in raw["withdrawal_records"] if isinstance(x, dict))
            if "identity_routing" in raw and isinstance(raw["identity_routing"], dict):
                self._identity_routing = copy.deepcopy(raw["identity_routing"])
            if "flagship_bonus_sent" in raw:
                self.flagship_bonus_sent = bool(raw["flagship_bonus_sent"])
            if "base_bonus_sent" in raw:
                self.base_bonus_sent = bool(raw["base_bonus_sent"])
            if "professional_bonus_sent" in raw:
                self.professional_bonus_sent = bool(raw["professional_bonus_sent"])
            if "trial_bonus_sent" in raw:
                self.trial_bonus_sent = bool(raw["trial_bonus_sent"])

            logger.info("[WeChatDBManager] 已从本地快照恢复状态")
        except Exception as e:
            logger.warning(f"[WeChatDBManager] 读取本地快照失败，使用默认内存: {e}")
            self._script_groups = _default_script_groups()
            self._keyword_replies = _default_keyword_replies()
            self._fulfillment_capabilities = _default_fulfillment_capabilities()
            self._market_plugins = []
            self._plugin_purchases = []
            self._withdrawal_records = []

    def _persist_snapshot(self) -> None:
        pl = {
            "version": 1,
            "saved_at": datetime.now().isoformat(),
            "friend_queue": copy.deepcopy(self._friend_queue),
            "auto_follow_queue": copy.deepcopy(self._auto_follow_queue),
            "script_groups": copy.deepcopy(self._script_groups),
            "tags_queue": copy.deepcopy(self._tags_queue),
            "mass_send_jobs": copy.deepcopy(self._mass_send_jobs),
            "mass_send_queues": copy.deepcopy(self._mass_send_queues),
            "keyword_replies": copy.deepcopy(self._keyword_replies),
            "promise_tasks": copy.deepcopy(self._promise_tasks),
            "fulfillment_capabilities": copy.deepcopy(self._fulfillment_capabilities),
            "market_plugins": copy.deepcopy(self._market_plugins),
            "plugin_purchases": copy.deepcopy(self._plugin_purchases),
            "withdrawal_records": copy.deepcopy(self._withdrawal_records),
            "identity_routing": copy.deepcopy(self._identity_routing),
            "flagship_bonus_sent": self.flagship_bonus_sent,
            "base_bonus_sent": self.base_bonus_sent,
            "professional_bonus_sent": self.professional_bonus_sent,
            "trial_bonus_sent": self.trial_bonus_sent,
        }
        try:
            _wdb_flush_state(_wdb_state_path_for_active_bot(), pl)
        except Exception as e:
            logger.warning(f"[WeChatDBManager] 写入本地快照失败: {e}")

    def init_db(self):
        """兼容旧调用，无操作"""
        pass



    # ======= 身份引导分流拉群配置 CRUD =======
    def get_identity_routing(self) -> dict:
        return getattr(self, "_identity_routing", {
            "enabled": False,
            "ask_prompt": "您好！请问您目前是我们的普通会员、高级会员，还是尊贵的销冠包用户？",
            "fallback_prompt": "抱歉，没能识别您的身份。您可以直接回复数字 1、2 或 3 哦。",
            "invite_success_reply": "已收到！已为您贴上【{tag_name}】标签，并向您发送了专属交流群【{group_name}】的入群邀请，请在微信中确认加入哦！",
            "invite_fail_reply": "已收到！已为您贴上【{tag_name}】标签。专属交流群【{group_name}】拉群受限，请稍等或联系客服邀请您加入哦~",
            "continuous_detection": False,
            "rules": [],
            "tag_mappings": []
        })

    def update_identity_routing(self, config: dict) -> dict:
        self._identity_routing = {
            "enabled": bool(config.get("enabled", False)),
            "ask_prompt": str(config.get("ask_prompt", "")),
            "fallback_prompt": str(config.get("fallback_prompt", "")),
            "invite_success_reply": str(config.get("invite_success_reply", "")),
            "invite_fail_reply": str(config.get("invite_fail_reply", "")),
            "continuous_detection": bool(config.get("continuous_detection", False)),
            "rules": list(config.get("rules", [])),
            "tag_mappings": list(config.get("tag_mappings", []))
        }
        self._persist_snapshot()

        # 实时同步推送到远程同步云存储
        try:
            from src.utils.cloud_sync import get_cloud_client
            client = get_cloud_client()
            client.save_setting("identity_routing", self._identity_routing)
            logger.info("[WeChatDBManager] 🔄 分流拉群配置已同步推送至远程云数据库")
        except Exception as e:
            logger.warning(f"[WeChatDBManager] 同步推送分流拉群配置至远程失败: {e}")

        return self._identity_routing