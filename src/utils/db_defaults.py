import copy
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)


def _default_tags_queue() -> List[Dict]:
    return [
        {"id": "t1", "name": "高意向客户", "color": "red", "created_at": "2026-04-11T12:00:00"},
        {"id": "t2", "name": "待跟进", "color": "orange", "created_at": "2026-04-11T12:00:00"},
        {"id": "t3", "name": "已报价未成交", "color": "blue", "created_at": "2026-04-11T12:00:00"},
        {"id": "t4", "name": "沉默超过3天", "color": "gray", "created_at": "2026-04-11T12:00:00"},
        {"id": "t5", "name": "VIP重要高客单", "color": "purple", "created_at": "2026-04-11T12:00:00"},
        {"id": "t6", "name": "老客户复购", "color": "green", "created_at": "2026-04-11T12:00:00"},
    ]


def _default_keyword_replies() -> List[Dict]:
    return [
        {
            "id": "kr_default_1",
            "keywords": ["联系", "客服"],
            "match_type": "fuzzy",
            "reply_content": "您好！我是您的专属 AI 获客专家，已收到您的消息。您可以直接在此留下您的具体需求，我会随时为您解答。",
            "is_active": True,
            "created_at": "2026-05-23T12:00:00"
        }
    ]


def _default_script_groups() -> List[Dict]:
    return [
        {
            "id": "sg_welcome",
            "name": "新客户破冰迎新话术",
            "description": "用于新好友加粉通过后的首次招呼与自我介绍，包含破冰迎新语及 AI 智能对话的混合链式投递",
            "created_at": "2026-05-23T12:00:00",
            "greetings": [
                {
                    "id": "node_welcome_1",
                    "type": "text",
                    "delay": 2,
                    "content": "您好！我是您的专属 AI SDR 助手。很高兴能与您建立联系！[玫瑰]"
                },
                {
                    "id": "node_welcome_3",
                    "type": "ai_chat",
                    "delay": 5,
                    "agent_id": "",
                    "content": "请针对新朋友 {nickname} 写一句温馨的破冰引导话语，着重询问他对自动化跟单的看法。"
                }
            ]
        },
        {
            "id": "sg_awaken",
            "name": "沉睡客户触达话术",
            "description": "用于已加粉但长期没有回复的沉默客户，进行福利引导与互动唤醒",
            "created_at": "2026-05-23T12:00:00",
            "greetings": [
                {
                    "id": "node_awaken_1",
                    "type": "text",
                    "delay": 2,
                    "content": "哈喽，向您同步一个好消息！我们系统刚刚更新了全新的 4.0 大厂级任务控制中枢！"
                },
                {
                    "id": "node_awaken_2",
                    "type": "text",
                    "delay": 4,
                    "content": "这是我们最新的产品功能介绍白皮书，您可以先了解一下："
                }
            ]
        }
    ]


def _default_fulfillment_capabilities() -> List[Dict]:
    return [
        {
            "key": "web_snapshot",
            "name": "网页访问静默截图",
            "safety_level": 2,
            "enabled": True,
            "config": {"whitelist_domains": ["*"]}
        },
        {
            "key": "download_media",
            "name": "流媒体去水印下载",
            "safety_level": 1,
            "enabled": True,
            "config": {"max_file_size_mb": 50}
        },
        {
            "key": "sys_control",
            "name": "系统高级控制(关机/删文件)",
            "safety_level": 3,
            "enabled": False,
            "config": {"allowed_commands": []}
        }
    ]


def wechat_db_flush_before_switch(previous_dir_name: str) -> None:
    """在全局活跃微信切换前，将内存中队列写入上一账号目录。"""
    if not previous_dir_name:
        return
    from src.utils.db_manager import WeChatDBManager
    inst = WeChatDBManager()
    from src.crm.account_data import get_account_data_dir
    path = Path(get_account_data_dir(previous_dir_name)) / "wechat_db_state.json"
    pl = {
        "version": 1,
        "saved_at": datetime.now().isoformat(),
        "friend_queue": copy.deepcopy(inst._friend_queue),
        "auto_follow_queue": copy.deepcopy(inst._auto_follow_queue),
        "script_groups": copy.deepcopy(inst._script_groups),
        "tags_queue": copy.deepcopy(inst._tags_queue),
        "mass_send_jobs": copy.deepcopy(inst._mass_send_jobs),
        "mass_send_queues": copy.deepcopy(inst._mass_send_queues),
        "keyword_replies": copy.deepcopy(inst._keyword_replies),
        "promise_tasks": copy.deepcopy(inst._promise_tasks),
        "fulfillment_capabilities": copy.deepcopy(inst._fulfillment_capabilities),
        "market_plugins": copy.deepcopy(inst._market_plugins),
        "plugin_purchases": copy.deepcopy(inst._plugin_purchases),
        "withdrawal_records": copy.deepcopy(inst._withdrawal_records),
        "identity_routing": copy.deepcopy(inst._identity_routing),
        "flagship_bonus_sent": inst.flagship_bonus_sent,
        "base_bonus_sent": inst.base_bonus_sent,
        "professional_bonus_sent": inst.professional_bonus_sent,
        "trial_bonus_sent": inst.trial_bonus_sent,
    }
    try:
        from src.utils.db_manager import _wdb_flush_state
        _wdb_flush_state(path, pl)
    except Exception as e:
        logger.warning(f"[WeChatDBManager] 切换前落盘失败: {e}")


def wechat_db_reload_after_switch() -> None:
    """切换 _active_wxid 后，从新账号目录加载获客/跟单/标签快照。"""
    from src.utils.db_manager import WeChatDBManager
    inst = WeChatDBManager()
    inst._friend_queue.clear()
    inst._auto_follow_queue.clear()
    inst._script_groups.clear()
    inst._mass_send_jobs.clear()
    inst._mass_send_queues.clear()
    inst._keyword_replies.clear()
    inst._promise_tasks.clear()
    inst._fulfillment_capabilities.clear()
    inst._market_plugins.clear()
    inst._plugin_purchases.clear()
    inst._withdrawal_records.clear()
    inst.flagship_bonus_sent = False
    inst.base_bonus_sent = False
    inst.professional_bonus_sent = False
    inst.trial_bonus_sent = False
    inst._tags_queue = _default_tags_queue()
    # 显式重置智能引导分流配置，防止上一个账号的数据残留在单例中污染新账号
    inst._identity_routing = {
        "enabled": False,
        "ask_prompt": "您好！请问您目前是我们的普通会员、高级会员，还是尊贵的销冠包用户？",
        "fallback_prompt": "抱歉，没能识别您的身份。您可以直接回复数字 1、2 或 3 哦。",
        "invite_success_reply": "已收到！已为您贴上【{tag_name}】标签，并向您发送了专属交流群【{group_name}】的入群邀请，请在微信中确认加入哦！",
        "invite_fail_reply": "已收到！已为您贴上【{tag_name}】标签。专属交流群【{group_name}】拉群受限，请稍等或联系客服邀请您加入哦~",
        "continuous_detection": False,
        "rules": [],
        "tag_mappings": []
    }
    inst._load_snapshot_if_exists()
