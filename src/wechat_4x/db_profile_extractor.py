import os
import sqlite3
import tempfile
import win32process
from typing import Optional, Tuple
from src.wechat_4x.wcdb_key_extractor import get_wcdb_key_extractor
from src.wechat_4x.db_match_helper import auto_detect_db_path
from src.utils.wechat_decrypt import WeChatDatabaseDecryptor

# 记录已打印过的路径/数据库验证失败记录，防止高频刷屏撑爆日志文件
_logged_mismatches = set()

def extract_profile_from_db(hwnd: int, expected_wxid: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    通过解密本地 contact.db 静默提取指定微信窗口对应的 wxid 和 nickname。
    0 物理点击，免 UIA 干扰，微信隐蔽接管。
    
    Returns:
        Optional[Tuple[wxid, nickname]]
    """
    try:
        import win32gui
        # 【优化】如果窗口句柄已被销毁或无效，直接静默退出，防止抛出 GetClassName 异常狂刷日志
        if not win32gui.IsWindow(hwnd):
            return None
        try:
            cls_name = win32gui.GetClassName(hwnd)
        except Exception as e:
            if hasattr(e, 'args') and len(e.args) >= 3 and e.args[0] == 1400:
                return None
            raise
        if "LoginWnd" in cls_name:
            # 【优化】扫码登录窗口，直接返回 None，不执行任何磁盘解密以抑制持续日志刷屏
            return None
        
        hex_key = None
        if expected_wxid:
            try:
                from src.api.instance_settings_api import load_instance_settings
                cfg = load_instance_settings(expected_wxid)
                manual_key = cfg.get("wechat_hex_key", "").strip()
                if manual_key and len(manual_key) == 64:
                    hex_key = manual_key
            except Exception:
                pass

        if not hex_key:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return None

            # ⚠️ 【关键防护】检查 PID 是否被 auto_get_key 独占，或微信是否处于登录态：
            # 独占时：auto_get_key 正在运行，wcdb_key_extractor 进来只会干扰 Hook 并在 2s 后把 Hook 卸掉
            # 登录态时：数据库尚未打开，sqlite3_key 还没被调用，Hook 必然超时
            # 两种情况下：只从缓存取，不做任何 DLL 注入操作
            _skip_dll = False
            try:
                from src.wechat_4x.wechat_key_monitor import _is_exclusive
                if _is_exclusive(pid):
                    _skip_dll = True
            except Exception:
                pass

            if not _skip_dll:
                # 检查该 PID 的窗口是否为登录窗口（WeChatLoginWndForPC），若是则跳过 DLL
                try:
                    import win32gui as _w32g_pf
                    def _chk_login_wnd(_hwnd, _):
                        try:
                            _, _p = win32process.GetWindowThreadProcessId(_hwnd)
                            if _p == pid:
                                _cn = _w32g_pf.GetClassName(_hwnd)
                                if "LoginWnd" in _cn:
                                    _skip_dll_ref[0] = True
                        except Exception:
                            pass
                    _skip_dll_ref = [False]
                    _w32g_pf.EnumWindows(_chk_login_wnd, None)
                    _skip_dll = _skip_dll_ref[0]
                except Exception:
                    pass

            if _skip_dll:
                # 只从 KeyStore 取缓存密钥，不做任何 DLL 注入
                try:
                    from src.wechat_4x.wcdb_key_helpers import _find_wxid_by_pid
                    from src.utils.wechat_key_store import get_persisted_wechat_key
                    _wxid_hint = expected_wxid or _find_wxid_by_pid(pid)
                    if _wxid_hint:
                        hex_key = get_persisted_wechat_key(_wxid_hint) or None
                except Exception:
                    pass
            else:
                # 尝试静默提取该进程对应的密钥，最长等待 2.0s（有缓存则瞬间返回）
                hex_key = get_wcdb_key_extractor().get_key(timeout_s=2.0, pid=pid)

        if not hex_key:
            return None
            
        db_path = auto_detect_db_path(hex_key, expected_wxid)
        if not db_path or not os.path.exists(db_path):
            return None
            
        # 通过 db_storage 父目录直接解出 WXID 并清洗后缀
        db_storage = os.path.dirname(os.path.dirname(db_path))
        account_dir = os.path.dirname(db_storage)
        raw_wxid = os.path.basename(account_dir)
        from src.utils.wechat_key_store import clean_wxid
        wxid = clean_wxid(raw_wxid)
        
        # 定位 contact.db 路径
        contact_db_path = ""
        for sub in ["contact", "Contact"]:
            candidate = os.path.join(db_storage, sub, "contact.db")
            if os.path.exists(candidate):
                contact_db_path = candidate
                break
                
        if not contact_db_path or not wxid:
            return None
            
        # 安全校验：在解密前如果发现通过路径推算出的 wxid 与预期的不一致，直接终止，防范 any 错绑别人残留文件夹的可能性
        if expected_wxid:
            ew_lower = expected_wxid.lower()
            is_temp_id = (
                ew_lower.startswith("wx_") or
                "instance" in ew_lower or
                len(expected_wxid) > 20 or
                any(c in expected_wxid for c in ("微信", "分身", "多开", "隔离"))
            )
            if not is_temp_id:
                if clean_wxid(wxid) != clean_wxid(expected_wxid):
                    key = (expected_wxid, wxid, "path")
                    if key not in _logged_mismatches:
                        _logged_mismatches.add(key)
                        print(f"[DB解密] 路径验证失败：期望微信号 {expected_wxid}，但推断路径微信号为 {wxid}，已安全拦截。")
                    return None

        decryptor = WeChatDatabaseDecryptor(hex_key)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xm_init_contact_") as tmp:
            tmp_path = tmp.name
            
        try:
            if decryptor.decrypt_database(contact_db_path, tmp_path):
                conn = sqlite3.connect(tmp_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {r[0].lower() for r in cursor.fetchall()}
                
                nickname = ""
                if "contact" in tables:
                    cursor.execute("PRAGMA table_info(contact)")
                    col_names = {r[1].lower(): r[1] for r in cursor.fetchall()}
                    db_usr_col = col_names.get("username")
                    db_nick_col = col_names.get("nick_name") or col_names.get("nickname")
                    db_ali_col = col_names.get("alias")
                    
                    if db_usr_col and db_nick_col:
                        # 多重微信号清洗：确保把一切多开容器添加的随机后缀完全剥离，对齐数据库原始记录
                        base_wxid = wxid
                        if "_" in wxid:
                            parts = wxid.split("_")
                            if len(parts) >= 3 and parts[0] == "wxid":
                                base_wxid = f"{parts[0]}_{parts[1]}"
                        
                        if db_ali_col:
                            cursor.execute(
                                f"SELECT {db_nick_col} FROM contact WHERE {db_usr_col} IN (?, ?) OR {db_ali_col} IN (?, ?)",
                                (wxid, base_wxid, wxid, base_wxid)
                            )
                        else:
                            cursor.execute(
                                f"SELECT {db_nick_col} FROM contact WHERE {db_usr_col} IN (?, ?)",
                                (wxid, base_wxid)
                            )
                        row = cursor.fetchone()
                        if row:
                            nickname = row[0] or ""
                conn.close()
                
                # 如果从数据库查询到的 nickname 为空，尝试从本地的 account_meta.json 恢复作为兜底
                if not nickname and wxid:
                    try:
                        from src.crm.account_data import _load_account_meta
                        meta = _load_account_meta(wxid)
                        if meta and meta.get("nickname") and meta.get("nickname") != wxid:
                            nickname = meta["nickname"]
                            print(f"[DB解密-本地兜底] 成功从本地元数据读取到历史昵称: {nickname}")
                    except Exception as e_meta:
                        print(f"[DB解密-本地兜底] 从本地元数据读取昵称异常: {e_meta}")
                
                if wxid and nickname:
                    # 提取并保存自己的头像到 ACCOUNTS_DIR
                    try:
                        head_image_db_path = ""
                        candidates_head = [
                            os.path.join(db_storage, "head_image", "head_image.db"),
                            os.path.join(db_storage, "head_image.db"),
                        ]
                        for candidate in candidates_head:
                            if os.path.exists(candidate):
                                head_image_db_path = candidate
                                break
                        
                        if not head_image_db_path:
                            parent_dir = os.path.dirname(db_storage)
                            candidate_sibling = os.path.join(parent_dir, "head_image", "head_image.db")
                            if os.path.exists(candidate_sibling):
                                head_image_db_path = candidate_sibling

                        if head_image_db_path:
                            from src.crm.account_data import ACCOUNTS_DIR
                            avatar_path = os.path.join(ACCOUNTS_DIR, f"{wxid}.png")
                            # 如果头像文件还不存在，则从数据库中解密获取
                            if not os.path.exists(avatar_path):
                                with tempfile.NamedTemporaryFile(suffix=".db", delete=False, prefix="xm_init_head_") as tmp_head:
                                    tmp_head_path = tmp_head.name
                                try:
                                    if decryptor.decrypt_database(head_image_db_path, tmp_head_path):
                                        conn_head = sqlite3.connect(tmp_head_path)
                                        cursor_head = conn_head.cursor()
                                        cursor_head.execute("SELECT name FROM sqlite_master WHERE type='table'")
                                        tables_head = {r[0].lower() for r in cursor_head.fetchall()}
                                        
                                        if "head_image" in tables_head:
                                            cursor_head.execute(
                                                "SELECT image_buffer FROM head_image WHERE username=?",
                                                (wxid,)
                                            )
                                            row_head = cursor_head.fetchone()
                                            if row_head and row_head[0]:
                                                image_buffer = row_head[0]
                                                img_bytes = None
                                                if isinstance(image_buffer, memoryview):
                                                    img_bytes = image_buffer.tobytes()
                                                elif isinstance(image_buffer, str):
                                                    if image_buffer.lower().startswith("ffd8") or image_buffer.lower().startswith("89504e47"):
                                                        img_bytes = bytes.fromhex(image_buffer)
                                                    else:
                                                        try:
                                                            import base64
                                                            img_bytes = base64.b64decode(image_buffer)
                                                        except:
                                                            img_bytes = image_buffer.encode('utf-8')
                                                else:
                                                    img_bytes = image_buffer
                                                    
                                                if img_bytes:
                                                    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
                                                    with open(avatar_path, "wb") as f_head:
                                                        f_head.write(img_bytes)
                                                    print(f"[多开-双引擎] 📸 成功从数据库解密并保存高清头像: {avatar_path}")
                                        conn_head.close()
                                finally:
                                    if os.path.exists(tmp_head_path):
                                        try: os.unlink(tmp_head_path)
                                        except: pass
                    except Exception as e_avatar:
                        print(f"[多开-双引擎] ⚠️ 提取个人头像异常: {e_avatar}")

                    # 安全双重校验：确保数据库表内记录的真实微信号与预期严格一致，防止串号
                    if expected_wxid:
                        ew_lower = expected_wxid.lower()
                        is_temp_id = (
                            ew_lower.startswith("wx_") or
                            "instance" in ew_lower or
                            len(expected_wxid) > 20 or
                            any(c in expected_wxid for c in ("微信", "分身", "多开", "隔离"))
                        )
                        if not is_temp_id:
                            if clean_wxid(wxid) != clean_wxid(expected_wxid):
                                key = (expected_wxid, wxid, "record")
                                if key not in _logged_mismatches:
                                    _logged_mismatches.add(key)
                                    print(f"[DB解密] 数据库内部记录验证失败：期望微信号 {expected_wxid}，但数据库记录微信号为 {wxid}，已安全拦截。")
                                return None

                    return wxid, nickname
            else:
                last_res = decryptor.last_result or {}
                err_msg = last_res.get("error") or last_res.get("diagnostic_status") or "未知原因"
                print(f"[DB解密] contact.db 解密失败: {err_msg} (密钥: {hex_key[:6]}******{hex_key[-6:]})")
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except:
                    pass
    except Exception as e:
        if hasattr(e, 'args') and len(e.args) >= 3 and e.args[0] == 1400:
            return None
        print(f"[WCDB协调器] 提取 profile 异常: {e}")
    return None
