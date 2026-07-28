"""
朋友圈互动消息回复监听器（竞品未实现的独有功能）

每轮巡游结束后检测铃铛图标，有未读互动消息则用 AI 回复。
UI 路径：朋友圈顶栏 → 铃铛 → 「全部互动消息」→ 解析回复 → AI 回复
"""
import logging
import random
import re
import time
import pyperclip
import uiautomation as uia

from src.uia.retry import try_click
from src.utils.moment_config import human_delay, validate_comment
from src.utils.moment_dedup import (
    generate_moment_fingerprint, has_interacted, record_interaction,
)

from src.utils.safe_uia import safe_exists as _se, safe_walk_control as _sw

logger = logging.getLogger(__name__)
_MAX_REPLY = 5

_REPLY_RE = re.compile(r'^(.+?)\s*回复了你(?:的评论)?(?:[：:]\s*(.+))?$')
_LIKE_RE = re.compile(r'^(.+?)\s*赞了你的朋友圈$')


def check_and_reply_interactions(manager, sns_window, settings, account_id):
    """巡游结束后检查并回复互动消息。返回成功回复数。"""
    if not settings.get("comment_enabled", True):
        return 0
    ai = getattr(manager, 'ai_service', None)
    if not ai or not ai.is_configured():
        return 0

    notify_btn = _find_notification_button(sns_window)
    if not notify_btn:
        logger.debug("[互动回复] 未找到消息通知图标，跳过")
        return 0
    if not try_click(notify_btn, max_retries=3, delay=0.3):
        logger.warning("[互动回复] 点击消息通知图标失败")
        return 0
    time.sleep(random.uniform(1.0, 1.5))

    panel = _find_interaction_panel(sns_window)
    if not panel:
        logger.info("[互动回复] 无互动消息面板或暂无消息，点击空白区域收起")
        _dismiss_popup(sns_window)
        return 0

    replied = 0
    try:
        items = panel.GetChildren()
        if not items:
            _dismiss_popup(sns_window)
            return 0
        logger.info(f"[互动回复] 发现 {len(items)} 条互动消息")
        for item in items:
            if replied >= _MAX_REPLY or not manager._running:
                break
            name = ""
            try:
                name = item.Name or ""
            except Exception:
                continue
            if not name.strip():
                continue
            parsed = _parse_msg(name.strip())
            if not parsed or parsed["type"] != "reply":
                continue
            replier, content = parsed["author"], parsed["content"]
            logger.info(f"[互动回复] 检测到: {replier} 说: {content}")
            fp = generate_moment_fingerprint(replier, f"reply:{content}")
            if has_interacted(fp, account_id):
                continue
            ai_reply = _gen_reply(manager, replier, content, settings)
            if not ai_reply:
                continue
            if _do_reply(item, sns_window, ai_reply):
                replied += 1
                record_interaction(replier, f"reply:{content}",
                                   "reply_comment", fp, account_id)
                manager._persist_log("comment", replier, content,
                                     reply_text=f"[回复评论] {ai_reply}",
                                     fingerprint=fp)
                logger.info(f"[互动回复] ✅ 已回复 {replier}: {ai_reply}")
                time.sleep(human_delay("comment"))
            else:
                logger.warning(f"[互动回复] 回复 {replier} 失败")
    except Exception as e:
        logger.error(f"[互动回复] 遍历互动消息异常: {e}")
    _dismiss_popup(sns_window)
    return replied


def _find_notification_button(sns_window):
    """查找朋友圈顶栏铃铛/消息通知按鈕。

    安全边界：候选按鈕必须同时满足：
    1. 在 sns_window 物理矩形范围内（绝对屏幕坐标）
    2. 距 sns_window 顶部 ≤ 80px（顶栏区域）
    """
    try:
        wr = sns_window.BoundingRectangle
    except Exception:
        return None

    def _in_topbar(br) -> bool:
        """button 必须在窗口物理范围内且在顶栏 80px内"""
        return (br.left >= wr.left and br.right <= wr.right
                and br.top >= wr.top and br.bottom <= wr.bottom
                and br.top - wr.top <= 80)

    # 策略1: 按 Name 匹配 + 坐标双重验证
    for hint in ['朋友圈消息', '互动消息', '铃铛', '通知']:
        btn = sns_window.ButtonControl(Name=hint)
        if _se(btn, 0.3):
            try:
                if _in_topbar(btn.BoundingRectangle):
                    return btn
                else:
                    logger.debug(f"[互动回复] Name='{hint}' 但坐标不在顶栏，跳过")
            except Exception:
                pass

    # 策略2: 定位相机按鈕，铃铛在相机左侧同行
    try:
        cam = sns_window.ButtonControl(Name='拍照')
        if not _se(cam, 0.3):
            cam = sns_window.ButtonControl(Name='相机')
        if _se(cam, 0.3):
            cr = cam.BoundingRectangle
            # 相机按鈕必须在顶栏才可信
            if not _in_topbar(cr):
                logger.debug("[互动回复] 相机按鈕不在顶栏，策略2放弃")
                return None

            _skip = {'关闭', '返回', '朋友圈', '拍照', '相机', '刷新',
                     '消息', '通讯录', '发现', '我', ''}
            for ctrl, _ in _sw(sns_window, max_depth=5):
                try:
                    if ctrl.ControlTypeName != 'ButtonControl':
                        continue
                    br = ctrl.BoundingRectangle
                    # 必须在窗口范围内 + 顶栏 + 在相机左侧 20~150px 内同行
                    if not _in_topbar(br):
                        continue
                    if not (abs(br.top - cr.top) < 25
                            and 20 < cr.left - br.right < 150):
                        continue
                    n = (ctrl.Name or '').strip()
                    if n not in _skip:
                        logger.info(f"[互动回复] 定位铃铛按鈕: '{n}' ({br.left},{br.top})")
                        return ctrl
                except Exception:
                    continue
    except Exception:
        pass

    logger.debug("[互动回复] 未能定位铃铛按鈕，跳过互动消息检查")
    return None


