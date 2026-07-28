import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from src.crm.profile_manager import ProfileManager
from src.crm.tag_manager import TagEntry

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".xm-ai-bot"
FR_LOG_FILE = CONFIG_DIR / "friend_request_logs.json"

def load_fr_logs() -> list:
    """加载好友请求日志"""
    try:
        if FR_LOG_FILE.exists():
            return json.loads(FR_LOG_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return []

def save_fr_logs(logs: list):
    """保存好友请求日志"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        FR_LOG_FILE.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        logger.error(f"保存好友请求日志失败: {e}")

def report_cloud_event(event_data: dict):
    """异步上报好友请求事件到同步后端"""
    def _push():
        try:
            from src.utils.cloud_sync import get_cloud_client
            get_cloud_client().report_event("friend_request", event_data)
        except Exception as e:
            logger.debug(f"[好友请求·同步后端] 上报失败: {e}")
    threading.Thread(target=_push, daemon=True, name="friend-request-cloud").start()

def create_new_friend_profile(nickname: str, friend_info: dict) -> str:
    """为新通过的好友创建初始客户画像"""
    try:
        initial_tags = [
            TagEntry(category="social", subcategory="source", value="新好友申请", confidence=1.0, source="friend_add"),
            TagEntry(category="social", subcategory="relationship", value="陌生人", confidence=0.8, source="friend_add"),
            TagEntry(category="business", subcategory="stage", value="首次接触", confidence=1.0, source="friend_add"),
        ]
        verify_msg = friend_info.get("verify_message", "")
        if verify_msg:
            initial_tags.extend(analyze_verify_message(verify_msg))

        profile = ProfileManager().update_tags(
            wxid=friend_info.get("wxid") or nickname,
            new_tags=initial_tags,
            source="friend_add",
            nickname=nickname,
        )
        tag_summary = profile.get_tag_summary()
        print(f"[CRM] 新好友画像: {nickname} → {tag_summary}")
        return tag_summary
    except Exception as e:
        logger.error(f"[CRM] 创建新好友画像失败 {nickname}: {e}")
        return ""

def analyze_verify_message(message: str) -> list:
    """分析好友申请附加的校验信息"""
    if not message:
        return []
    tags = []
    source_patterns = {
        "推荐": ("social", "source", "朋友推荐"), "介绍": ("social", "source", "转介绍"),
        "朋友圈": ("social", "source", "朋友圈"), "群里": ("social", "source", "群聊"),
        "群聊": ("social", "source", "群聊"), "抖音": ("social", "source", "抖音"),
        "小红书": ("social", "source", "小红书"), "58同城": ("social", "source", "58同城"),
        "百度": ("social", "source", "百度")
    }
    for keyword, (cat, sub, val) in source_patterns.items():
        if keyword in message:
            tags.append(TagEntry(category=cat, subcategory=sub, value=val, confidence=0.8, source="friend_add"))
            break
            
    intent_keywords = {
        "咨询": ("business", "intent", "意向-中等"), "了解": ("business", "intent", "意向-观望"),
        "想买": ("business", "intent", "意向-强烈"), "购买": ("business", "intent", "意向-强烈"),
        "多少钱": ("business", "intent", "意向-强烈"), "价格": ("business", "intent", "意向-中等"),
        "办理": ("business", "intent", "意向-强烈")
    }
    for keyword, (cat, sub, val) in intent_keywords.items():
        if keyword in message:
            tags.append(TagEntry(category=cat, subcategory=sub, value=val, confidence=0.7, source="friend_add"))
            break
            
    need_keywords = {
        "保险": ("business", "need", "保险咨询"), "车险": ("business", "need", "车险"),
        "房子": ("business", "need", "房产"), "装修": ("business", "need", "装修"),
        "培训": ("business", "need", "培训"), "课程": ("business", "need", "课程"),
        "加盟": ("business", "need", "加盟"), "代理": ("business", "need", "代理")
    }
    for keyword, (cat, sub, val) in need_keywords.items():
        if keyword in message:
            tags.append(TagEntry(category=cat, subcategory=sub, value=val, confidence=0.7, source="friend_add"))
            break
    return tags

def auto_enroll_sdr(friend_wxid: str, nickname: str, verify_msg: str) -> str:
    """自动挂载长期跟单任务"""
    if not friend_wxid:
        return ""
    try:
        from src.utils.db_manager import WeChatDBManager
        import uuid
        db = WeChatDBManager()
        existing_tasks = db.get_auto_follow_tasks()
        for task in existing_tasks:
            if task.get("status") == "active":
                targets = task.get("targets") or []
                if friend_wxid in targets:
                    logger.info(f"[SDR] 好友 {nickname}({friend_wxid}) 已在跟单任务中，跳过")
                    return ""
        scenario = "产品咨询跟单"
        if any(k in verify_msg for k in ("群", "入群", "拉我")):
            scenario = "社群引流跟单"
        elif any(k in verify_msg for k in ("代理", "合作", "加盟")):
            scenario = "代理加盟跟单"
        payload = {
            "task_id": f"afl_auto_{uuid.uuid4().hex[:8]}",
            "targets": [friend_wxid],
            "follow_days": 7,
            "follow_frequency": "front3_then_interval2",
            "time_range_start": "09:00",
            "time_range_end": "20:00",
            "follow_scenario": scenario,
            "use_ai": True,
            "fallback_text": "您好！请问您目前主要关注我们系统的哪些自动化功能呢？",
            "max_daily": 50,
            "status": "active",
            "created_at": datetime.now().isoformat(),
        }
        db.add_auto_follow_task(payload)
        logger.info(f"[SDR] 已自动为新好友 {nickname}({friend_wxid}) 开启 SDR 跟进")
        return scenario
    except Exception as e:
        logger.error(f"[SDR] 自动挂载跟单长程任务失败: {e}")
        return ""
