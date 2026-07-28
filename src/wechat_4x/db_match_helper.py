"""
db_match_helper.py
微信 db_storage 目录密钥 HMAC 校验与自动探测辅助模块
"""
import os
import logging
from typing import Optional

logger = logging.getLogger("DbMatchHelper")


def find_session_db(db_storage: str) -> Optional[str]:
    """在 db_storage 目录下查找 session.db"""
    for sub in ["Session", "session"]:
        p = os.path.join(db_storage, sub, "session.db")
        if os.path.exists(p):
            return p
    p2 = os.path.join(db_storage, "session.db")
    if os.path.exists(p2):
        return p2
    return None


def match_db_storage_by_key(hex_key: str, db_storage_dirs: list) -> Optional[str]:
    """用密钥 HMAC 匹配真正对应的 db_storage 目录"""
    try:
        import hashlib
        import hmac as _hmac

        key_bytes = bytes.fromhex(hex_key)
        PAGE_SIZE = 4096
        SALT_SIZE = 16
        KEY_SIZE = 32
        HMAC_SIZE = 64
        RESERVE_SIZE = 16 + HMAC_SIZE

        def _derive_mac_key(enc_key, salt):
            mac_salt = bytes(b ^ 0x3A for b in salt)
            return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SIZE)

        def _derive_enc_key(km, salt):
            return hashlib.pbkdf2_hmac("sha512", km, salt, 256000, dklen=KEY_SIZE)

        def _compute_hmac(mac_key, page):
            data_end = PAGE_SIZE - RESERVE_SIZE + 16
            m = _hmac.new(mac_key, digestmod=hashlib.sha512)
            m.update(page[SALT_SIZE:data_end])
            m.update((1).to_bytes(4, "little"))
            return m.digest()

        for db_storage in db_storage_dirs:
            for sub in ["contact", "Contact"]:
                contact_db = os.path.join(db_storage, sub, "contact.db")
                if not os.path.exists(contact_db):
                    continue
                try:
                    with open(contact_db, "rb") as f:
                        page1 = f.read(PAGE_SIZE)
                    if len(page1) < PAGE_SIZE:
                        continue
                    salt = page1[:SALT_SIZE]
                    stored = page1[PAGE_SIZE - HMAC_SIZE :]
                    mk = _derive_mac_key(key_bytes, salt)
                    if _hmac.compare_digest(stored, _compute_hmac(mk, page1)):
                        return db_storage
                    dk = _derive_enc_key(key_bytes, salt)
                    mk2 = _derive_mac_key(dk, salt)
                    if _hmac.compare_digest(stored, _compute_hmac(mk2, page1)):
                        return db_storage
                except Exception:
                    continue
    except Exception as e:
        logger.debug("[DbMatchHelper] 密钥匹配异常: %s", e)
    return None


_db_path_cache = {}


_cached_base_dirs = None


def get_wechat_base_dirs() -> list:
    """
    获取所有可能的微信数据根目录。
    支持从 Windows 注册表读取用户自定义的文件保存路径。
    使用内存级缓存，避免高频 I/O 与 psutil 进程扫描拖慢系统响应。
    """
    global _cached_base_dirs
    if _cached_base_dirs is not None:
        return _cached_base_dirs.copy()

    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    base_dirs = []

    # 0. 优先通过运行中的微信进程正打开的文件句柄，动态发现数据目录（完美解决 UNC 共享、软链接、挂载等特殊路径问题）
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info.get('name') or ''
                if name.lower() in ('wechat.exe', 'weixin.exe'):
                    for f in proc.open_files():
                        p_path = f.path
                        if p_path:
                            p_path_lower = p_path.lower()
                            if p_path_lower.endswith("contact.db") and "db_storage" in p_path_lower:
                                base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(p_path))))
                                if os.path.isdir(base) and base not in base_dirs:
                                    base_dirs.append(base)
                            elif p_path_lower.endswith("session.db") and "db_storage" in p_path_lower:
                                base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(p_path))))
                                if os.path.isdir(base) and base not in base_dirs:
                                    base_dirs.append(base)
            except Exception:
                pass
    except Exception:
        pass

    # 1. 尝试从 Windows 注册表获取微信文件保存路径
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat", 0, winreg.KEY_READ)
        try:
            val, _ = winreg.QueryValueEx(key, "FileSavePath")
            if val:
                val = str(val).strip()
                # 如果是 MyDocument: 则表示保存在我的文档中，交由后面的默认逻辑处理
                if val.lower() != "mydocument:":
                    # 支持绝对路径
                    if os.path.isdir(val):
                        base_dirs.append(val)
                    # 微信在注册表里也可能存为 "D:\xwechat_files" 这样的格式，我们需要确保没有尾部反斜杠或其它空格
                    # 我们还要探测该目录下是否存在 "xwechat_files" 子目录，或者它直接就是根目录
                    candidate_sub = os.path.join(val, "xwechat_files")
                    if os.path.isdir(candidate_sub) and candidate_sub not in base_dirs:
                        base_dirs.append(candidate_sub)
                    candidate_sub2 = os.path.join(val, "WeChat Files")
                    if os.path.isdir(candidate_sub2) and candidate_sub2 not in base_dirs:
                        base_dirs.append(candidate_sub2)
        except Exception:
            pass
        finally:
            try: key.Close()
            except: pass
    except Exception:
        pass

    # 2. 默认的几个路径作为兜底
    defaults = [
        os.path.join(user_profile, "xwechat_files"),
        os.path.join(user_profile, "Documents", "xwechat_files"),
        os.path.join(user_profile, "Documents", "WeChat Files"),
    ]
    for d in defaults:
        if os.path.isdir(d) and d not in base_dirs:
            base_dirs.append(d)

    # 3. 扫描各盘符下的根目录，以支持用户自定义在D盘、E盘等其他盘符根目录存放的数据
    try:
        import ctypes
        buf = ctypes.create_string_buffer(1024)
        ctypes.windll.kernel32.GetLogicalDriveStringsA(1024, buf)
        drives = [d.decode('utf-8').strip() for d in buf.value.split(b'\x00') if d]
    except Exception:
        drives = [f"{c}:\\" for c in "CDEFGHIJKLMNOP"]

    for drive in drives:
        if not drive:
            continue
        
        # 🌟 强力自愈：调用 GetDriveTypeW 限制只扫描本地固定硬盘(3)和可移动磁盘(2)，
        # 绝对不扫描网络共享映射盘(4)和光盘驱动器(5)，防止由于不可达盘符导致 os.path.isdir 严重阻塞。
        try:
            import ctypes
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive)
            if drive_type not in (2, 3):  # 2=REMOVABLE, 3=FIXED
                continue
        except Exception:
            pass

        for sub in ["xwechat_files", "WeChat Files"]:
            d_path = os.path.join(drive, sub)
            try:
                if os.path.isdir(d_path) and d_path not in base_dirs:
                    base_dirs.append(d_path)
            except Exception:
                pass

    _cached_base_dirs = base_dirs
    return _cached_base_dirs.copy()


