import asyncio
import logging
import re
import time
from typing import Any
from .intent_fallback import apply_fallback_routing, check_group_announcement_receipt

logger = logging.getLogger(__name__)


async def determine_intent_and_routing(
    engine: Any,
    name: str,
    actual_message: str,
    user_name: str,
    account_id: str,
    context_msgs: list,
    chat_round: int,
    is_group: bool = False,
    wxid: str = None
) -> tuple[str, bool, Any, Any, Any]:
    """进行意图分类、CRM画像标记、飞书/SDR跟单判断并进行路由决策"""

    # 0. 群公告/所有人自动确认回执拦截器
    from src.api.config_api.privacy_shield import _get_reply_config_isolated
    from src.api.config_api import _load_configs
    
    reply_cfg = {}
    try:
        reply_cfg = _get_reply_config_isolated(account_id)
    except Exception:
        pass
    
    configs = {}
    try:
        configs = _load_configs()
    except Exception:
        pass

    receipt_text = await check_group_announcement_receipt(
        engine=engine, name=name, actual_message=actual_message,
        user_name=user_name, account_id=account_id, is_group=is_group,
        reply_cfg=reply_cfg, configs=configs
    )
    if receipt_text:
        return "群公告自动回执", False, receipt_text, None, None

    # 提前获取当前行业的专属配置，供后面的关键词阻断 or AI 路由使用
    industry_profile = None
    try:
        from src.crm.industry_config import IndustryConfigManager as _ICM
        _global_config = _ICM(account_id="global")
        inst_profile_id = ""
        try:
            from src.api.instance_settings_api import load_instance_settings
            inst_settings = load_instance_settings(account_id)
            inst_profile_id = inst_settings.get("industry_profile_id", "")
        except Exception as e:
            logger.warning(f"[CRM] 获取微信专属行业ID失败: {e}")

        if inst_profile_id:
            industry_profile = _global_config.get_profile_by_id(inst_profile_id)
        if not industry_profile:
            industry_profile = _global_config.get_active_profile()

        if industry_profile:
            logger.info(
                f"[CRM] 当前微信账号 '{account_id}' 正在使用的行业配置: "
                f"名称='{industry_profile.name}' (id={industry_profile.id})，"
                f"已配电话='{getattr(industry_profile, 'phone', '') or '未配置'}'，"
                f"已配地址='{getattr(industry_profile, 'address', '') or '未配置'}'，"
                f"物料配图数={len(getattr(industry_profile, 'materials', []))}"
            )
        else:
            logger.warning(f"[CRM] 当前微信账号 '{account_id}' 未匹配到任何有效的行业配置，将使用系统白盒默认行为")
    except Exception as e_prof:
        logger.error(f"[CRM] 提前获取行业配置失败: {e_prof}")

    try:
        from src.utils.db_manager import WeChatDBManager
        db = WeChatDBManager()
        rules = db.get_all_keyword_replies()

        matched_rule = None
        for rule in rules:
            if not rule.get("is_active", True):
                continue

            scope = rule.get("scope", "all")
            if scope == "friend" and is_group:
                continue
            if scope == "group" and not is_group:
                continue

            keywords = rule.get("keywords", [])
            match_type = rule.get("match_type", "fuzzy")

            triggered = False
            for kw in keywords:
                if not kw:
                    continue
                if match_type == "exact":
                    # 1. 整体精确匹配
                    if actual_message.strip() == kw.strip():
                        triggered = True
                        break
                    # 2. 支持消息合并缓冲后的分段精确匹配 (以 '。' 分隔)
                    parts = [p.strip() for p in actual_message.split("。") if p.strip()]
                    if any(p == kw.strip() for p in parts):
                        triggered = True
                        break
                else:  # fuzzy
                    if kw.strip() in actual_message:
                        # 🌟 智能旁路过滤：若为短关键词模糊匹配 (如'小瑞','客服','你好'，字符长度<=4)，
                        # 且除了该关键词之外还有其他业务文本内容，则放行该拦截，交给 AI 智能组织高情商应答。
                        kw_strip = kw.strip()
                        if len(kw_strip) <= 4:
                            rest = actual_message.replace(kw_strip, "")
                            # 移除常见标点符号和空白字符
                            rest_clean = re.sub(r'[\s，。？！；：,.\?!;:\(\)（）—\-\_\*]+', '', rest)
                            if len(rest_clean) > 0:
                                logger.info(f"[关键词放行] 命中短词 '{kw_strip}'，但除该词外仍有业务内容 '{rest_clean}'，放行交由 AI 处理。")
                                continue
                        
                        triggered = True
                        break

            if triggered:
                matched_rule = rule
                break

        if matched_rule:
            # 💡 人性化优化：如果是系统的默认客服/联系规则 "kr_default_1" (包含"联系","客服","人工","电话"等关键词)
            # 且当前激活的行业中已经配置了官方电话（phone）或详细地址（address），则不应死板地使用默认固定客服回复，
            # 而是主动跳过该匹配拦截，交给大模型根据 System Prompt 中录入的真实联系方式进行高情商智能应答。
            if matched_rule.get("id") == "kr_default_1":
                has_phone = bool(industry_profile and getattr(industry_profile, "phone", "") and getattr(industry_profile, "phone").strip())
                has_address = bool(industry_profile and getattr(industry_profile, "address", "") and getattr(industry_profile, "address").strip())
                if has_phone or has_address:
                    logger.info(
                        f"[关键词回复] 好友 {name} 询问客服/电话/地址，但当前行业已配置电话('{getattr(industry_profile, 'phone', '')}')"
                        f"或地址('{getattr(industry_profile, 'address', '')}')，将跳过默认规则拦截，由 LLM 智能应答。"
                    )
                    matched_rule = None

        if matched_rule:
            logger.info(f"[关键词回复] 好友 {name} 的消息 \"{actual_message}\" 触发规则: {matched_rule['keywords']}")
            if matched_rule.get("delete_on_reply"):
                try:
                    logger.info(f"[关键词回复] 检测到回复后自动删除，正在删除规则: {matched_rule.get('id')}")
                    db.delete_keyword_reply(matched_rule["id"])
                except Exception as del_err:
                    logger.error(f"[关键词回复] 自动删除规则异常: {del_err}")
            return "关键词自动回复", False, matched_rule["reply_content"], None, None
    except Exception as kw_ex:
        logger.error(f"[关键词回复] 拦截匹配异常: {kw_ex}")

    if not is_group:
        try:
            from src.crm.account_data import get_account_settings
            reply_cfg = get_account_settings(account_id).get("reply", {})
            friend_daily_limit = reply_cfg.get("friend_daily_limit", 0)
            if friend_daily_limit > 0:
                from .base import _chat_daily_counter
                key = f"friend_reply_{name}"
                used_count = _chat_daily_counter.get_count(key, account_id)
                if used_count >= friend_daily_limit:
                    friend_limit_reply = reply_cfg.get("friend_limit_reply", "您今天的免费咨询额度已用完，请联系客服解锁更多次数。")
                    logger.info(f"[限额拦截] 好友 {name} 的 AI 回复已达到今日上限 {friend_daily_limit}，已返回超限话术。")
                    return "超额提示", False, friend_limit_reply, None, None
        except Exception as limit_ex:
            logger.error(f"[限额拦截] 校验异常: {limit_ex}")

    from src.ai.intent_classifier import IntentClassifier
    from src.ai.prompt_router import PromptRouter
    from src.crm.industry_config import IndustryConfigManager as _ICM

    intent_result = IntentClassifier.classify(actual_message, context_msgs)
    intent = intent_result["intent"]

    # 优先从通讯录缓存获取真实 wxid，避免使用临时 nickname 创建重复画像
    contact_wxid = wxid or name
    if not wxid:
        try:
            from src.utils.contacts_cache import contacts_cache
            all_friends = contacts_cache.get_friends(account_id)
            found_wxid = next((f.get("wxid", "") for f in all_friends if (f.get("name") or "").strip() == name.strip() or (f.get("remark") or "").strip() == name.strip()), "")
            if found_wxid:
                contact_wxid = found_wxid
        except Exception:
            pass

    old_intent = None
    try:
        profile = engine._profile_manager.get_profile(contact_wxid, nickname=user_name or name)
        old_intent_tag = profile.get_tag("intent")
        old_intent = old_intent_tag.value if old_intent_tag else None
    except Exception as e:
        logger.error(f"[CRM] 获取旧意图异常: {e}")

    from src.crm.tag_manager import TagEntry
    
    # 将英文意图转换为中文展示名，方便打标微信好友和后台查看
    INTENT_NAMES_CN = {
        "greeting": "问候",
        "price_inquiry": "价格咨询",
        "material_request": "索取资料",
        "negative": "负面情绪",
        "casual_chat": "闲聊",
        "business": "业务咨询",
        "friend_accepted": "新加好友",
        "transfer_to_manual": "转人工"
    }
    intent_cn = INTENT_NAMES_CN.get(intent, intent)

    try:
        engine._profile_manager.update_tags(
            wxid=contact_wxid,
            new_tags=[TagEntry(
                category="business",
                subcategory="intent",
                value=f"意图-{intent_cn}",
                confidence=intent_result.get("confidence", 0.8),
                source="intent_classification"
            )],
            source="intent_classification",
            nickname=user_name or name
        )
    except Exception as e:
        logger.error(f"[CRM] 写入意图画像异常: {e}")

    # 检查是否触发负面情绪、转人工意图或敏感词
    SENSITIVE_WORDS = ["退钱", "起诉", "曝光", "假货", "欺骗", "报警", "维权", "投诉", "骗子", "垃圾", "差评", "举报", "拉黑"]
    is_sensitive_matched = any(sw in actual_message for sw in SENSITIVE_WORDS)

    if intent in ("negative", "transfer_to_manual") or is_sensitive_matched:
        try:
            from src.utils.alert_notifier import alert_notifier
            from src.utils.websocket_manager import ws_manager
            from src.utils.feishu_notifier import feishu_notifier

            alert_title = "🚨 客户触发紧急状态告警" if intent == "negative" or is_sensitive_matched else "👤 客户呼叫人工告警"
            alert_content = (
                f"**微信账号**: {account_id}\n"
                f"**客户姓名**: {user_name or name} ({name})\n"
                f"**触发原因**: {intent_cn if intent in ('negative', 'transfer_to_manual') else '匹配到敏感词'}\n"
                f"**触发消息**: {actual_message}\n\n"
                f"请相关人员尽快处理！"
            )

            # 1. 发送消息中心通知
            asyncio.create_task(alert_notifier.send_user_notification(
                title=alert_title,
                body=f"客户 {user_name or name} 触发投诉/转人工：{actual_message}",
                category="alert"
            ))

            # 2. WebSocket 桌面弹窗
            asyncio.create_task(ws_manager.broadcast_alert(
                level="error",
                title=alert_title,
                content=alert_content.replace("**", "")
            ))

            # 3. 飞书告警卡片
            asyncio.create_task(feishu_notifier.send_alert_card(alert_title, alert_content, level="fatal"))
        except Exception as alert_err:
            logger.error(f"[告警中心] 发送实时投诉告警异常: {alert_err}")

    if old_intent and old_intent != f"意图-{intent_cn}":
        try:
            from src.utils.feishu_notifier import feishu_notifier
            from src.utils.alert_notifier import alert_notifier
            from src.utils.websocket_manager import ws_manager

            title = "🎯 客户意图发生偏移通知"
            content = (
                f"**微信账号**: {account_id}\n"
                f"**客户姓名**: {user_name or name} ({name})\n"
                f"**原意图**: {old_intent}\n"
                f"**新意图**: 意图-{intent_cn} ({intent_result.get('confidence', 0.8) * 100:.1f}% 置信度)\n"
                f"**最新聊天消息**: {actual_message}\n\n"
                f"请销售人员及时关注并考虑人工介入唤醒！"
            )
            asyncio.create_task(feishu_notifier.send_alert_card(title, content, level="warning"))

            # 消息中心通知
            asyncio.create_task(alert_notifier.send_user_notification(
                title=title,
                body=f"客户 {user_name or name} 意图从 [{old_intent}] 偏移为 [意图-{intent_cn}]！",
                category="alert"
            ))

            # WebSocket 桌面弹窗
            level = "error" if intent in ("negative", "transfer_to_manual") else "info"
            asyncio.create_task(ws_manager.broadcast_alert(
                level=level,
                title=title,
                content=content.replace("**", "")
            ))
        except Exception as fe:
            logger.error(f"[飞书通知] 意图变化通知发送异常: {fe}")

    is_high_intent = False
    if intent in ("buy", "price", "强烈购买", "意向-强烈") or any(k in actual_message for k in ("多少钱", "怎么购买", "购买", "怎么买", "怎么收费", "价格")):
        is_high_intent = True

    if is_high_intent:
        try:
            from src.crm.account_data import get_account_settings
            reply_settings = get_account_settings(account_id).get("reply", {})
            if reply_settings.get("auto_follow", False):
                from src.monitor.friend_request_store import auto_enroll_sdr
                auto_enroll_sdr(contact_wxid, user_name or name, actual_message)
        except Exception as e:
            logger.error(f"[SDR] 自动挂载 SDR 跟单失败: {e}")

    # 复用前面已提取的行业配置，若未成功获取则进行兜底
    if not industry_profile:
        try:
            from src.crm.industry_config import IndustryConfigManager as _ICM
            _global_config = _ICM(account_id="global")
            industry_profile = _global_config.get_active_profile()
        except Exception:
            pass

    fixed_reply, ai_prompt, file_to_send = PromptRouter.route(
        intent=intent, message=actual_message, industry_config=industry_profile,
        chat_round=chat_round, history_messages=context_msgs,
        session_id=contact_wxid, account_id=account_id
    )

    fixed_reply, ai_prompt, file_to_send = await apply_fallback_routing(
        intent=intent, actual_message=actual_message, fixed_reply=fixed_reply,
        ai_prompt=ai_prompt, file_to_send=file_to_send, context_msgs=context_msgs,
        chat_round=chat_round, name=name, account_id=account_id,
        industry_profile=industry_profile, wxid=contact_wxid
    )
    return intent, is_high_intent, fixed_reply, ai_prompt, file_to_send

