# -*- coding: utf-8 -*-
"""清理 contacts.py 中遗留的 uid_ 函数和注释"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 1. 清理 contacts.py 中的死函数
path = r'd:\code\xm-core\products\xm-bot4\backend-python\src\uia\contacts.py'
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()

# 删除 _is_synthetic_placeholder_wxid 函数
for nl in ['\r\n', '\n']:
    old = f'def _is_synthetic_placeholder_wxid(wxid: str) -> bool:{nl}    """本地无真实微信号时落库的 uid_昵称 占位（与 db_manager 一致），不可与右侧解析出的微信号做相等比对。"""{nl}    return bool((wxid or "").strip().startswith("uid_")){nl}{nl}{nl}'
    if old in c:
        c = c.replace(old, nl)
        print("[OK] removed _is_synthetic_placeholder_wxid")
        break
else:
    print("[SKIP] _is_synthetic_placeholder_wxid not found in expected format")

# 删除 _uid_placeholder_suffix 函数
for nl in ['\r\n', '\n']:
    old = f'def _uid_placeholder_suffix(wxid: str) -> str:{nl}    """uid_光勤 → 光勤，用于与右侧资料卡大标题 Text（如 mmui::XTextView Name）对齐。"""{nl}    w = (wxid or "").strip(){nl}    if not w.startswith("uid_"):{nl}        return ""{nl}    return w[4:].strip(){nl}{nl}{nl}'
    if old in c:
        c = c.replace(old, nl)
        print("[OK] removed _uid_placeholder_suffix")
        break
else:
    print("[SKIP] _uid_placeholder_suffix not found in expected format")

# 更新 _friend_identity_display_tokens 的 docstring
old_doc = '"""可与右侧资料标题行比对的身份串：主键名、备注、uid_ 占位去掉前缀后的昵称。"""'
new_doc = '"""可与右侧资料标题行比对的身份串：主键名、备注。"""'
if old_doc in c:
    c = c.replace(old_doc, new_doc)
    print("[OK] updated _friend_identity_display_tokens docstring")

# 更新 _poll_stable_profile_details 的 docstring 中的 uid_ 引用
old_doc2 = '无或仅为 uid_ 占位时：wxid 多数表决 / 连续两轮相同；若始终无 wxid，则「地区+备注」'
new_doc2 = '无 wxid 时：wxid 多数表决 / 连续两轮相同；若始终无 wxid，则「地区+备注」'
if old_doc2 in c:
    c = c.replace(old_doc2, new_doc2)
    print("[OK] updated _poll_stable docstring 1")

old_doc3 = '连续两轮一致且备注与好友名/备注匹配也可通过；或右侧文本行连续两轮出现与 name/remark/uid_后缀'
new_doc3 = '连续两轮一致且备注与好友名/备注匹配也可通过；或右侧文本行连续两轮出现与 name/remark'
if old_doc3 in c:
    c = c.replace(old_doc3, new_doc3)
    print("[OK] updated _poll_stable docstring 2")

# 更新 无缓存真实 wxid 注释
old_comment = '# 无缓存真实 wxid（如 uid_ 占位）且 UIA 未给出微信号'
new_comment = '# 无缓存 wxid 且 UIA 未给出微信号'
if old_comment in c:
    c = c.replace(old_comment, new_comment)
    print("[OK] updated comment")

with open(path, 'w', encoding='utf-8') as f:
    f.write(c)
print(f"saved: {path}")

# 2. 清理 task_api.py 注释
path2 = r'd:\code\xm-core\products\xm-bot4\backend-python\src\api\task_api.py'
with open(path2, 'r', encoding='utf-8') as f:
    c2 = f.read()
old_doc4 = '    for_mass_send=False → 优先 wxid，缺失则 uid_昵称（与通讯录落库一致）。'
new_doc4 = '    for_mass_send=False → 优先 wxid，缺失则用昵称。'
if old_doc4 in c2:
    c2 = c2.replace(old_doc4, new_doc4)
    with open(path2, 'w', encoding='utf-8') as f:
        f.write(c2)
    print("[OK] updated task_api.py docstring")

print("\nDone! All uid_ references cleaned.")
