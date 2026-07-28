"""
朋友圈 AI 评论生成与发送 — 从 moment_interact_action.py 拆分

包含：
- _do_ai_comment: 生成并发送 AI 评论
- _reopen_popup_robust: 点赞后重新弹出互动浮层
"""
import logging
import random
import time
import pyperclip
import uiautomation as uia

from src.uia.retry import try_click, physical_click
from src.utils.daily_counter import DailyCounter
from src.utils.moment_config import (
    human_delay, record_interaction, validate_comment,
)
from src.monitor.moment_utils import (
    reopen_interaction_popup, click_interaction_area,
)
from src.monitor.moment_insight_crm import (
    parse_comment_and_insight as _parse_comment_and_insight,
    update_crm_from_insight as _update_crm_from_insight,
)

from src.utils.safe_uia import safe_exists as _se

logger = logging.getLogger(__name__)
_daily_counter = DailyCounter()


def _do_ai_comment(manager, sns_window, comment_btn, author_name, post_text,
                   fingerprint, account_id, settings,
                   item_name_raw: str = "", media_hint: str = "",
                   moment_item=None, precheck_comment: str = "") -> bool:
    """生成并发送 AI 评论（对齐竞品 YokoAIBot_v2 L3339-3435）。

    升级要点：
    - prompt 增加图片/视频/链接上下文感知
    - GAP 1: AI 返回"无需评论"时跳过
    - 评论编辑框优先从 moment_item 控件查找（竞品 L3386）
    - 竞品 L3408: 验证粘贴内容是否填入编辑框
    - 评论发送增加兜底确认（Send 按钮 + 像素坐标）

    Returns:
        True 如果评论成功发送
    """
    profile_context = ""
    try:
        from src.crm.profile_manager import ProfileManager
        from src.utils.chat_history import ChatHistoryManager
        current_bot_wxid = getattr(manager.driver, 'bot_wxid', 'main')
        pm_cm = ProfileManager(account_id=current_bot_wxid)
        target_wxid_cm = f"nick_{author_name}"
        for p in pm_cm.get_all_profiles():
            if p.nickname == author_name:
                target_wxid_cm = p.wxid
                break
        p = pm_cm.get_profile(target_wxid_cm)
        tags_str = ", ".join([t.value for t in p.tags]) if p.tags else "暂无"
        summary = p.conversation_summary if getattr(p, 'conversation_summary', '') else "暂无"
        ch = ChatHistoryManager(account_id=current_bot_wxid)
        history_msgs = ch.get_context(target_wxid_cm, window_size=5)
        if history_msgs:
            history_str = "\n".join([
                f"{'我' if m['role'] == 'assistant' else '客户'}: {m['content']}"
                for m in history_msgs
            ])
        else:
            history_str = "暂无"
        profile_context = f"TA的标签: {tags_str}\n历史私聊摘要: {summary}\n最近私聊记录:\n{history_str}"
    except Exception as e:
        logger.debug(f"[智能评论] 提取联系人属性与私聊历史失败: {e}")

    media_context = ""
    if media_hint:
        media_context = (
            f"\n该朋友圈还包含以下媒体内容：{media_hint}\n"
            f"请在评论中适当关联这些媒体内容（如图片可以夸拍得好看、视频可以说好有趣等），"
            f"让评论更加走心自然。"
        )

    content_desc = post_text if post_text.strip() else "（纯图片/视频动态，无文字）"

    comment_prompt = (
        f"你现在正在刷微信朋友圈，请针对以下好友发表的动态写一句简短实在的评论"
        f"（严格控制在15个字以内，像熟人好友之间的语气，不要像客服或机器）。\n"
        f"如果该动态实在无法评论（如纯广告、无意义内容），请回复'无需评论'。\n"
        f"可以参考以下关于TA的情报档案来进行关联发挥（如果没有情报可只针对内容发言）：\n{profile_context}\n"
        f"朋友圈文字信息：'{content_desc}'"
        f"{media_context}\n"
        f"要求：只输出应该发出去的那句话，不加任何解释文字或引号标识。"
    )

    try:
        # ===== 如果上游 _ai_precheck 已预判生成评论，直接复用，跳过 AI 调用 =====
        if precheck_comment and precheck_comment != '__SKIP__':
            logger.info(f"[智能评论] 复用预判结果，跳过二次 AI 调用: {precheck_comment!r}")
            reply_text = precheck_comment
            # 直接跳过 AI 调用，进入发送流程
            try_click(comment_btn)
            time.sleep(1.0)
            edit_box = _find_comment_edit_box(sns_window, moment_item)
            if edit_box is not None and _se(edit_box, 1.0):
                edit_box.SetFocus()
                time.sleep(0.5)
                pyperclip.copy(reply_text)
                time.sleep(0.5)
                uia.SendKeys('{Ctrl}v')
                time.sleep(0.5)
                try:
                    val_pattern = edit_box.GetValuePattern()
                    if not val_pattern or not val_pattern.Value:
                        logger.warning("[智能评论] 评论内容未成功输入")
                        return False
                except Exception:
                    pass
                uia.SendKeys('{Enter}')
                time.sleep(0.5)
                _try_fallback_send(sns_window, moment_item, edit_box)
                _daily_counter.increment("comment", account_id)
                record_interaction(author_name, post_text, "comment", fingerprint, account_id)
                manager._persist_log(
                    "comment", author_name, post_text,
                    reply_text=reply_text, fingerprint=fingerprint
                )
                logger.info(f"[智能评论] ✅ 已成功评论(复用预判) {author_name}: {reply_text}")
                time.sleep(human_delay("comment"))
                return True
            else:
                logger.warning(f"[智能评论] 复用预判时未找到评论编辑框: {author_name}")
                return False

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            svc = manager.ai_service
            # 优先从已注册的 _agent_map["chat"] 取，避免 agent_id 属性为空/0 导致 bot_id=0 传入 Coze API
            if hasattr(svc, 'get_agent_id_for_role'):
                bot_id = svc.get_agent_id_for_role("chat") or ''
            else:
                bot_id = getattr(svc, 'agent_id', '') or ''
            logger.info(f"[AI评论] 使用 bot_id={bot_id!r} 调用 AI 服务")

            if bot_id:
                # Coze：直接 start_chat，传真实 agent_id
                ai_coro = svc.start_chat(
                    agent_id=bot_id,
                    message=comment_prompt,
                    session_id="moment_comment",
                    user_name=author_name,
                    cache_session=False,
                )
            elif hasattr(svc, 'generate_comment'):
                # Coze agentId 未配置时 generate_comment 会使用默认 agent_id
                # 并且会把 comment_prompt 作为 content 包在完整 prompt 里发送给 Coze
                # 注意：这里传 post_text 而非 comment_prompt，避免双重包裹
                ai_coro = svc.generate_comment(
                    content=post_text or "（纯图片/视频动态）",
                    session_id=author_name,
                    user_name=author_name,
                    account_id=account_id,
                )
            else:
                # 其他服务（OpenAI/Dify）：直接传 prompt
                ai_coro = svc.start_chat(
                    message=comment_prompt,
                    session_id="moment_comment",
                    user_name=author_name,
                    cache_session=False,
                )
            ai_result = loop.run_until_complete(
                asyncio.wait_for(ai_coro, timeout=30.0)
            )
        except asyncio.TimeoutError:
            logger.warning(f"[AI评论] AI 响应超时 (>30s)，跳过评论: {author_name}")
            loop.close()
            return False
        loop.close()

        # 兼容 Coze(content) 和 OpenAI/Dify(reply) 两种返回格式
        reply_text = ""
        moment_insight = {}  # Coze 输出的画像 JSON
        if ai_result and ai_result.get('success'):
            raw = (ai_result.get('content') or ai_result.get('reply') or "").strip()
            reply_text, moment_insight = _parse_comment_and_insight(raw)
        elif ai_result:
            logger.warning(f"[AI评论] AI 返回失败: {ai_result.get('error', '未知')}")

        if not reply_text:
            return False

        if '无需评论' in reply_text:
            logger.info(f"[智能评论] AI 判断无需评论，跳过 {author_name}")
            return False

        is_safe, reason, cleaned = validate_comment(reply_text, post_text, settings)
        if not is_safe:
            logger.warning(f"[评论安全网] 拦截: {reason}, 原文: {reply_text}")
            return False
        reply_text = cleaned
        logger.info(f"[智能评论] 安全校验通过: {reply_text}")

        try_click(comment_btn)
        time.sleep(1.0)  # 竞品 L3385

        edit_box = _find_comment_edit_box(sns_window, moment_item)

        if edit_box is not None and _se(edit_box, 1.0):
            edit_box.SetFocus()
            time.sleep(0.5)
            pyperclip.copy(reply_text)
            time.sleep(0.5)
            uia.SendKeys('{Ctrl}v')
            time.sleep(0.5)

            # 验证粘贴内容（竞品 L3408）
            try:
                val_pattern = edit_box.GetValuePattern()
                if not val_pattern or not val_pattern.Value:
                    logger.warning("[智能评论] 评论内容未成功输入")
                    return False
            except Exception:
                pass  # 某些 EditControl 不支持 ValuePattern

            uia.SendKeys('{Enter}')
            time.sleep(0.5)
            _try_fallback_send(sns_window, moment_item, edit_box)

            _daily_counter.increment("comment", account_id)
            record_interaction(author_name, post_text, "comment", fingerprint, account_id)
            manager._persist_log(
                "comment", author_name, post_text,
                reply_text=reply_text, fingerprint=fingerprint
            )
            logger.info(f"[智能评论] ✅ 已成功评论 {author_name}: {reply_text}")

            # 利用 Coze 输出的画像 JSON 更新 CRM（异步，不阻塞 UI）
            if moment_insight:
                _update_crm_from_insight(manager, author_name, account_id, moment_insight)

            time.sleep(human_delay("comment"))
            return True
        else:
            logger.warning(f"[智能评论] 未找到评论编辑框，跳过评论: {author_name}")
    except Exception as e:
        logger.warning(f"[智能评论] 提交执行失败: {e}")
    return False