def _find_interaction_panel(sns_window):
    """查找「全部互动消息」悬浮面板。"""
    time.sleep(0.5)
    try:
        for ctrl, _ in _sw(sns_window, max_depth=8):
            try:
                n = ctrl.Name or ""
                if '互动消息' in n or '全部互动' in n:
                    p = ctrl.GetParentControl()
                    return p if p else ctrl
            except Exception:
                continue
    except Exception:
        pass
    # 兜底：查找弹出的 ListControl
    try:
        lst = sns_window.ListControl()
        if _se(lst, 1.0):
            children = lst.GetChildren()
            if children:
                fn = getattr(children[0], 'Name', '') or ""
                if '回复' in fn or '赞了' in fn:
                    return lst
    except Exception:
        pass
    return None


def _dismiss_popup(sns_window):
    """安全关闭互动消息浮层面板。

    严禁使用 uia.SendKeys('{ESC}')：ESC 是全局热键，会直接关闭整个朋友圈窗口！
    正确做法：点击朋友圈顶栏空白区域使面板失焦自动收起。
    """
    try:
        wr = sns_window.BoundingRectangle
        # 点击顶栏左侧空白区域（距左边缘 20px，距顶部 20px），安全不触碰任何按钮
        cx = wr.left + 20
        cy = wr.top + 20
        import ctypes
        ctypes.windll.user32.SetCursorPos(cx, cy)
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        time.sleep(0.3)
    except Exception:
        pass


def _parse_msg(text):
    """解析互动消息 → {"type","author","content"} 或 None。"""
    m = _REPLY_RE.match(text)
    if m:
        return {"type": "reply", "author": m.group(1).strip(),
                "content": (m.group(2) or "").strip()}
    m = _LIKE_RE.match(text)
    if m:
        return {"type": "like", "author": m.group(1).strip(), "content": ""}
    return None


def _gen_reply(manager, replier, their_reply, settings):
    """AI 生成回复。"""
    prompt = (
        f"你在微信朋友圈收到好友 {replier} 回复了你的评论，"
        f"对方说：「{their_reply}」\n"
        f"请写一句简短自然的回复（10字以内，熟人语气）。\n"
        f"只输出那句话，不加引号或解释。"
    )
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        r = loop.run_until_complete(
            manager.ai_service.start_chat("moment_reply", prompt,
                                          "system", replier)
        )
        loop.close()
        if r and r.get('reply'):
            txt = r['reply'].strip()
            if '无需' in txt:
                return ""
            ok, reason, cleaned = validate_comment(txt, their_reply, settings)
            if not ok:
                logger.warning(f"[互动回复] 安全网: {reason}")
                return ""
            return cleaned
    except Exception as e:
        logger.error(f"[互动回复] AI 失败: {e}")
    return ""


def _do_reply(item, sns_window, reply_text):
    """点击互动消息条目，输入并发送回复。"""
    try:
        if not try_click(item, max_retries=2, delay=0.3):
            return False
        time.sleep(random.uniform(1.5, 2.5))

        edit = _find_edit_box(sns_window)
        if not edit:
            logger.warning("[互动回复] 未找到评论编辑框")
            _dismiss_popup(sns_window)
            return False

        edit.SetFocus()
        time.sleep(0.2)
        pyperclip.copy(reply_text)
        uia.SendKeys('{Ctrl}v')
        time.sleep(human_delay("typing"))
        uia.SendKeys('{Enter}')
        time.sleep(0.5)

        send = sns_window.ButtonControl(Name='发送')
        if _se(send, 0.6):
            try_click(send)
            time.sleep(0.3)
        return True
    except Exception as e:
        logger.error(f"[互动回复] 执行失败: {e}")
        return False


def _find_edit_box(sns_window):
    """查找评论编辑框（多策略兜底）。"""
    edit = sns_window.EditControl(Name='评论')
    if _se(edit, 1.0):
        return edit
    edit = sns_window.EditControl(ClassName='mmui::XValidatorTextEdit')
    if _se(edit, 1.0):
        return edit
    try:
        f = uia.GetFocusedControl()
        if f and getattr(f, 'ControlTypeName', '') == 'EditControl':
            return f
    except Exception:
        pass
    return None
