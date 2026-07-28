"""
测试脚本：验证通讯录好友头像同步的 UIA 定位逻辑

目标：
1. 打开微信通讯录，定位中间列好友行
2. 点击某一行后，验证右侧资料卡是否正确加载该好友的资料
3. 截取头像并保存，验证截到的是好友头像而非当前登录者头像
4. 输出所有候选头像控件的坐标、尺寸、来源信息用于诊断

运行方式:
    cd backend-python
    python scripts/test_sync_avatar.py

需要：
- 微信 PC 端已登录并在前台
- 已切换到「通讯录」页面
- Python 环境安装了 uiautomation、Pillow
"""
import os
import sys
import time
import json

# 确保能导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

try:
    import comtypes
    comtypes.CoInitialize()
except Exception:
    pass

import uiautomation as uia


# ═══ 配置 ═══
OUTPUT_DIR = os.path.join(os.path.expanduser("~/.xm-ai-bot"), "test_avatar_sync")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg: str):
    try:
        print(f"[TEST] {msg}")
    except UnicodeEncodeError:
        print(f"[TEST] {msg.encode('ascii', 'replace').decode()}")


def find_wechat_window():
    """找到微信主窗口"""
    win = uia.WindowControl(ClassName="WeChatMainWndForPC")
    if win.Exists(3):
        log(f"✅ 找到微信窗口: {win.Name}")
        return win
    log("❌ 未找到微信主窗口 (WeChatMainWndForPC)")
    return None


def find_contacts_list(root):
    """找到通讯录中间列主列表"""
    # 方法1: StickyHeaderRecyclerListView
    for ctrl, depth in uia.WalkControl(root, maxDepth=22):
        try:
            cls = ctrl.ClassName or ""
            if "StickyHeaderRecycler" in cls and ctrl.ControlTypeName == "ListControl":
                if ctrl.Exists(1):
                    log(f"✅ 找到通讯录列表 (StickyHeaderRecycler), depth={depth}")
                    return ctrl
        except Exception:
            continue

    # 方法2: TreeControl(Name=联系人)
    try:
        t = root.TreeControl(Name="联系人")
        if t and t.Exists(1):
            log("✅ 找到通讯录列表 (TreeControl Name=联系人)")
            return t
    except Exception:
        pass

    # 方法3: ListControl(Name=联系人)
    try:
        l = root.ListControl(Name="联系人")
        if l and l.Exists(1):
            log("✅ 找到通讯录列表 (ListControl Name=联系人)")
            return l
    except Exception:
        pass

    log("❌ 未找到通讯录主列表控件")
    return None


def get_window_info(root):
    """获取窗口的布局信息"""
    try:
        r = root.BoundingRectangle
        return {
            "left": r.left,
            "top": r.top,
            "right": r.right,
            "bottom": r.bottom,
            "width": r.width(),
            "height": r.height(),
        }
    except Exception as e:
        return {"error": str(e)}


def scan_contact_rows(contacts_list, max_rows=10):
    """扫描通讯录中可见的联系人行"""
    rows = []
    sys_prefixes = ("新的朋友", "公众号", "企业微信联系人", "群聊", "标签", "服务号", "我的企业", "联系人")

    items = contacts_list.GetChildren()
    log(f"📋 当前可见行数: {len(items)}")

    for item in items:
        name = (item.Name or "").strip()
        if not name:
            continue
        # 跳过字母索引和系统项
        if len(name) == 1 and name.isalpha():
            continue
        is_sys = False
        for pre in sys_prefixes:
            if name.startswith(pre):
                suffix = name[len(pre):].strip()
                if not suffix or suffix.isdigit():
                    is_sys = True
                    break
        if is_sys:
            continue

        try:
            rect = item.BoundingRectangle
            rows.append({
                "name": name,
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": rect.width(),
                "height": rect.height(),
                "element": item,
            })
        except Exception:
            rows.append({"name": name, "error": "无法获取矩形", "element": item})

        if len(rows) >= max_rows:
            break

    return rows


def find_right_panel_avatar_candidates(root, win_info):
    """在右侧资料卡区域扫描所有候选头像控件"""
    candidates = []
    # 右侧大约从窗口 43% 宽开始
    min_avatar_x = win_info["left"] + int(win_info["width"] * 0.43)

    DENY_NAMES = {"发消息", "语音聊天", "视频聊天", "更多", "资料", "添加到通讯录", "删除"}
    DENY_SUBSTR = ("发消息", "语音", "视频聊天", "朋友圈", "更多信息")

    for child, depth in uia.WalkControl(root, maxDepth=14):
        try:
            ctype = child.ControlTypeName
            if "Image" not in ctype and "Button" not in ctype:
                continue
            nm = (child.Name or "").strip()
            if nm in DENY_NAMES:
                continue
            if any(sub in nm for sub in DENY_SUBSTR):
                continue

            rect = child.BoundingRectangle
            w, h = rect.width(), rect.height()
            if w < 32 or h < 32:
                continue
            if max(w, h) > 260:
                continue
            aratio = max(w, h) / max(min(w, h), 1)
            if aratio > 1.38:
                continue
            if rect.left < min_avatar_x:
                continue

            area = w * h
            candidates.append({
                "type": ctype,
                "name": nm,
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": w,
                "height": h,
                "area": area,
                "is_image": "Image" in ctype,
                "element": child,
            })
        except Exception:
            continue

    # 按面积排序
    candidates.sort(key=lambda c: c["area"], reverse=True)
    return candidates


