"""
朋友圈互动配套工具 — 从 moment_interactor_helpers.py 拆分（保证300行规范）

包含：
- _intel_radar : 情报雷达，抓取动态信息落库 CRM
- _sync_tags   : 同步待打标签到微信通讯录
"""
import logging
import time

logger = logging.getLogger(__name__)


def _intel_radar(manager, author_name, post_text, media_hint, account_id, item_name=""):
    """情报雷达：抓取动态与评论并落库"""
    try:
        from src.crm.profile_manager import ProfileManager
        pm = ProfileManager(account_id=getattr(manager.driver, 'bot_wxid', 'main'))
        wxid = author_name
        for p in pm.get_all_profiles():
            if p.nickname == author_name:
                wxid = p.wxid
                break

        # 从微信拼接好的朋友圈条目文本中提取出其他好友的评论内容
        comments_part = ""
        if item_name and (":" in item_name or "：" in item_name):
            lines = [line.strip() for line in item_name.replace("\r", "").split("\n") if line.strip()]
            comment_lines = []
            for line in lines:
                if ":" in line or "：" in line:
                    parts = line.split(":", 1) if ":" in line else line.split("：", 1)
                    p_name = parts[0].strip()
                    p_content = parts[1].strip()
                    # 排除作者本人发的内容、系统按键如“赞/评论/详情”等
                    if p_name != author_name and p_name not in ("赞", "评论", "详情", "朋友圈", "新的消息"):
                        comment_lines.append(f"{p_name}评论: {p_content}")
            if comment_lines:
                comments_part = " | ".join(comment_lines)

        note = f"发布动态: {post_text}" + (f" [{media_hint}]" if media_hint else "")
        if comments_part:
            note += f" [评论互动: {comments_part}]"

        pm.add_note(wxid, note, nickname=author_name)

        # 拼接动态与评论，一同传给 AI 进行语义画像与打标签提炼
        ai_analysis_text = post_text
        if comments_part:
            ai_analysis_text += f"\n【互动评论】：{comments_part}"

        if manager.ai_service and manager.ai_service.is_configured() and ai_analysis_text.strip():
            manager._extract_queue.put({
                "author_name": author_name,
                "post_text": ai_analysis_text,
                "account_id": account_id,
            })
    except Exception as e:
        logger.error(f"[情报雷达] 抓取写入失败: {e}")


def _sync_tags(manager, sns_window, settings):
    """同步待打标签"""
    from src.utils.moment_config import human_delay
    if not manager._pending_tags or not settings.get("tag_enabled", True):
        return
    logger.info(f"[朋友圈标签] 开始同步 {len(manager._pending_tags)} 个待打标签")
    for tag_task in manager._pending_tags[:]:
        if not manager._running:
            break
        try:
            ok = manager._tag_sync.apply_tags_from_moment(
                moment_window=sns_window, item_ctrl=tag_task["item"],
                author_name=tag_task["author"], tags=tag_task["tags"],
                remark=tag_task.get("remark", ""),
                inside_lock=True,  # 在 moment_interact@ 持锁上下文内，跳过内部锁避免死锁
            )
            if ok:
                manager._persist_log("tag", tag_task["author"], ", ".join(tag_task["tags"]))
            time.sleep(human_delay("comment"))
        except Exception as e:
            logger.error(f"[朋友圈标签] 同步失败 {tag_task['author']}: {e}")
    manager._pending_tags.clear()


def _try_fallback_send(sns_window, moment_item, edit_box) -> bool:
    """编辑框内容仍存在时，尝试兜底发送（对齐竞品 L3418-3430）。"""
    import time
    from src.uia.retry import try_click
    if not edit_box.Exists(0.5):
        return False
    try:
        val = edit_box.GetValuePattern()
        if not (val and val.Value):
            return False
        send_btn = sns_window.ButtonControl(Name='发送')
        if not send_btn.Exists(0.5) and moment_item is not None:
            send_btn = moment_item.ButtonControl(Name='发送')
        if send_btn.Exists(0.6):
            if try_click(send_btn):
                time.sleep(0.5)
                return True
        return _pixel_send(edit_box)
    except Exception:
        return False


def _pixel_send(edit_box) -> bool:
    """像素坐标点击发送区域（编辑框右下方）。分辨率自适应。"""
    import time
    try:
        import win32api
        from src.uia.retry import physical_click
        er = edit_box.BoundingRectangle
        sh = win32api.GetSystemMetrics(1)
        dy_off = 22 if sh < 2160 else 28   # 4K屏偏移更大
        for dx in (35, 32, 28):
            sx = er.right - dx
            sy = er.bottom + dy_off
            physical_click(sx, sy, settle=0.1)
            time.sleep(0.3)
            if not edit_box.Exists(0.3):
                return True
            try:
                if not edit_box.GetValuePattern().Value:
                    return True
            except Exception:
                return True
    except Exception as px_e:
        logger.debug(f"[像素发送] 像素坐标兜底发送失败: {px_e}")
    return False


