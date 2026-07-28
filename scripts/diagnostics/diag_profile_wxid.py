"""
诊断：通讯录右侧资料区 wxid 解析是否稳定。

用法（Windows，微信已打开）：
  1. 打开「通讯录」并选中一名好友，右侧显示资料卡（含微信号）。
  2. 在 backend-python 目录下执行：
     python scripts/diagnostics/diag_profile_wxid.py

输出：按坐标排序的 Text 行、连续多次解析结果、以及一次 _poll_stable_profile_details（无 expect）是否成功。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from uia.driver import WeChatDriver
from uia.contacts import ContactSync


def main() -> int:
    d = WeChatDriver()
    if not d.connect():
        print("WeChatDriver.connect() 失败，请确认微信已启动。")
        return 1
    cs = ContactSync(d)
    anchor = cs._find_wechat_profile_right_detail_anchor()
    scope = cs._expand_profile_scope_from_anchor(anchor)
    if not scope:
        print("无法解析右侧资料 scope（锚点缺失）。请先打开通讯录并选中一名好友。")
        return 1

    print("=== 按 BoundingRectangle 排序的 Text 行（生产环境同款）===")
    lines = cs._collect_profile_text_lines_sorted(scope)
    for i, ln in enumerate(lines):
        print(f"{i:4d}  {ln!r}")
    print(f"\n共 {len(lines)} 行\n")

    print("=== 连续 8 次 _parse_wechat_profile_right_details（间隔 0.15s）===")
    for r in range(8):
        wxid, region, sig, src = cs._parse_wechat_profile_right_details(scope, "diag")
        sig_s = repr(sig) if len(sig) <= 28 else repr(sig[:28] + "…")
        src_s = repr(src) if len(src) <= 44 else repr(src[:44] + "…")
        print(f"  #{r + 1} wxid={wxid!r} region={region!r} sig={sig_s} src={src_s}")
        time.sleep(0.15)

    print("\n=== _poll_stable_profile_details（friend_dict.wxid 为空，走串号防护逻辑）===")
    fd = {"wxid": ""}
    t0 = time.time()
    wxid, region, sig, src, ok, _sc = cs._poll_stable_profile_details(fd, "diag")
    dt = time.time() - t0
    print(f"  ok={ok} 耗时约 {dt:.2f}s")
    print(f"  wxid={wxid!r} region={region!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
