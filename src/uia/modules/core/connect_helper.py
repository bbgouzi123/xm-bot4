import os
import re
import json
import logging
import sqlite3
import tempfile
import time
from typing import Dict, List, Optional

try:
    import uiautomation as uia
    import win32gui
except ImportError:
    uia = None
    win32gui = None

from src.uia.elements import WxClass

logger = logging.getLogger("WeChatDriver")

def is_wechat_title(title: str) -> bool:
    """判断窗口标题是否为微信主窗口"""
    if not title:
        return False
    t = title.strip()

    if len(t) >= 3:
        if t.startswith("[#]") or t.startswith("[#]"):
            t = t[3:].strip()
    if len(t) >= 3:
        if t.endswith("[#]") or t.endswith("[#]"):
            t = t[:-3].strip()

    if t == "微信":
        return True

    if t.endswith("微信") and t.startswith("[") and "] " in t:
        suffix = t.split("] ", 1)[1]
        if suffix == "微信":
            return True

    if t.endswith("微信") and t.startswith("[") and "]" in t:
        parts = t.split("]", 1)
        if len(parts) == 2 and parts[1].strip() == "微信":
            return True

    return False

def find_all_wechat_windows() -> List[Dict]:
    """发现当前系统中所有可用的微信主窗口候选"""
    visible_results = []
    hidden_results = []

    def enum_callback(hwnd, _):
        try:
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            
            is_wechat4 = (cls.endswith("WeChatMainWndForPC") or cls.endswith("WeChatLoginWndForPC")) and (title.strip() != "")
            is_wechat3 = cls.endswith(WxClass.WIN32_CLASS) and is_wechat_title(title)
            
            if is_wechat4 or is_wechat3:
                r = win32gui.GetWindowRect(hwnd)
                w = r[2] - r[0]
                h = r[3] - r[1]
                is_visible = win32gui.IsWindowVisible(hwnd)
                if w >= 250 and h >= 250:
                    import win32process
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    info = {"hwnd": hwnd, "width": w, "height": h, "pid": pid,
                            "title": title or "(微信4.x)", "cls": cls}
                    if is_visible:
                        visible_results.append(info)
                    else:
                        hidden_results.append({**info, "_original_visible": False})
        except Exception:
            pass

    win32gui.EnumWindows(enum_callback, None)

    total = len(visible_results) + len(hidden_results)
    if total > 0:
        logger.debug(f"[多开] 窗口扫描: 可见={len(visible_results)}, "
                     f"隐藏={len(hidden_results)}, 总计={total}")
        for w in visible_results:
            logger.debug(f"[多开]   [OK] 可见 hwnd={w['hwnd']} {w['width']}x{w['height']} pid={w.get('pid')}")
        for w in hidden_results:
            logger.debug(f"[多开]   [Hidden] 隐藏 hwnd={w['hwnd']} {w['width']}x{w['height']} pid={w.get('pid')}")

    results = list(visible_results) + list(hidden_results)
    results.sort(key=lambda x: x["width"] * x["height"], reverse=True)

    seen_pids = set()
    unique_results = []
    for r in results:
        pid = r.get("pid")
        if pid not in seen_pids:
            seen_pids.add(pid)
            unique_results.append(r)
    return unique_results

def find_wechat_window() -> Optional[int]:
    """遍历所有窗口查找微信主窗口"""
    visible_results = []
    all_results = []

    def enum_callback(hwnd, _):
        try:
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            is_wechat4 = cls.endswith("WeChatMainWndForPC")
            is_wechat3 = cls.endswith(WxClass.WIN32_CLASS) and is_wechat_title(title)
            if is_wechat4 or is_wechat3:
                r = win32gui.GetWindowRect(hwnd)
                w = r[2] - r[0]
                h = r[3] - r[1]
                area = w * h
                is_visible = win32gui.IsWindowVisible(hwnd)
                all_results.append((hwnd, area, w, h, is_visible))
                if is_visible:
                    visible_results.append((hwnd, area, w, h))
        except Exception:
            pass

    win32gui.EnumWindows(enum_callback, None)

    if not all_results:
        return None

    if visible_results:
        visible_results.sort(key=lambda x: x[1], reverse=True)
        for hwnd, area, w, h in visible_results:
            if w >= 500 and h >= 400:
                print(f"[UIA] 选择微信主窗口: hwnd={hwnd} 尺寸={w}x{h}")
                return hwnd

    all_results.sort(key=lambda x: x[1], reverse=True)
    for hwnd, area, w, h, is_visible in all_results:
        if w >= 500 and h >= 400:
            if not is_visible:
                print(f"[UIA] 微信主窗口在托盘中，主动唤回: hwnd={hwnd} 尺寸={w}x{h}")
                try:
                    from src.uia.retry.window_ops import ensure_wechat_foreground
                    ensure_wechat_foreground(hwnd)
                except Exception as e:
                    print(f"[UIA] 唤回微信窗口异常: {e}")
            else:
                print(f"[UIA] 选择微信主窗口: hwnd={hwnd} 尺寸={w}x{h}")
            return hwnd

    hwnd = all_results[0][0]
    print(f"[UIA] 未找到大窗口，使用: hwnd={hwnd} 尺寸={all_results[0][2]}x{all_results[0][3]}")
    return hwnd

