r"""
诊断脚本 v6 (final)：完整模拟同步流程
每次点击前都重新获取当前可见行引用，避免行引用失效问题。
结果保存到 diag_result.log
"""
import sys, time, re, ctypes
from typing import List, Dict, Tuple
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import uiautomation as uia

MAX_TEST = 5
LOG_FILE = "diag_result.log"
log_lines = []

def log(msg):
    print(msg)
    log_lines.append(msg)

def bring_front(ctrl):
    try:
        ctypes.windll.user32.SetForegroundWindow(ctrl.NativeWindowHandle)
        time.sleep(0.3)
    except: pass

def ex(ctrl, t=1.0):
    try: return ctrl is not None and ctrl.Exists(t)
    except: return False

def wxid_like(s):
    t = (s or "").strip()
    return 4 <= len(t) <= 40 and bool(re.match(r"^[a-zA-Z0-9_\.\-]+$", t))

def collect_texts(scope, wr):
    rows = []
    try:
        for child, _ in uia.WalkControl(scope, maxDepth=22):
            try:
                ct = child.ControlTypeName or ""
                cls = child.ClassName or ""
                nm = ""
                if ct == "TextControl":
                    nm = (child.Name or "").strip()
                elif ct == "EditControl":
                    try: nm = (child.GetValuePattern().Value or "").strip()
                    except: nm = (child.Name or "").strip()
                else: continue
                if not nm or len(nm) > 500: continue
                rect = child.BoundingRectangle
                if rect.left < wr[0] or rect.right > wr[2] or rect.top < wr[1] or rect.bottom > wr[3]:
                    continue
                rows.append((int(rect.top), int(rect.left), nm, cls))
            except: continue
    except: pass
    rows.sort()
    return [(r[2], r[3], r[0], r[1]) for r in rows]

def find_anchor_scope(wc, wr):
    wl, ww = wr[0], wr[2] - wr[0]
    rx = wl + int(ww * 0.4)
    anchor, aname = None, ""
    for pf in ("地区", "微信号", "昵称"):
        for sc in ("：", ":"):
            try:
                t = wc.TextControl(Name=f"{pf}{sc}")
                if ex(t, 0.3) and t.BoundingRectangle.left >= rx:
                    anchor, aname = t, f"{pf}{sc}"; break
            except: continue
        if anchor: break
    if not anchor:
        try:
            b = wc.ButtonControl(Name="发消息")
            if ex(b, 0.3) and b.BoundingRectangle.left >= rx:
                anchor, aname = b, "发消息"
        except: pass
    scope = anchor
    if anchor:
        p = anchor
        for _ in range(6):
            try:
                parent = p.GetParentControl()
                if not parent: break
                if parent.BoundingRectangle.width() >= ww * 0.85: break
                p = parent
            except: break
        scope = p
    return anchor, aname, scope

def parse_from_classified(raw):
    """使用 ClassName 区分标签和值来解析"""
    r = {"wxid":"","region":"","nickname":"","remark":"","source":"","signature":""}
    pending = None
    for text, cls, top, left in raw:
        s = text.strip()
        if not s: continue
        # XTextView = 标签行
        if "XTextView" in cls:
            if "微信号" in s:
                if "：" in s or ":" in s:
                    v = s.split("：",1)[-1].split(":",1)[-1].strip()
                    if wxid_like(v): r["wxid"] = v; pending = None; continue
                pending = "wxid"
            elif "地区" in s:
                if "：" in s or ":" in s:
                    v = s.split("：",1)[-1].split(":",1)[-1].strip()
                    if v: r["region"] = v; pending = None; continue
                pending = "region"
            elif "昵称" in s:
                pending = "nickname"
            elif s in ("备注", "备注：", "备注:"):
                pending = "remark"
            elif "来源" in s:
                pending = "source"
            elif "个性签名" in s:
                pending = "signature"
            else:
                pending = None
        elif "ContactProfileTextView" in cls:
            # 值行
            if pending == "wxid" and wxid_like(s):
                r["wxid"] = s
            elif pending == "region":
                r["region"] = s
            elif pending == "nickname":
                r["nickname"] = s
            elif pending == "remark":
                r["remark"] = s
            elif pending == "source":
                r["source"] = s
            elif pending == "signature":
                r["signature"] = s
            pending = None
    # 第一个 XTextView（在标签之前）通常是大标题=昵称/备注名
    for text, cls, _, _ in raw:
        s = text.strip()
        if "XTextView" in cls and s and not any(
            kw in s for kw in ("微信号", "地区", "昵称", "备注", "来源", "朋友权限", "朋友圈",
                               "发消息", "语音聊天", "视频聊天", "个性签名")):
            if not r["nickname"]:
                r["nickname"] = s
            break
    return r

