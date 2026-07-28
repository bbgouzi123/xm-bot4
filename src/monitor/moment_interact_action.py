"""
朋友圈互动执行动作 — 从 moment_interactor.py 拆分

包含：
- do_like_and_comment: 执行点赞和/或 AI 评论的核心函数

升级要点（对齐竞品 YokoAIBot_v2）：
- AI 评论增加图片/视频/链接上下文感知
- 评论发送增加兜底确认（Send 按钮点击 + 像素坐标兜底）
- 点赞后重新点击互动区域呼出评论浮层（不再依赖 UIA interaction_btn）
- GAP 1: AI "无需评论" 识别跳过
- GAP 6: 支持 should_like / should_comment 参数控制交互模式
- 返回结果字典 {"liked": bool, "commented": bool} 供上层计数
"""
import logging
import random
import time

from src.uia.retry import try_click
from src.utils.daily_counter import DailyCounter
from src.utils.moment_config import (
    human_delay, record_interaction,
)
from src.monitor.moment_utils import (
    find_toast_window,
)
from src.monitor.moment_comment_sender import (
    _do_ai_comment,
    _reopen_popup_robust,
)

logger = logging.getLogger(__name__)
_daily_counter = DailyCounter()


def do_like_and_comment(
    manager,
    sns_window,
    interaction_btn,
    toast_window,
    author_name: str,
    post_text: str,
    fingerprint: str,
    account_id: str,
    settings: dict,
    screen_idx: int,
    item_name_raw: str = "",
    media_hint: str = "",
    should_like: bool = True,
    should_comment: bool = True,
    moment_item=None,
    precheck_comment: str = "",
) -> dict:
    """执行点赞和/或 AI 评论。

    Args:
        manager: MomentInteractionManager 实例
        sns_window: 朋友圈主窗口控件
        interaction_btn: 互动触发按钮（'...' 或 '评论'），可为 None（像素点击模式）
        toast_window: 已定位的浮层控件（可能未显示）
        author_name: 动态发布者昵称
        post_text: 动态文字
        fingerprint: 动态指纹（防重互动标识）
        account_id: 当前账号 wxid
        settings: 互动配置字典
        screen_idx: 当前滚动屏次（用于衰减概率）
        item_name_raw: 朋友圈条目原始 Name 文本（含图片/视频标记）
        media_hint: 媒体类型提示（如"包含3张图片"、"[视频]"等）
        should_like: 是否执行点赞（GAP 6: 交互模式）
        should_comment: 是否执行评论（GAP 6: 交互模式）
        moment_item: 朋友圈列表条目控件（用于编辑框查找和像素坐标点击）
        precheck_comment: _ai_precheck 已生成的评论文本（非空时跳过二次 AI 调用）

    Returns:
        {"liked": bool, "commented": bool}
    """
    result = {"liked": False, "commented": False}

    from src.utils.safe_uia import safe_exists

    if safe_exists(toast_window, 0.5):
        like_btn = toast_window.ButtonControl(Name='赞')
        cancel_like = toast_window.ButtonControl(Name='取消')
        comment_btn = toast_window.ButtonControl(Name='评论')
    else:
        like_btn = sns_window.ButtonControl(Name='赞')
        cancel_like = sns_window.ButtonControl(Name='取消')
        comment_btn = sns_window.ButtonControl(Name='评论')

    # 竞品做法：不用概率门控，用 commentLimit 控制总量
    do_like = (
        should_like
        and settings.get("like_enabled", True)
        and _daily_counter.can_do("like", account_id)
    )

    # ── 朋友圈关键词匹配规则检查 ──
    matched_reply = ""
    try:
        from src.utils.db_manager import WeChatDBManager
        db = WeChatDBManager()
        rules = db.get_all_keyword_replies()
        
        for rule in rules:
            if not rule.get("is_active", True):
                continue
            if rule.get("scope") != "moment":
                continue
                
            keywords = rule.get("keywords", [])
            match_type = rule.get("match_type", "fuzzy")
            
            triggered = False
            for kw in keywords:
                if not kw:
                    continue
                if match_type == "exact":
                    if post_text.strip() == kw.strip():
                        triggered = True
                        break
                else:  # fuzzy
                    if kw.strip() in post_text:
                        triggered = True
                        break
            
            if triggered:
                matched_reply = rule["reply_content"]
                logger.info(f"[朋友圈关键词评论] 动态 \"{post_text}\" 命中关键词规则: {rule['keywords']}")
                if rule.get("delete_on_reply"):
                    try:
                        logger.info(f"[朋友圈关键词评论] 自动删除一次性规则: {rule.get('id')}")
                        db.delete_keyword_reply(rule["id"])
                    except Exception as del_err:
                        logger.error(f"[朋友圈关键词评论] 自动删除异常: {del_err}")
                break
    except Exception as ex:
        logger.error(f"[朋友圈关键词评论] 匹配异常: {ex}")

    if matched_reply:
        precheck_comment = matched_reply

    # ── 评论条件逐项诊断（方便排查不评论的根因）──
    _cond_should   = should_comment
    _cond_enabled  = settings.get("comment_enabled", True)
    _cond_quota    = _daily_counter.can_do("comment", account_id)
    _cond_ai_obj   = bool(getattr(manager, 'ai_service', None))
    _cond_ai_cfg   = _cond_ai_obj and manager.ai_service.is_configured()
    
    # 若有关键词预判回复，则无须强求 AI 配置
    do_comment     = _cond_should and _cond_enabled and _cond_quota and (bool(precheck_comment) or _cond_ai_cfg)

    if not do_comment:
        _reasons = []
        if not _cond_should:   _reasons.append("should_comment=False(概率未命中或内容过短)")
        if not _cond_enabled:  _reasons.append("comment_enabled=False(已在设置关闭)")
        if not _cond_quota:    _reasons.append("今日评论已达上限(daily_comment_limit)")
        if not precheck_comment:
            if not _cond_ai_obj:   _reasons.append("未绑定AI服务(ai_service=None)")
            elif not _cond_ai_cfg: _reasons.append("AI服务未配置(未填写API Key)")
        logger.info(f"[互动·跳过评论] {author_name} | 原因: {'; '.join(_reasons) or '未知'}")

    # 标记浮层是否因点赞而关闭（需要重新弹出）
    popup_closed_by_like = False

    # ========== 点赞流程（对齐竞品 L3288-3323）==========
    if do_like:
        if safe_exists(like_btn, 0.5):
            try_click(like_btn)
            _daily_counter.increment("like", account_id)
            record_interaction(author_name, post_text, "like", fingerprint, account_id)
            manager._persist_log("like", author_name, post_text, fingerprint=fingerprint)
            logger.info(f"已赞 {author_name}")
            result["liked"] = True
            time.sleep(human_delay("like"))
            popup_closed_by_like = True  # 点赞后浮层会自动关闭
        elif safe_exists(cancel_like, 0.1):
            logger.info(f"已点过赞，跳过: {author_name}")
            # 浮层仍然打开，可以直接评论

    # ========== 评论流程（对齐竞品 L3351-3435）==========
    if do_comment and (post_text.strip() or media_hint):
        comment_btn = _prepare_comment_btn(
            popup_closed_by_like, interaction_btn, sns_window,
            moment_item, comment_btn
        )

        # 竞品 L3372: 验证 toast 中的评论按钮存在（2 秒超时）
        if safe_exists(comment_btn, 2.0):
            logger.info(f"准备为 {author_name} 的朋友圈生成走心评论...")
            commented = _do_ai_comment(
                manager, sns_window, comment_btn,
                author_name, post_text, fingerprint, account_id, settings,
                item_name_raw=item_name_raw,
                media_hint=media_hint,
                moment_item=moment_item,
                precheck_comment=precheck_comment,   # 传递预判评论，避免二次 AI 调用
            )
            if commented:
                result["commented"] = True
        else:
            logger.warning(f"[互动] 未找到评论按钮，跳过评论: {author_name}")
    elif do_comment:
        logger.info(f"[互动·跳过评论] {author_name} | 原因: 动态无文字且无媒体标记")

    return result