def try_restore_from_cache(driver_obj) -> bool:
    """从本地缓存恢复账号信息"""
    if driver_obj._nickname and driver_obj._wxid:
        return True

    try:
        import win32process
        import psutil
        from src.utils.wechat_key_store import clean_wxid
        _, pid = win32process.GetWindowThreadProcessId(driver_obj.hwnd)
        proc = psutil.Process(pid)
        for f in proc.open_files():
            path = f.path
            match = re.search(r"[\\/](?:WeChat Files|WeXin Files|xwechat_files|WeChatFiles)[\\/]([^\\/]+)", path, re.IGNORECASE)
            if match:
                inferred_wxid = match.group(1)
                if inferred_wxid and inferred_wxid.lower() not in {"all users", "all_users", "backup", "finderlive", "common", "global"}:
                    driver_obj._wxid = clean_wxid(inferred_wxid)
                    logger.info(f"[无感推断] 成功从微信进程句柄静默匹配到 wxid: {driver_obj._wxid}")
                    break
        if driver_obj._wxid:
            try:
                from src.crm.account_data import _load_account_meta
                meta = _load_account_meta(driver_obj._wxid)
                if meta and meta.get("nickname") and meta.get("nickname") != driver_obj._wxid:
                    driver_obj._nickname = meta["nickname"]
                    logger.info(f"[无感推断] 成功通过本地元数据匹配到昵称: {driver_obj._nickname}")
            except Exception as e_meta:
                logger.debug(f"[无感推断] 读取本地元数据异常: {e_meta}")

            if not driver_obj._nickname:
                try:
                    from src.utils.wechat_key_store import get_persisted_wechat_key
                    hex_key = get_persisted_wechat_key(driver_obj._wxid)
                    if hex_key:
                        from src.wechat_4x.db_match_helper import get_wechat_base_dirs
                        base_dirs = get_wechat_base_dirs()
                        db_storage = ""
                        for base_dir in base_dirs:
                            candidate_storage = os.path.join(base_dir, driver_obj._wxid, "db_storage")
                            if os.path.isdir(candidate_storage):
                                db_storage = candidate_storage
                                break
                        
                        if db_storage:
                            contact_db = ""
                            for sub in ["contact", "Contact"]:
                                candidate_db = os.path.join(db_storage, sub, "contact.db")
                                if os.path.exists(candidate_db):
                                    contact_db = candidate_db
                                    break
                            
                            if contact_db:
                                from src.utils.wechat_decrypt import WeChatDatabaseDecryptor
                                decryptor = WeChatDatabaseDecryptor(hex_key)
                                with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xm_bot_self_") as tmp:
                                    tmp_path = tmp.name
                                try:
                                    if decryptor.decrypt_database(contact_db, tmp_path):
                                        conn = sqlite3.connect(tmp_path)
                                        conn.row_factory = sqlite3.Row
                                        cursor = conn.cursor()
                                        
                                        cursor.execute("PRAGMA table_info(contact)")
                                        col_names = {r["name"].lower(): r["name"] for r in cursor.fetchall()}
                                        usr_col = col_names.get("username")
                                        nick_col = col_names.get("nick_name") or col_names.get("nickname")
                                        alias_col = col_names.get("alias")
                                        
                                        if usr_col and nick_col:
                                            q_parts = [usr_col, nick_col]
                                            if alias_col:
                                                q_parts.append(alias_col)
                                            cursor.execute(
                                                f"SELECT {', '.join(q_parts)} FROM contact WHERE {usr_col}=? OR {usr_col} LIKE 'wxid_%'",
                                                (driver_obj._wxid,)
                                            )
                                            rows = cursor.fetchall()
                                            target_row = None
                                            for r in rows:
                                                if r[usr_col] == driver_obj._wxid or (alias_col and r[alias_col] == driver_obj._wxid):
                                                    target_row = r
                                                    break
                                            if not target_row and rows:
                                                target_row = rows[0]
                                                
                                            if target_row and target_row[nick_col]:
                                                driver_obj._nickname = target_row[nick_col]
                                                logger.info(f"[无感推断] 🎉 成功通过 contact.db 静默解密到 bot 昵称: {driver_obj._nickname}")
                                                
                                                try:
                                                    from src.crm.account_data import _save_account_meta
                                                    _save_account_meta(driver_obj._wxid, {
                                                        "wxid": driver_obj._wxid,
                                                        "nickname": driver_obj._nickname,
                                                        "alias": target_row[alias_col] if (alias_col and target_row[alias_col]) else ""
                                                    })
                                                except Exception as e_save:
                                                    logger.debug(f"[无感推断] 自动保存元数据失败: {e_save}")
                                        conn.close()
                                except Exception as db_err:
                                    logger.debug(f"[无感推断] 静默解析 contact.db 获取昵称失败: {db_err}")
                                finally:
                                    if os.path.exists(tmp_path):
                                        try:
                                            os.unlink(tmp_path)
                                        except Exception:
                                            pass
                except Exception as key_err:
                    logger.debug(f"[无感推断] 尝试静默解密获取昵称链路异常: {key_err}")
    except Exception as _infer_err:
        logger.debug(f"[无感推断] 进程句柄推算异常 (将自动降级到传统流程): {_infer_err}")

    try:
        from src.utils.instance_snapshot import WeChatInstanceSnapshotStore
        snapshot_path = WeChatInstanceSnapshotStore.get_snapshot_path()
        if os.path.exists(snapshot_path):
            with open(snapshot_path, "r", encoding="utf-8") as _f:
                snapshot: dict = json.load(_f)
            for _inst_id, _info in snapshot.items():
                if _info.get("window_handle") == driver_obj.hwnd:
                    _wxid = _info.get("wxid", "").strip()
                    _nick = _info.get("nickname", "").strip()
                    if _wxid and _wxid.lower() not in {"all users", "all_users"} and _nick:
                        driver_obj._wxid = driver_obj._wxid or clean_wxid(_wxid)
                        driver_obj._nickname = driver_obj._nickname or _nick
                        logger.debug(f"[缓存恢复] hwnd={driver_obj.hwnd} 命中快照: nickname={driver_obj._nickname!r} wxid={driver_obj._wxid!r}")
                        break
    except Exception as _snap_err:
        logger.debug(f"[缓存恢复] 快照读取异常: {_snap_err}")

    if driver_obj._wxid and not driver_obj._nickname:
        driver_obj._nickname = driver_obj._wxid
        logger.info(f"[无感推断] 使用 wxid {driver_obj._wxid} 兜底作为昵称，成功避免物理点击")

    if driver_obj._wxid and driver_obj._nickname:
        return True

    logger.warning(f"[缓存恢复] 账号信息不完整(nickname={driver_obj._nickname!r}, wxid={driver_obj._wxid!r})，将走降级流程")
    return False

def init_privacy_shield_with_local_avatar(driver_obj, hwnd: int):
    """初始化隐私遮罩"""
    try:
        cls_name = win32gui.GetClassName(hwnd)
        if "LoginWnd" in cls_name or "Qt51514QWindowIcon" in cls_name:
            return
    except Exception:
        pass

    try:
        from src.uia.privacy_shield import get_privacy_shield
        from src.crm.account_data import get_config_path, ACCOUNTS_DIR
        shield = get_privacy_shield()
        avatar_path, nickname, wxid = "", driver_obj._nickname or "", driver_obj._wxid or ""
        if wxid:
            cached = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
            if os.path.exists(cached):
                avatar_path = cached
        shield.update_wechat_hwnd(hwnd)
        if avatar_path or nickname:
            shield.update_user_info(nickname, avatar_path)
        if wxid:
            shield.set_config_path(get_config_path(wxid))
        if not shield.enabled:
            shield.auto_start(hwnd, get_config_path(wxid) if wxid else "", nickname, avatar_path)
    except Exception as e:
        print(f"[隐私遮罩] 初始化失败: {e}")
