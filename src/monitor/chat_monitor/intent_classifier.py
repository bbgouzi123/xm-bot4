"""
意图分类器（IntentClassifier）
极速纯内存分类，耗时 < 1ms，不依赖任何 AI 接口。
根据行业词库 JSON 预设（src/crm/industry_presets/*.json）判断意图等级。
"""
import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 行业预设文件目录
_PRESETS_DIR = os.path.join(os.path.dirname(__file__), "../crm/industry_presets")

# 缓存：{ account_id -> (preset_name, preset_data) }
_PRESET_CACHE: dict[str, tuple[str, dict]] = {}


class IntentLevel(Enum):
    NORMAL = "normal"
    ALERT_BODY = "alert_body"           # 身体异常（最高优先级）
    ALERT_COMPLAINT = "alert_complaint" # 客诉危机
    HIGH_VALUE = "high_value"           # 高价值升单意向
    SENSITIVE_CLAIM = "sensitive_claim" # 广告法敏感词


@dataclass
class IntentResult:
    level: IntentLevel
    matched_keyword: str = ""
    placeholder_reply: str = ""
    fallback_reply: str = ""
    request_card_template: str = ""

    def build_request_card(self, friend_name: str, message: str) -> str:
        return self.request_card_template.format(
            friend_name=friend_name,
            message=message[:200]  # 截断过长的消息
        )


def _load_preset_for_account(account_id: str) -> dict:
    """加载账号对应的行业预设（带缓存）"""
    if account_id in _PRESET_CACHE:
        return _PRESET_CACHE[account_id][1]

    # 从账号的行业配置中查找 preset_name
    preset_name = "general"
    try:
        from src.api.instance_settings_api import load_instance_settings
        inst_settings = load_instance_settings(account_id)
        preset_name = inst_settings.get("decision_gateway_preset", "general")
    except Exception:
        pass

    preset_data = _load_preset_file(preset_name)
    _PRESET_CACHE[account_id] = (preset_name, preset_data)
    return preset_data


def _load_preset_file(preset_name: str) -> dict:
    """从磁盘加载行业词库 JSON（找不到则降级为通用）"""
    candidates = [preset_name, "general"]
    for name in candidates:
        path = os.path.join(_PRESETS_DIR, f"{name}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[意图分类] 加载行业预设 '{name}' 失败: {e}")
    return {}


def _match_keywords(text: str, keywords: list[str]) -> str:
    """返回第一个命中的关键词，未命中返回空字符串"""
    for kw in keywords:
        if kw and kw in text:
            return kw
    return ""


def classify_intent(account_id: str, message: str, is_group: bool = False) -> IntentResult:
    """
    对单条消息进行意图分类。
    优先级：身体异常 > 客诉危机 > 高价值 > 广告法敏感 > 普通
    群消息默认不触发决策网关（除非有特殊配置）。
    """
    if is_group:
        return IntentResult(level=IntentLevel.NORMAL)

    preset = _load_preset_for_account(account_id)
    if not preset:
        return IntentResult(level=IntentLevel.NORMAL)

    p_replies = preset.get("placeholder_replies", {})
    f_replies = preset.get("fallback_replies", {})
    cards = preset.get("request_card_templates", {})

    # 按优先级逐级检测
    checks = [
        (IntentLevel.ALERT_BODY,      "alert_body_keywords"),
        (IntentLevel.ALERT_COMPLAINT, "alert_complaint_keywords"),
        (IntentLevel.HIGH_VALUE,      "high_value_keywords"),
        (IntentLevel.SENSITIVE_CLAIM, "sensitive_claim_keywords"),
    ]

    for level, kw_field in checks:
        keywords = preset.get(kw_field, [])
        hit = _match_keywords(message, keywords)
        if hit:
            key = level.value
            return IntentResult(
                level=level,
                matched_keyword=hit,
                placeholder_reply=p_replies.get(key, "收到您的消息，正在为您处理，请稍候..."),
                fallback_reply=f_replies.get(key, "感谢您的耐心等待，稍后我们会第一时间与您联系！"),
                request_card_template=cards.get(key, "📋 {friend_name} 发送了需要审批的消息：\n{message}"),
            )

    return IntentResult(level=IntentLevel.NORMAL)


def invalidate_cache(account_id: str = None):
    """使缓存失效（在用户切换行业预设后调用）"""
    if account_id:
        _PRESET_CACHE.pop(account_id, None)
    else:
        _PRESET_CACHE.clear()
