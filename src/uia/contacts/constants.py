from typing import Any, Dict, List, Optional, Set

# ── 同步控制 ──

# 网页端 ESC 会优先被 WebView 吃掉，后端用此标志 + GetAsyncKeyState 双通道暂停
_contact_sync_pause_requested = False


def clear_contact_sync_pause() -> None:
    global _contact_sync_pause_requested
    _contact_sync_pause_requested = False


def request_contact_sync_pause() -> None:
    """由 HTTP 调用：用户在 Bot 页按 ESC 或点击暂停按钮时前端下发，UIA 线程轮询后结束通讯录相关同步任务。"""
    global _contact_sync_pause_requested
    _contact_sync_pause_requested = True


def is_contact_sync_pause_requested() -> bool:
    return _contact_sync_pause_requested


def is_synthetic_placeholder_wxid(wxid: str) -> bool:
    """本地无真实微信号时落库的 uid_昵称 占位（与 db_manager 一致），不可与右侧解析出的微信号做相等比对。"""
    return bool((wxid or "").strip().startswith("uid_"))


def is_denied_contact_row_name(raw_name: str) -> bool:
    """判定一个 UIA 节点的名称是否属于‘非联系人’噪音（索引字母或分组标题）。"""
    s = (raw_name or "").strip()
    if not s:
        return True
    
    # 1. 索引字母 (A-Z, #)
    if len(s) == 1 and (s.isupper() or s == "#"):
        return True

    # 2. 常见分组标题头（WeChat 4.x UI 特征：名称后可能带数字，如 '联系人 123'）
    # 注意：'文件传输助手' 绝不是标题头，它是真实联系人
    headers = [
        "联系人", "公众号", "服务号", "订阅号", "视频号", 
        "星标朋友", "新的朋友", "仅聊天", "群聊", "企业微信联系人",
        "我的企业", "标签"
    ]
    for h in headers:
        if s.startswith(h):
            # 进一步校验：如果是标题头，通常后面紧跟数字、带括号的数字或为空
            suffix = s[len(h):].strip()
            if not suffix:
                return True
            import re
            # 匹配 123 或 (123)
            if re.match(r"^\(?\d+\)?$", suffix):
                return True

    return False


def match_friend_for_detail_row(
    raw_name: str,
    missing_avatars: List[Dict[str, Any]],
    target_names: Set[str],
) -> Optional[Dict[str, Any]]:
    """中间列 UIA 行文案常为备注；与缓存主键 name 可能不一致，需同时按备注匹配。
    
    [同名联系人支持] 当 raw_name 直接匹配不到时，会尝试按序号后缀（②③④...）查找
    同名的第 N 个人，返回第一个还在 target_names 里（未处理）的记录。
    """
    rn = (raw_name or "").strip()
    if not rn:
        return None

    # ── 1. 精确匹配（含 display_name / remark 匹配） ──
    for friend in missing_avatars:
        canon = (friend.get("name") or "").strip()
        if not canon or canon not in target_names:
            continue
        if rn == canon:
            return friend
        # display_name 匹配（同名联系人的原始显示名）
        display = (friend.get("display_name") or "").strip()
        if display and rn == display:
            return friend
        remark = (friend.get("remark") or "").strip()
        if remark and rn == remark:
            return friend

    # ── 2. 同名联系人序号匹配 ──
    # 如果 UI 上显示的是"李雷"，但缓存里有"李雷②"、"李雷③"等带序号的记录
    # 按序返回第一个还在 target_names 里的同名人
    _DUP_SUFFIXES = ["2", "3", "4", "5", "6", "7", "8", "9", "10"]
    for suffix in _DUP_SUFFIXES:
        candidate_name = rn + suffix
        if candidate_name not in target_names:
            continue
        for friend in missing_avatars:
            canon = (friend.get("name") or "").strip()
            if canon == candidate_name:
                return friend

    return None