def find_list_row_avatar(row_elem, win_info):
    """在通讯录中间列单行内寻找小头像"""
    candidates = []
    min_abs_x = win_info["left"] + max(int(win_info["width"] * 0.18), 96)

    try:
        row_rect = row_elem.BoundingRectangle
        row_left = int(row_rect.left)
        row_w = max(int(row_rect.width()), 1)
    except Exception:
        return None, []

    # 行宽占满整窗说明不是单行
    if row_w > int(win_info["width"] * 0.88):
        return None, []

    max_avatar_right = row_left + min(int(row_w * 0.42), 140)

    for child, depth in uia.WalkControl(row_elem, maxDepth=8):
        try:
            ctype = child.ControlTypeName
            if "Image" not in ctype and "Button" not in ctype:
                continue
            rect = child.BoundingRectangle
            w, h = rect.width(), rect.height()
            if w < 22 or h < 22 or max(w, h) > 96:
                continue
            aratio = max(w, h) / max(min(w, h), 1)
            if aratio > 1.45:
                continue
            left = int(rect.left)
            if left < min_abs_x:
                continue
            if rect.right > max_avatar_right:
                continue

            candidates.append({
                "type": ctype,
                "name": (child.Name or "").strip(),
                "left": rect.left,
                "top": rect.top,
                "width": w,
                "height": h,
                "area": w * h,
                "element": child,
            })
        except Exception:
            continue

    if not candidates:
        return None, candidates
    # 优先 ImageControl，其次面积接近 48x48 的
    candidates.sort(key=lambda c: (1 if c["type"] == "ImageControl" else 0, -abs(c["area"] - 48*48)), reverse=True)
    return candidates[0], candidates


def extract_right_panel_details(root):
    """从右侧资料卡解析微信号、地区等文本"""
    details = {"wxid": "", "region": "", "texts": []}

    for ctrl, depth in uia.WalkControl(root, maxDepth=22):
        try:
            if ctrl.ControlTypeName != "TextControl":
                continue
            nm = (ctrl.Name or "").strip()
            if not nm or len(nm) > 400:
                continue
            rect = ctrl.BoundingRectangle
            details["texts"].append({
                "text": nm,
                "left": rect.left,
                "top": rect.top,
            })

            if "微信号" in nm and ("：" in nm or ":" in nm):
                tail = nm.split("：", 1)[1].strip() if "：" in nm else nm.split(":", 1)[-1].strip()
                if tail:
                    details["wxid"] = tail
            if "地区" in nm and ("：" in nm or ":" in nm):
                tail = nm.split("：", 1)[1].strip() if "：" in nm else nm.split(":", 1)[-1].strip()
                if tail:
                    details["region"] = tail
        except Exception:
            continue

    # 按 top 排序
    details["texts"].sort(key=lambda t: (t["top"], t["left"]))
    return details


def capture_avatar(element, filename):
    """截取头像到文件"""
    path = os.path.join(OUTPUT_DIR, filename)
    try:
        element.CaptureToImage(path)
        log(f"  📸 已保存: {path}")
        return path
    except Exception as e:
        log(f"  ❌ 截图失败: {e}")
        return None


