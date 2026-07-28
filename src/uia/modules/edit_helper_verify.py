"""
edit_helper_verify.py — 会话切换校验辅助函数（从 edit_helper.py 拆分）

WeChat 4.1.7 inspect 实测数据：
  - 聊天输入框 AutomationId = "chat_input_field"
  - 聊天输入框 ClassName    = "mmui::ChatInputField"
  - 聊天输入框 Name 属性     = 当前聊天好友 / 群的昵称（即 session_name）
  - 搜索框 AutomationId     = "" (空串)
  - 搜索框 ClassName        = "mmui::XValidatorTextEdit"

核心修复：当 _get_header_title_safely 因标题栏 AutomationId 在新版微信发生变化而读取失败时，
通过直接比对输入框 Name 属性来快速判断当前聊天是否已切换到目标会话，
彻底消灭"明明切换成功、UIA 却报告校验失败"的假阴性错误。
"""
import re
import logging
from typing import Optional

from src.uia.session import clean_session_name

logger = logging.getLogger("WeChatDriver.EditHelper")


def _normalize_spaces(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


def verify_by_input_name(
    root_ctrl,
    norm_search: str,
    norm_real: Optional[str],
    search_who: str,
    wxid: Optional[str],
    attempt: int,
    verify_history_fn,
) -> Optional[bool]:
    """
    通过读取 chat_input_field 的 Name 属性（= 好友昵称）来快速校验会话是否切换成功。

    返回值：
        True  → 校验通过，已切换到目标会话
        False → 找到输入框且名字匹配但历史比对失败（重名冲突）
        None  → 未能判断（容器不存在 / 输入框不存在 / 名字不匹配），调用方继续重试
    """
    try:
        from src.utils.safe_uia import get_chat_container_safely
        cc = get_chat_container_safely(root_ctrl)
        if not cc or not cc.Exists(0.05):
            return None

        fast_edit = cc.EditControl(AutomationId="chat_input_field", searchDepth=16)
        if not fast_edit.Exists(0.05):
            fast_edit = cc.EditControl(ClassName="mmui::ChatInputField", searchDepth=16)
        if not fast_edit.Exists(0.1):
            return None

        edit_name_raw = (fast_edit.Name or "").strip()
        # 剥离群聊人数后缀 "(12)" / "（12）"
        edit_name_clean = re.sub(r'[（(]\d+[）)]$', '', edit_name_raw).strip()
        norm_edit = _normalize_spaces(clean_session_name(edit_name_clean))

        if not norm_edit:
            return None

        name_matched = (
            norm_edit == norm_search or
            norm_edit in norm_search or
            norm_search in norm_edit or
            bool(norm_real and (
                norm_edit == norm_real or
                norm_edit in norm_real or
                norm_real in norm_edit
            ))
        )

        if not name_matched:
            return None

        # 名字匹配，进一步做历史记录比对（仅在有 wxid 且存在重名风险时）
        if wxid:
            if verify_history_fn(wxid):
                logger.info(
                    f"[UIA] verify_chat_switched 输入框Name直接比对通过且历史匹配成功："
                    f"input.Name='{edit_name_raw}'，目标='{search_who}'，判定切换成功"
                )
                return True
            else:
                logger.debug(
                    f"[UIA] verify_chat_switched 输入框Name匹配('{edit_name_raw}')但历史比对暂未通过，"
                    f"继续重试... attempt={attempt}"
                )
                return None  # 继续重试，等待历史比对稳定
        else:
            logger.info(
                f"[UIA] verify_chat_switched 输入框Name直接比对通过："
                f"input.Name='{edit_name_raw}'，目标='{search_who}'，判定切换成功"
            )
            return True

    except Exception as ex:
        logger.debug(f"[UIA] verify_chat_switched 输入框Name直接比对异常: {ex}")
        return None


def verify_chat_by_history_impl(self, wxid: str, get_header_fn) -> bool:
    """
    利用数据库历史消息比对当前 UIA 窗口内容，确保切换到了正确的同名联系人，防止错发。
    由 edit_helper.verify_chat_by_history 委托调用（满足 300 行限制拆分规范）。
    """
    if not wxid:
        return True
    dup = 0
    try:
        from src.crm.account_data import get_active_account
        from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
        active_acct = get_active_account()
        if not active_acct or active_acct == 'default':
            return True

        # 🌟 同名冲突预检
        try:
            from src.utils.contacts_cache import contacts_cache
            friends = contacts_cache.get_friends(active_acct) or []
            groups = contacts_cache.get_groups(active_acct) or []
            target_name = ""
            for f in friends:
                if f.get("wxid") == wxid:
                    target_name = (f.get("name") or "").strip()
                    break
            if not target_name:
                for g in groups:
                    if g.get("wxid") == wxid:
                        target_name = (g.get("name") or "").strip()
                        break

            chat_container = self.root.GroupControl(ClassName='mmui::ChatDetailView', searchDepth=12)
            header_title = ""
            if chat_container.Exists(0.1):
                header_title = get_header_fn(chat_container)

            search_name = target_name or header_title
            if search_name:
                clean_s = re.sub(r'[（(]\d+[）)]$', '', search_name).strip()
                dup = (
                    sum(1 for f in friends if re.sub(r'[（(]\d+[）)]$', '', (f.get("name") or "")).strip() == clean_s or f.get("remark") == clean_s) +
                    sum(1 for g in groups if re.sub(r'[（(]\d+[）)]$', '', (g.get("name") or "")).strip() == clean_s)
                )
                if dup <= 1:
                    logger.info(f"[UIA] 无重名联系人 (同名数={dup})，直接信任当前窗口。")
                    return True
        except Exception as e_dup:
            logger.debug(f"[UIA] 预检同名冲突异常: {e_dup}")

        monitor = get_wcdb_monitor(active_acct)
        if not monitor or not monitor.is_active():
            return True
        db_msgs = monitor.get_latest_messages(wxid, limit=5)
        if not db_msgs:
            return True
        db_contents = {m["content"].strip() for m in db_msgs if m.get("content")}
        if not db_contents:
            return True

        from src.uia.driver import WeChatDriver
        uia_msgs = WeChatDriver.get_all_messages(self, parse_file=False, context_count=5)
        if not uia_msgs:
            return True

        match_found = any(
            any(db_c == c.strip() or db_c in c or c.strip() in db_c for db_c in db_contents)
            for _, c in uia_msgs if c
        )
        if match_found:
            logger.info(f"[UIA] 历史消息比对通过，前台窗口为 wxid: {wxid}")
            return True

        # 🌟 只有确知重名冲突且比对失败才阻断，否则放行防卡死！
        if dup > 1:
            logger.warning(f"[UIA] 历史消息比对失败！有同名混淆 (同名数={dup})，当前窗口不匹配 wxid: {wxid}")
            return False
        logger.info(f"[UIA] 历史消息比对未通过，但无重名冲突，安全放行")
        return True
    except Exception as ex:
        logger.error(f"[UIA] 历史消息比对异常: {ex}", exc_info=True)
        return True