def main():
    log("=" * 60)
    log("  诊断脚本 v6：模拟同步头像及详情流程")
    log("=" * 60)

    wc = uia.WindowControl(ClassName="mmui::MainWindow")
    if not ex(wc, 3): log("  [X] 微信未打开"); return
    bring_front(wc)
    r = wc.BoundingRectangle
    wr = (int(r.left), int(r.top), int(r.right), int(r.bottom))
    log(f"  微信窗口: w={wr[2]-wr[0]} h={wr[3]-wr[1]}")

    # 通讯录
    btn = wc.ButtonControl(Name="通讯录")
    if ex(btn, 2):
        btn.Click()
        time.sleep(1)
    cl = wc.ListControl(Name="通讯录")
    if not ex(cl, 2): log("  [X] 无列表"); return

    # 确保展开
    has_items = any("ContactsCellItemView" in (i.ClassName or "") for i in cl.GetChildren())
    if not has_items:
        for item in cl.GetChildren():
            if (item.Name or "").strip().startswith("联系人"):
                log(f"  展开 '{item.Name}'")
                item.Click()
                time.sleep(2)
                break

    log("\n  逐个点击联系人（每次重新获取行引用）：")
    processed = set()
    count = 0

    for scroll_round in range(5):
        if count >= MAX_TEST: break
        items = cl.GetChildren()
        found_new = False
        for item in items:
            if count >= MAX_TEST: break
            nm = (item.Name or "").strip()
            cls = (item.ClassName or "").strip()
            if "ContactsCellItemView" not in cls or not nm or nm in processed:
                continue

            processed.add(nm)
            found_new = True
            count += 1

            log(f"\n{'~'*50}")
            log(f"  [{count}/{MAX_TEST}] '{nm}'")
            log(f"{'~'*50}")

            # 检查 BoundingRectangle 有效性
            try:
                ir = item.BoundingRectangle
                iw, ih = ir.width(), ir.height()
                log(f"  行矩形: ({ir.left},{ir.top}) {iw}x{ih}")
                if iw <= 0 or ih <= 0:
                    log(f"  [SKIP] BoundingRectangle 无效 (行不在屏幕上)")
                    continue
            except Exception as e:
                log(f"  [SKIP] 无法读取行矩形: {e}")
                continue

            bring_front(wc)
            try:
                item.Click()
            except Exception:
                try:
                    uia.Click(int((ir.left+ir.right)/2), int((ir.top+ir.bottom)/2))
                except: pass
            time.sleep(1.2)

            anchor, aname, scope = find_anchor_scope(wc, wr)
            if anchor:
                ar = anchor.BoundingRectangle
                log(f"  锚点: '{aname}' at ({ar.left},{ar.top})")
            else:
                log("  [WARN] 未找到锚点")

            raw = collect_texts(scope or wc, wr)
            log(f"  右侧文本行 ({len(raw)}条):")
            for i,(t,c,top,left) in enumerate(raw[:18]):
                mk = ">>" if "ContactProfile" in c else "  "
                log(f"  {mk}[{i:2d}] '{t}' cls={c}")

            d = parse_from_classified(raw)
            log(f"  解析: wxid='{d['wxid']}' region='{d['region']}' nick='{d['nickname']}' "
                f"remark='{d['remark']}' sig='{d['signature']}' src='{d['source']}'")

            # 身份匹配
            tokens = {nm}
            found = False
            for t,c,_,_ in raw:
                s = t.strip()
                for tk in tokens:
                    if tk and (s == tk or tk in s or s in tk):
                        found = True
                        log(f"  [OK] 身份匹配: '{tk}' <-> '{s}'")
                        break
                if found: break
            if not found:
                log(f"  [FAIL] 身份名 '{nm}' 未出现在右侧")
                # 看看大标题是什么
                for t,c,_,_ in raw:
                    if "XTextView" in c and t.strip() and not any(
                        kw in t for kw in ("微信号", "地区", "昵称", "备注", "来源",
                                           "朋友权限", "朋友圈", "发消息")):
                        log(f"  [INFO] 右侧大标题: '{t.strip()}'（可能是昵称，列表行可能是备注名）")
                        break

        if not found_new:
            # 滚动露出新行
            try:
                cr = cl.BoundingRectangle
                import win32api
                old = win32api.GetCursorPos()
                x = int((cr.left + cr.right) / 2)
                y = int((cr.top + cr.bottom) / 2)
                win32api.SetCursorPos((x, y))
                time.sleep(0.1)
                cl.WheelDown(wheelTimes=4)
                time.sleep(0.8)
                win32api.SetCursorPos(old)
            except:
                break

    log("\n" + "=" * 60)
    log("  诊断完成")
    log("=" * 60)

    # 保存日志
    from pathlib import Path
    logp = Path(__file__).parent / LOG_FILE
    logp.write_text("\n".join(log_lines), encoding="utf-8")
    print(f"\n日志已保存到: {logp}")

if __name__ == "__main__":
    main()