def _prepare_comment_btn(popup_closed_by_like, interaction_btn, sns_window,
                         moment_item, comment_btn):
    """点赞后重新弹出浮层并获取评论按钮（适配新版微信 mmui::TimelineFloatMenu）。

    新版微信 item 内部没有 UIA ButtonControl，必须通过像素坐标点击 item
    右下角互动区域来触发 mmui::TimelineFloatMenu 浮层，再从浮层里取评论按钮。

    使用 safe_exists 防止 COM 断连导致进程崩溃。
    """
    from src.monitor.moment_utils import click_interaction_area, find_toast_window
    from src.utils.safe_uia import safe_exists

    if popup_closed_by_like:
        time.sleep(random.uniform(0.5, 1.0))

        reopened = False

        # 策略1：像素坐标点击 item 右下角（最可靠，新版微信无 UIA 按钮）
        if moment_item is not None:
            try:
                if click_interaction_area(moment_item):
                    time.sleep(1.5)
                    toast = find_toast_window(sns_window)
                    if toast and safe_exists(toast, 2.0):
                        logger.info("[互动] 像素点击成功重新弹出浮层")
                        return toast.ButtonControl(Name='评论')
                    logger.warning("[互动] 像素点击后浮层未出现，尝试 UIA 按钮")
            except Exception as e:
                logger.debug(f"[互动] 像素点击异常: {e}")

        # 策略2：从 item 找 UIA 评论按钮（旧版微信兼容）
        if moment_item is not None:
            try:
                comment_area = moment_item.ButtonControl(Name='评论')
                if safe_exists(comment_area, 0.5):
                    try_click(comment_area, max_retries=2, delay=0.3)
                    time.sleep(2.0)
                    toast = find_toast_window(sns_window)
                    if toast and safe_exists(toast, 2.0):
                        logger.info("[互动] UIA 按钮点击成功重开浮层")
                        return toast.ButtonControl(Name='评论')
            except Exception:
                pass

        # 策略3：全局搜索评论按钮兜底
        logger.warning("[互动] 所有浮层重开策略失败，兜底全局搜索")
        return sns_window.ButtonControl(Name='评论')

    # 浮层未被关闭：二次兜底（comment_btn 可能已无效）
    if not safe_exists(comment_btn, 0.5):
        logger.info("[互动] 评论按钮未就绪，尝试像素重开浮层")
        if moment_item is not None:
            try:
                if click_interaction_area(moment_item):
                    time.sleep(1.5)
                    toast = find_toast_window(sns_window)
                    if toast and safe_exists(toast, 2.0):
                        return toast.ButtonControl(Name='评论')
            except Exception:
                pass

    return comment_btn