def _find_comment_edit_box(sns_window, moment_item):
    """查找评论编辑框（多级快速查找，避免长时间阻塞）。"""
    # 策略1: 从 moment_item 找（最准，等 1.0s）
    if moment_item is not None:
        _eb = moment_item.EditControl(Name='评论')
        if _se(_eb, 1.0):
            return _eb

    # 策略2: 全局快速查找（不指定 depth，由库自行递归）
    candidate = sns_window.EditControl(Name='评论')
    if candidate and _se(candidate, 1.0):
        return candidate

    # 策略3: ClassName 匹配 (兼容新旧微信版本类名变化)
    eb = sns_window.EditControl(ClassName='mmui::XValidatorTextEdit')
    if _se(eb, 0.3):
        return eb
    eb = sns_window.EditControl(ClassName='mmui::CommentReplyTextEdit')
    if _se(eb, 0.3):
        return eb

    # 策略4: 焦点控件
    try:
        focused = uia.GetFocusedControl()
        if focused and getattr(focused, 'ControlTypeName', '') == 'EditControl':
            return focused
    except Exception:
        pass

    # 策略5: 终极兜底：全局安全搜索任何可见且大小合理的 EditControl
    try:
        from src.utils.safe_uia import safe_walk_control
        for ctrl, _ in safe_walk_control(sns_window, max_depth=8):
            try:
                if ctrl.ControlTypeName == 'EditControl':
                    rect = ctrl.BoundingRectangle
                    if rect.width() > 50 and rect.height() > 20:
                        logger.info(f"[智能评论] 终极兜底策略成功找到评论编辑框: Name={ctrl.Name}, ClassName={ctrl.ClassName}")
                        return ctrl
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[智能评论] 终极兜底搜索 EditControl 异常: {e}")

    return None


# _try_fallback_send / _pixel_send 已迁移到 moment_interact_utils.py（保证300行规范）
from src.monitor.moment_interact_utils import _try_fallback_send, _pixel_send  # noqa: F401


def _reopen_popup_robust(interaction_btn, sns_window, moment_item=None) -> bool:
    """点赞后重新弹出赞/评论浮层（多策略兜底）。

    策略优先级：
    1. UIA 按钮点击（interaction_btn 如果仍有效）
    2. 像素坐标点击 item 右下角两个小点（最可靠）
    3. 全局搜索评论按钮
    """
    if interaction_btn is not None:
        try:
            br = interaction_btn.BoundingRectangle
            if br.right > 0 and br.bottom > 0:
                if try_click(interaction_btn, max_retries=2, delay=0.3):
                    logger.info("[互动] 原按钮重新点击成功")
                    return True
        except Exception:
            pass

    if moment_item is not None:
        logger.info("[互动] 使用像素坐标重新点击两个小点")
        if click_interaction_area(moment_item):
            time.sleep(0.5)
            return True

    return reopen_interaction_popup(None, sns_window)