def test_single_contact(root, contacts_list, row_info, win_info, index):
    """测试单个联系人的头像截取"""
    name = row_info["name"]
    elem = row_info["element"]
    log(f"\n{'='*60}")
    log(f"🔍 测试第 {index+1} 个联系人: 「{name}」")
    log(f"  行位置: left={row_info.get('left')}, top={row_info.get('top')}, "
        f"width={row_info.get('width')}, height={row_info.get('height')}")

    # 1. 先查中间列行内小头像
    list_avatar, list_candidates = find_list_row_avatar(elem, win_info)
    log(f"  中间列行内候选头像: {len(list_candidates)} 个")
    for i, c in enumerate(list_candidates):
        log(f"    [{i}] {c['type']} name='{c['name']}' "
            f"pos=({c['left']},{c['top']}) size={c['width']}x{c['height']} area={c['area']}")

    if list_avatar:
        log(f"  ✅ 选定中间列头像: {list_avatar['type']} "
            f"pos=({list_avatar['left']},{list_avatar['top']}) "
            f"size={list_avatar['width']}x{list_avatar['height']}")
        capture_avatar(list_avatar["element"], f"list_avatar_{index}_{name}.png")

    # 2. 点击该行，等待右侧资料卡加载
    log(f"  👆 点击该行...")
    try:
        rect = elem.BoundingRectangle
        x = (rect.left + rect.right) // 2
        y = (rect.top + rect.bottom) // 2
        uia.Click(x, y)
        time.sleep(1.2)
    except Exception as e:
        log(f"  ❌ 点击失败: {e}")
        return

    # 3. 解析右侧资料
    details = extract_right_panel_details(root)
    log(f"  右侧资料: wxid={details['wxid']!r}, region={details['region']!r}")
    log(f"  右侧文本行数: {len(details['texts'])}")
    for t in details["texts"][:10]:
        log(f"    text='{t['text'][:50]}' pos=({t['left']},{t['top']})")

    # 4. 在右侧资料卡搜索头像候选
    right_candidates = find_right_panel_avatar_candidates(root, win_info)
    log(f"  右侧资料卡候选头像: {len(right_candidates)} 个")
    for i, c in enumerate(right_candidates):
        log(f"    [{i}] {c['type']} name='{c['name']}' "
            f"pos=({c['left']},{c['top']}) size={c['width']}x{c['height']} "
            f"area={c['area']} is_image={c['is_image']}")

    if right_candidates:
        best = right_candidates[0]
        log(f"  ✅ 选定右侧头像(最大面积): {best['type']} "
            f"pos=({best['left']},{best['top']}) size={best['width']}x{best['height']}")
        capture_avatar(best["element"], f"right_avatar_{index}_{name}.png")

    # 5. 同时截取整行作为对比
    capture_avatar(elem, f"row_full_{index}_{name}.png")

    return {
        "name": name,
        "list_avatar_found": list_avatar is not None,
        "list_candidates_count": len(list_candidates),
        "right_candidates_count": len(right_candidates),
        "wxid": details["wxid"],
        "region": details["region"],
    }


def main():
    log("=" * 60)
    log("通讯录好友头像同步 UIA 诊断脚本")
    log("=" * 60)

    # 1. 找到微信窗口
    root = find_wechat_window()
    if not root:
        return

    win_info = get_window_info(root)
    log(f"📐 窗口布局: {json.dumps(win_info, ensure_ascii=False)}")

    # 2. 找到通讯录列表
    contacts_list = find_contacts_list(root)
    if not contacts_list:
        log("⚠️ 请先点击微信左侧「通讯录」按钮")
        return

    # 3. 扫描可见联系人
    rows = scan_contact_rows(contacts_list, max_rows=5)
    log(f"\n📋 扫描到 {len(rows)} 个可测试联系人:")
    for i, r in enumerate(rows):
        log(f"  [{i}] {r['name']}")

    if not rows:
        log("⚠️ 未发现可测试的联系人行")
        return

    # 4. 左侧导航栏头像位置诊断（排除误截当前登录者头像）
    log(f"\n🔬 窗口左侧导航栏区域 (0 ~ {int(win_info['width'] * 0.18)}) 内的 Image/Button:")
    nav_threshold = win_info["left"] + max(int(win_info["width"] * 0.18), 96)
    nav_images = []
    for ctrl, depth in uia.WalkControl(root, maxDepth=10):
        try:
            ctype = ctrl.ControlTypeName
            if "Image" not in ctype and "Button" not in ctype:
                continue
            rect = ctrl.BoundingRectangle
            if rect.left > nav_threshold:
                continue
            w, h = rect.width(), rect.height()
            if w < 20 or h < 20:
                continue
            nm = (ctrl.Name or "").strip()
            info = f"  {ctype} name='{nm}' pos=({rect.left},{rect.top}) size={w}x{h}"
            log(info)
            nav_images.append({
                "type": ctype,
                "name": nm,
                "left": rect.left,
                "top": rect.top,
                "width": w,
                "height": h,
            })
        except Exception:
            continue

    log(f"  → 导航栏共 {len(nav_images)} 个 Image/Button 控件")
    log(f"  → 中间列头像最小 X 阈值 (min_abs_x): {nav_threshold}")

    # 5. 逐个测试联系人
    results = []
    for i, row in enumerate(rows):
        result = test_single_contact(root, contacts_list, row, win_info, i)
        if result:
            results.append(result)
        time.sleep(0.5)

    # 6. 输出汇总
    log(f"\n{'='*60}")
    log("📊 测试汇总")
    log(f"{'='*60}")
    log(f"测试联系人数: {len(results)}")
    log(f"中间列行内头像成功: {sum(1 for r in results if r['list_avatar_found'])}")
    log(f"右侧资料卡头像候选非空: {sum(1 for r in results if r['right_candidates_count'] > 0)}")
    log(f"成功解析微信号: {sum(1 for r in results if r['wxid'])}")
    log(f"截图保存路径: {OUTPUT_DIR}")

    # 保存报告
    report_path = os.path.join(OUTPUT_DIR, "test_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log(f"📄 报告已保存: {report_path}")


if __name__ == "__main__":
    main()
