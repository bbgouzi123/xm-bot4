import re
import time

from typing import Any, Dict, List, Optional, Set, Tuple

import uiautomation as uia

from ..retry import exists_with_timeout, is_escape_pressed
from .constants import is_contact_sync_pause_requested


class ContactProfileMixin:
    def _find_wechat_profile_right_detail_anchor(self):
        """查找右侧详情面板的锚点控件（地区/微信号/昵称标签或发消息按钮）。

        经实测：root.TextControl(Name="地区：").Exists() 单次耗时 4s（UIA 深层搜索）；
        而 WalkControl(root, maxDepth=22) 遍历微信窗口只有约 500 个节点、0.4s，
        性能远优于逐个 Exists。因此使用 WalkControl 作为唯一查找策略。
        """
        root = self.driver.root
        if not root:
            return None
        _t = time.time()
        try:
            for ctrl, _depth in uia.WalkControl(root, maxDepth=22):
                if is_escape_pressed() or is_contact_sync_pause_requested():
                    return None
                try:
                    ctype = ctrl.ControlTypeName
                    nm = (ctrl.Name or "").strip()
                    if not nm:
                        continue
                    if ctype == "TextControl" and nm in (
                        "地区：", "地区:", "微信号：", "微信号:", "昵称：", "昵称:",
                    ):
                        print(f"[详情同步]   锚点命中: {nm!r} ({time.time() - _t:.2f}s)")
                        return ctrl
                    if ctype == "ButtonControl" and nm in ("发消息", "接受"):
                        print(f"[详情同步]   锚点命中(按钮): {nm!r} ({time.time() - _t:.2f}s)")
                        return ctrl
                except Exception:
                    continue
        except Exception:
            pass
        print(f"[详情同步]   锚点未找到 ({time.time() - _t:.2f}s)")
        return None

    def _expand_profile_scope_from_anchor(self, anchor):
        if not anchor:
            return None
        try:
            wr = self.driver.root.BoundingRectangle
            win_w = wr.width()
        except Exception:
            win_w = 9999
        p = anchor
        for _ in range(6):
            try:
                parent = p.GetParentControl()
                if not parent:
                    break
                try:
                    pr = parent.BoundingRectangle
                    pw = pr.width()
                    if pw >= win_w * 0.85:
                        break
                except Exception:
                    pass
                p = parent
            except Exception:
                break
        return p

    @staticmethod
    def _looks_like_wechat_wxid(s: str) -> bool:
        t = (s or "").strip()
        if len(t) < 4 or len(t) > 40:
            return False
        return bool(re.match(r"^[a-zA-Z0-9_\-\.]+$", t))

    def _collect_profile_text_lines_sorted(self, scope, max_depth: int = 22) -> List[str]:
        """收集 scope 内的文本行，按 y/x 坐标排序。

        重要：通过 BoundingRectangle 过滤掉非微信区域的控件，
        防止把 Edge、VS Code 等桌面窗口的文本混入。
        """
        rows: List[Tuple[int, int, str]] = []
        import uiautomation as uia_lib

        # 获取微信窗口范围，用于过滤
        try:
            wr = self.driver.root.BoundingRectangle
            wx_left, wx_top = int(wr.left), int(wr.top)
            wx_right, wx_bottom = int(wr.right), int(wr.bottom)
        except Exception:
            wx_left, wx_top, wx_right, wx_bottom = 0, 0, 99999, 99999

        try:
            for child, _depth in uia_lib.WalkControl(scope, maxDepth=max_depth):
                if is_escape_pressed() or is_contact_sync_pause_requested():
                    break
                try:
                    ctype = child.ControlTypeName or ""
                    nm = ""
                    if ctype == "TextControl":
                        nm = (child.Name or "").strip()
                    elif ctype == "EditControl":
                        try:
                            vp = child.GetValuePattern()
                            nm = (vp.Value or "").strip()
                        except Exception:
                            nm = (child.Name or "").strip()
                    elif ctype == "HyperlinkControl":
                        nm = (child.Name or "").strip()
                    else:
                        continue
                    if not nm or len(nm) > 600:
                        continue
                    rect = child.BoundingRectangle
                    cx, cy = int(rect.left), int(rect.top)
                    # 只保留在微信窗口范围内的控件
                    if cx < wx_left or cx > wx_right or cy < wx_top or cy > wx_bottom:
                        continue
                    rows.append((cy, cx, nm))
                except Exception:
                    continue
        except Exception:
            pass
        rows.sort(key=lambda x: (x[0], x[1]))
        return [x[2] for x in rows]

    def _parse_wechat_profile_right_details_walk_legacy(self, scope, friend_name: str) -> Tuple[str, str, str, str]:
        wxid, region, signature, source = "", "", "", ""
        if not scope:
            return wxid, region, signature, source
        import uiautomation as uia_lib

        pending = None
        try:
            for child, _depth in uia_lib.WalkControl(scope, maxDepth=20):
                try:
                    nm = (child.Name or "").strip()
                    if not nm or len(nm) > 400:
                        continue
                    if ("微信号" in nm) and ("：" in nm or ":" in nm) and len(nm) > 4:
                        tail = nm.split("：", 1)[1].strip() if "：" in nm else nm.split(":", 1)[-1].strip()
                        if tail and not wxid:
                            wxid = tail
                        continue
                    if ("地区" in nm) and ("：" in nm or ":" in nm) and len(nm) > 5:
                        tail = nm.split("：", 1)[1].strip() if "：" in nm else nm.split(":", 1)[-1].strip()
                        if tail and not region:
                            region = tail
                        continue
                    if nm in ("微信号：", "微信号:") or (nm.startswith("微信号") and len(nm) <= 4):
                        pending = "wxid"
                        continue
                    if nm in ("地区：", "地区:") or (nm.startswith("地区") and len(nm) <= 4):
                        pending = "region"
                        continue
                    if nm in ("个性签名", "个性签名：", "个性签名:"):
                        pending = "sig"
                        continue
                    if nm in ("来源", "来源：", "来源:"):
                        pending = "src"
                        continue
                    if pending == "wxid" and not nm.endswith("：") and not nm.endswith(":"):
                        wxid = nm
                        pending = None
                        continue
                    if pending == "region" and not nm.endswith("：") and not nm.endswith(":"):
                        region = nm
                        pending = None
                        continue
                    if pending == "sig":
                        signature = nm
                        pending = None
                        continue
                    if pending == "src":
                        source = nm
                        pending = None
                        continue
                    if nm.startswith("通过") and len(nm) < 120 and not source:
                        source = nm
                except Exception:
                    continue
        except Exception:
            pass
        return wxid, region, signature, source

    def _parse_remark_from_sorted_lines(self, lines: List[str]) -> str:
        """从排序后的文本行中提取备注名。
        
        优化：排除掉占位符和后续标签，防止错位。
        """
        blob = "\n".join(lines)
        m = re.search(r"备注\s*[：:]\s*([^\n\r]+)", blob)
        if m:
            val = m.group(1).strip()
            if val and val not in ("添加备注名", "朋友圈", "视频号", "地区", "微信号", "个性签名", "来源"):
                return val
            return ""

        reserved_labels = {"备注", "备注：", "备注:", "朋友圈", "视频号", "地区", "微信号", "个性签名", "来源", "添加备注名"}
        for i, line in enumerate(lines):
            ln = line.strip()
            if ln in ("备注", "备注：", "备注:") or re.match(r"^备注\s*[：:]\s*$", ln):
                if i + 1 < len(lines):
                    val = lines[i + 1].strip()
                    if val and val not in reserved_labels:
                        return val
        return ""

    def _parse_nickname_from_sorted_lines(self, lines: List[str]) -> str:
        """从排序后的文本行中提取联系人昵称。"""
        blob = "\n".join(lines)
        reserved = {"微信号", "地区", "备注", "朋友圈", "视频号", "个性签名", "来源", "添加备注名"}
        
        m = re.search(r"昵称\s*[：:]\s*([^\n\r]+)", blob)
        if m:
            val = m.group(1).strip()
            if val and val not in reserved:
                return val
            return ""

        for i, line in enumerate(lines):
            ln = line.strip()
            if ln in ("昵称", "昵称：", "昵称:") or re.match(r"^昵称\s*[：:]\s*$", ln):
                if i + 1 < len(lines):
                    val = lines[i + 1].strip()
                    if val and val not in reserved:
                        return val
        return ""

    def _remark_matches_friend(self, remark_parsed: str, friend_dict: Dict[str, Any]) -> bool:
        r = (remark_parsed or "").strip()
        if not r:
            return False
        name = (friend_dict.get("name") or "").strip()
        remark = (friend_dict.get("remark") or "").strip()
        return bool((name and r == name) or (remark and r == remark))

    def _friend_identity_display_tokens(self, friend_dict: Dict[str, Any]) -> Set[str]:
        out: Set[str] = set()
        for key in ("name", "remark"):
            v = (friend_dict.get(key) or "").strip()
            if v:
                out.add(v)
        return out

    def _right_panel_lines_match_friend_identity(
        self,
        lines: List[str],
        friend_dict: Dict[str, Any],
    ) -> bool:
        targets = self._friend_identity_display_tokens(friend_dict)
        if not targets:
            return False
        for line in lines:
            s = (line or "").strip()
            if not s:
                continue
            for target in targets:
                if target and (s == target or target in s or s in target):
                    return True
        return False

    def _parse_wechat_profile_right_details_from_lines(
        self,
        lines: List[str],
        scope,
        friend_name: str,
    ) -> Tuple[str, str, str, str]:
        wxid, region, signature, source = "", "", "", ""
        if not scope:
            return wxid, region, signature, source

        blob = "\n".join(lines)
        m = re.search(r"微信号\s*[：:]\s*(\S+)", blob)
        if m:
            cand = m.group(1).strip()
            if self._looks_like_wechat_wxid(cand):
                wxid = cand

        m = re.search(r"地区\s*[：:]\s*([^\n\r]+)", blob)
        if m:
            region = m.group(1).strip()

        # 标签+下一行解析（微信面板中标签和值经常是独立文本行）
        reserved = {"微信号", "地区", "备注", "朋友圈", "视频号", "个性签名", "来源", "添加备注名"}
        for i, line in enumerate(lines):
            ln = line.strip()
            if not wxid and (ln in ("微信号", "微信号：", "微信号:") or re.match(r"^微信号\s*[：:]\s*$", ln)):
                if i + 1 < len(lines):
                    cand = lines[i + 1].strip()
                    if self._looks_like_wechat_wxid(cand) and cand not in reserved:
                        wxid = cand
            if not region and (ln in ("地区", "地区：", "地区:") or re.match(r"^地区\s*[：:]\s*$", ln)):
                if i + 1 < len(lines):
                    cand = lines[i + 1].strip()
                    if cand and len(cand) < 80 and cand not in reserved:
                        region = cand
            if not signature and (ln in ("个性签名", "个性签名：", "个性签名:")):
                if i + 1 < len(lines):
                    cand = lines[i + 1].strip()
                    if cand and len(cand) < 200 and cand not in reserved:
                        signature = cand
            if not source and (ln in ("来源", "来源：", "来源:")):
                if i + 1 < len(lines):
                    cand = lines[i + 1].strip()
                    if cand and len(cand) < 120 and cand not in reserved:
                        source = cand

        # regex 回退 (行内合并形式)
        if not signature:
            m = re.search(r"个性签名\s*[：:]\s*([^\n\r]+)", blob)
            if m:
                signature = m.group(1).strip()
        if not source:
            m = re.search(r"来源\s*[：:]\s*([^\n\r]+)", blob)
            if m:
                source = m.group(1).strip()
        # "通过xxx添加" 模式
        if not source:
            for line in lines:
                s = line.strip()
                if s.startswith("通过") and len(s) < 120:
                    source = s
                    break

        wxid = (wxid or "").strip()
        if wxid and not self._looks_like_wechat_wxid(wxid):
            wxid = ""
        return wxid, region, signature, source

    def _parse_wechat_profile_right_details(self, scope, friend_name: str) -> Tuple[str, str, str, str]:
        if not scope:
            return "", "", "", ""
        lines = self._collect_profile_text_lines_sorted(scope)
        return self._parse_wechat_profile_right_details_from_lines(lines, scope, friend_name)

    def _poll_stable_profile_details(
        self,
        friend_dict: Dict[str, Any],
        name: str,
    ) -> Tuple[str, str, str, str, bool, Any]:
        """一次性读取微信右侧面板的联系人详情。

        sync_details 在调用前已经点击了微信列表中的联系人，
        右侧面板显示的就是目标联系人——以微信为准，读到什么覆盖什么。
        """
        _start = time.time()
        print(f"[详情同步] 读取 profile: name={name!r}")

        if is_escape_pressed() or is_contact_sync_pause_requested():
            return "", "", "", "", False, None

        try:
            anchor = self._find_wechat_profile_right_detail_anchor()
            scope = self._expand_profile_scope_from_anchor(anchor)
            if not scope:
                print(f"[详情同步]   scope=None ({time.time() - _start:.2f}s)")
                return "", "", "", "", False, None

            lines = self._collect_profile_text_lines_sorted(scope)
            wxid, region, signature, source = self._parse_wechat_profile_right_details_from_lines(
                lines, scope, name
            )
            wxid = (wxid or "").strip()
            remark_parsed = self._parse_remark_from_sorted_lines(lines)
            _cost = time.time() - _start
            print(f"[详情同步]   V wxid={wxid!r}, remark={remark_parsed!r}, region={region!r}, sig={signature[:20] if signature else ''!r}, src={source!r} ({_cost:.2f}s)")
            return wxid, region, signature, source, True, scope
        except Exception as _e:
            print(f"[详情同步]   读取异常: {_e} ({time.time() - _start:.2f}s)")
            return "", "", "", "", False, None


