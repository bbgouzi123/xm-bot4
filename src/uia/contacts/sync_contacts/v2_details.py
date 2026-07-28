import time
import os
import uiautomation as uia
from typing import Dict, Any, Tuple, Optional, List
import re

from ...retry import (
    exists_with_timeout,
    random_delay,
    smooth_click_at,
    capture_avatar_via_clipboard,
)
from ...elements import WxClass
from ...modules.core.preview_helpers import (
    download_avatar_from_head_view,
)

class ContactV2DetailExtractorMixin:
    """从通讯录管理页面的资料卡弹窗（ProfileUniquePop）提取详情的逻辑"""

    def _extract_details_from_profile_pop(self, pop_win, contact_name: str) -> Dict[str, Any]:
        """从资料卡弹窗提取信息"""
        details = {
            "nickname": "",
            "wxid": "",
            "region": "",
            "signature": "",
            "source": "",
            "remark": "",
            "tags": [],
            "avatar_url": None
        }

        # 1. 提取文本信息 (使用 BFS 遍历，避免 Qt 控件树循环)
        main_pid = -1
        try:
            main_pid = getattr(pop_win, "ProcessId", -1)
        except Exception:
            pass

        queue = [(pop_win, 0)]
        max_depth = 12
        visited_count = 0
        
        # 寻找锚点
        head_view = None
        
        while queue:
            ctrl, depth = queue.pop(0)
            visited_count += 1
            if depth > max_depth or visited_count > 400:
                continue

            try:
                # 校验进程 ID，防范跨进程控件污染
                pid = getattr(ctrl, "ProcessId", -1)
                if main_pid != -1 and pid != -1 and pid != main_pid:
                    continue

                name = (ctrl.Name or "").strip()
                cls = ctrl.ClassName or ""
                ctype = ctrl.ControlTypeName or ""

                # 提取微信号
                if ctype == "TextControl" and (name in ("微信号：", "微信号:", "微信号") or name.startswith("微信号")):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        details["wxid"] = (sibling.Name or "").strip()
                elif "微信号" in name:
                    details["wxid"] = name.replace("微信号：", "").replace("微信号:", "").replace("微信号", "").strip()

                # 提取地区
                if ctype == "TextControl" and (name in ("地区：", "地区:", "地区") or name.startswith("地区")):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        details["region"] = (sibling.Name or "").strip()

                # 提取来源
                if ctype == "TextControl" and (name in ("来源：", "来源:", "来源") or name.startswith("来源")):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        details["source"] = (sibling.Name or "").strip()

                # 提取个性签名
                if ctype == "TextControl" and (name in ("个性签名：", "个性签名:", "个性签名") or name.startswith("个性签名")):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        details["signature"] = (sibling.Name or "").strip()

                # 提取备注
                if ctype == "TextControl" and (name in ("备注名：", "备注名:", "备注：", "备注:", "备注名", "备注") or name.startswith("备注名") or name == "备注"):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        details["remark"] = (sibling.Name or "").strip()

                # 提取标签
                if ctype == "TextControl" and (name in ("标签：", "标签:", "标签") or name.startswith("标签")):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        tags_str = (sibling.Name or "").strip()
                        if tags_str:
                            details["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]

                # 提取昵称 (通常是第一个大号的 TextControl，排除了上面的标签)
                if not details["nickname"] and ctype == "TextControl" and name and len(name) < 40:
                    exclude_names = (
                        "微信号：", "微信号:", "微信号",
                        "地区：", "地区:", "地区",
                        "来源：", "来源:", "来源",
                        "个性签名：", "个性签名:", "个性签名",
                        "备注名：", "备注名:", "备注：", "备注:", "备注名", "备注",
                        "标签：", "标签:", "标签",
                        "发消息", "视频通话", "语音通话"
                    )
                    if name not in exclude_names:
                        # 简单的启发式：第一个遇到的非系统标签的文本可能是昵称
                        details["nickname"] = name

                # 头像控件
                if "ContactHeadView" in cls:
                    head_view = ctrl

                # 继续入队子节点
                if depth < max_depth:
                    for child in ctrl.GetChildren():
                        queue.append((child, depth + 1))
            except Exception:
                continue

        # 兜底：如果 BFS 未找到头像控件，通过 WalkControl 搜索以对齐个人资料卡查找逻辑
        if not head_view:
            count = 0
            try:
                for ctrl, _ in uia.WalkControl(pop_win, maxDepth=6):
                    count += 1
                    if count > 200:
                        break
                    try:
                        if "ContactHeadView" in (getattr(ctrl, 'ClassName', '') or ''):
                            head_view = ctrl
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        # 2. 提取高清头像
        if head_view and details["wxid"]:
            print(f"[详情同步] 正在提取高清头像 (wxid={details['wxid']})...")
            try:
                info_hwnd = 0
                try:
                    info_hwnd = pop_win.NativeWindowHandle
                except Exception:
                    pass
                exclude = {info_hwnd} if info_hwnd else None

                avatar_path = download_avatar_from_head_view(
                    head_view=head_view,
                    wxid=details["wxid"],
                    main_hwnd=self.driver.hwnd,
                    exclude_hwnds=exclude,
                    is_friend=True,
                    bot_wxid=getattr(self.driver, "_wxid", None)
                )
                if avatar_path and os.path.exists(avatar_path):
                    details["avatar_url"] = self._avatar_png_to_jpeg_data_uri(avatar_path)
            except Exception as e:
                print(f"[详情同步] 头像提取失败: {e}")

        return details