def _execute_moment_interaction(
    item, manager, sns_window, list_ctrl, author_name, post_text,
    media_hint, item_name, fingerprint, account_id, settings, screen_idx,
    this_like, this_comment, content_too_short, precheck_comment,
    user_comment_counts,
):
    """互动执行主体：情报雷达 → 可见性滚动 → 触发浮层 → 点赞/评论 → 打标签 → 等待。

    从 _process_single_moment_item 拆分，避免 helpers 超 300 行规范。
    返回: (stop_this_round, interacted_delta, liked_delta, commented_delta)
    """
    import random
    import time
    from src.uia.retry import random_delay, try_click
    from src.uia.input_guard import UIAInterruptError
    from src.utils.stop_signal import stop_signal
    from src.monitor.moment_utils import click_interaction_area, dismiss_popup
    from src.monitor.moment_interactor_helpers import (
        _is_btn_visible, _click_interaction_area_adaptive, _find_toast,
    )
    from src.utils.safe_uia import safe_exists

    _intel_radar(manager, author_name, post_text, media_hint, account_id, item_name)

    # 可见性检测 & 滚动到可见位置（对齐竞品 L1477-1488）
    try:
        list_rect = list_ctrl.BoundingRectangle
        if not _is_btn_visible(item, list_rect):
            logger.info("[互动] 条目不在底部可见区域，滚动2格后重试...")
            list_ctrl.WheelDown(wheelTimes=2)
            time.sleep(random.uniform(0.5, 1.0))
            for _ in range(3):
                if _is_btn_visible(item, list_rect):
                    break
                list_ctrl.WheelDown(wheelTimes=3)
                time.sleep(0.3)
    except Exception as _ve:
        logger.debug(f"[互动] 可见性检测异常: {_ve}")

    # 分辨率自适应像素点击（优先），UIA 按钮为备选
    comment_btn = item.ButtonControl(Name='评论')
    btn_found = safe_exists(comment_btn, 0.5)
    if btn_found:
        try:
            list_rect = list_ctrl.BoundingRectangle
            if not _is_btn_visible(comment_btn, list_rect):
                for _ in range(3):
                    list_ctrl.WheelDown(3)
                    time.sleep(0.3)
                    if _is_btn_visible(comment_btn, list_rect):
                        break
        except Exception as _ce:
            logger.debug(f"[互动] list_ctrl 引用已过期: {_ce}")
        if try_click(comment_btn, max_retries=3, delay=0.3):
            logger.info(f"[互动] 已点击 {author_name} 的评论按钮")
        else:
            logger.warning(f"[互动] 点击评论按钮失败，转像素坐标: {author_name}")
            _click_interaction_area_adaptive(item)
    else:
        logger.info(f"[互动] 未找到 UIA 评论按钮，使用分辨率自适应像素坐标: {author_name}")
        if not _click_interaction_area_adaptive(item):
            if not click_interaction_area(item):
                logger.warning(f"[互动] 像素点击也失败: {author_name}")
                return False, 0, 0, 0

    random_delay(0.5, 1.0)
    dismiss_popup()
    toast_window = _find_toast(sns_window)

    from src.monitor.moment_interact_action import do_like_and_comment
    result = do_like_and_comment(
        manager=manager, sns_window=sns_window,
        interaction_btn=comment_btn if btn_found else None,
        toast_window=toast_window, author_name=author_name,
        post_text=post_text, fingerprint=fingerprint,
        account_id=account_id, settings=settings,
        screen_idx=screen_idx, item_name_raw=item_name,
        media_hint=media_hint, should_like=this_like,
        should_comment=this_comment and not content_too_short,
        moment_item=item, precheck_comment=precheck_comment,
    )
    liked_delta     = 1 if result.get("liked") else 0
    commented_delta = 1 if (result and result.get("commented")) else 0
    dismiss_popup()

    if commented_delta:
        user_comment_counts[author_name] = user_comment_counts.get(author_name, 0) + 1

    if settings.get("tag_enabled", True):
        try:
            from datetime import datetime
            custom_tag = settings.get("moment_tag_name", "").strip()
            tags_to_apply = [custom_tag] if custom_tag else [f"互动-{datetime.now().strftime('%y年%m月')}"]

            logger.info(f"[朋友圈标签] 实时为好友 {author_name} 贴标签 {tags_to_apply}...")
            ok = manager._tag_sync.apply_tags_from_moment(
                moment_window=sns_window,
                item_ctrl=item,
                author_name=author_name,
                tags=tags_to_apply,
                remark=f"朋友圈自动互动: {post_text[:30]}",
                inside_lock=True,
            )
            if ok:
                manager._persist_log("tag", author_name, ", ".join(tags_to_apply))
        except Exception as e:
            logger.error(f"[朋友圈标签] 实时同步标签失败 {author_name}: {e}")

    _wait = random.uniform(3.0, 8.0)
    _start = time.time()
    while time.time() - _start < _wait:
        if stop_signal.is_stopped:
            logger.info("[互动间隔] 检测到 ESC 信号，立即中断等待")
            raise UIAInterruptError("用户按下 ESC 中断")
        if not manager._running:
            break
        time.sleep(0.2)

    return False, 1, liked_delta, commented_delta