_db_path_cache = {}


def auto_detect_db_path(hex_key: str = "", expected_wxid: Optional[str] = None) -> Optional[str]:
    r"""
    自动探测微信 NT 数据库路径。
    """
    cache_key = f"{hex_key or 'default_no_key'}_{expected_wxid or 'any'}"
    if cache_key in _db_path_cache:
        cached_path = _db_path_cache[cache_key]
        if not cached_path or os.path.exists(cached_path):
            return cached_path

    # 🌟 强力插入：检查该 expected_wxid 是否有用户手动配置的独立密钥和数据目录，若有则优先使用 🌟
    if expected_wxid:
        try:
            from src.api.instance_settings_api import load_instance_settings
            cfg = load_instance_settings(expected_wxid)
            manual_key = cfg.get("wechat_hex_key", "").strip()
            if manual_key and len(manual_key) == 64:
                hex_key = manual_key
                logger.info(f"[DbMatchHelper] 使用用户手动配置的微信解密密钥: {hex_key[:6]}...{hex_key[-6:]}")
                
            manual_dir = cfg.get("wechat_data_dir", "").strip()
            if manual_dir and os.path.exists(manual_dir):
                db_storage_cand = None
                if os.path.basename(manual_dir).lower() == "db_storage":
                    db_storage_cand = manual_dir
                else:
                    cand = os.path.join(manual_dir, "db_storage")
                    if os.path.isdir(cand):
                        db_storage_cand = cand
                    else:
                        db_storage_cand = manual_dir
                
                if db_storage_cand:
                    session_db = find_session_db(db_storage_cand)
                    if session_db:
                        logger.info(f"[DbMatchHelper] 成功根据手动配置的数据目录定位到数据库: {session_db}")
                        _db_path_cache[cache_key] = session_db
                        return session_db
        except Exception as e_cfg:
            logger.debug(f"[DbMatchHelper] 获取手动配置失败: {e_cfg}")

    # 收集所有可能的 db_storage 目录
    from src.utils.wechat_key_store import clean_wxid
    target_wxid_clean = clean_wxid(expected_wxid) if expected_wxid else None

    # 判断 target_wxid_clean 是否为 4.x 的 32 位 MD5 哈希形式
    is_hash_id = target_wxid_clean and len(target_wxid_clean) == 32 and all(c in "0123456789abcdef" for c in target_wxid_clean.lower())

    all_db_storage_dirs = []
    base_dirs = get_wechat_base_dirs()
    for base_dir in base_dirs:
        if not os.path.isdir(base_dir):
            continue
        try:
            for entry in os.listdir(base_dir):
                if entry.lower() in {"all users", "all_users", "backup", "finderlive", "common", "global", "temp", "cache"}:
                    continue
                # 如果指定了期望的 wxid，则强制只保留该账号的数据目录，杜绝多开状态下任何跨账号目录串号解密或错误覆盖
                if target_wxid_clean:
                    if clean_wxid(entry) != target_wxid_clean:
                        continue
                db_storage = os.path.join(base_dir, entry, "db_storage")
                if os.path.isdir(db_storage):
                    all_db_storage_dirs.append(db_storage)
        except Exception:
            pass

    # 优先用密钥 HMAC 匹配正确的账号目录
    if hex_key and len(hex_key) == 64:
        matched = match_db_storage_by_key(hex_key, all_db_storage_dirs)
        if matched:
            session_db = find_session_db(matched)
            if session_db:
                # 只有在期望 wxid 为 32 位哈希时，才执行路径强验证
                if target_wxid_clean and is_hash_id and target_wxid_clean not in clean_wxid(session_db):
                    logger.warning("[DbMatchHelper] ⚠️ HMAC 匹配的路径与期望的 wxid 不符，放弃匹配: %s", session_db)
                else:
                    logger.info("[DbMatchHelper] 自动探测到 session.db 路径 (密钥匹配): %s", session_db)
                    _db_path_cache[cache_key] = session_db
                    return session_db

    # 密钥匹配失败时，按字典序兜底（若已指定 expected_wxid，即便兜底也只会在期望的 wxid 目录内找，确保安全）
    for db_storage in sorted(all_db_storage_dirs):
        session_db = find_session_db(db_storage)
        if session_db:
            logger.info("[DbMatchHelper] 自动探测到 session.db 路径: %s", session_db)
            _db_path_cache[cache_key] = session_db
            return session_db

    _db_path_cache[cache_key] = None
    return None
