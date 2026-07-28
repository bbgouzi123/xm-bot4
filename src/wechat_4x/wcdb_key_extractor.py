"""
wcdb_key_extractor.py
WCDB 数据库密钥提取器

使用 wx_key.dll 通过注入方式从微信进程内存中提取 WCDB 加密数据库的 hex key。
流程:
  1. 找到当前运行的微信进程 PID
  2. 通过 wx_key.dll 注入 Hook 并轮询拿到 64 字符 hex key
  3. 缓存密钥，避免每次重新提取

此模块为纯提取模块，不涉及数据库读取。
"""
import ctypes
import os
import sys
import time
import logging
import json
from typing import Optional

from .wcdb_key_helpers import (
    _get_dynamic_token,
    _get_dll_path,
    _find_wechat_pid,
    _find_wxid_by_pid,
    _validate_key_for_pid
)

logger = logging.getLogger(__name__)


class WcdbKeyExtractor:
    """
    使用 wx_key.dll 从运行中的微信进程提取数据库 hex 密钥。
    密钥提取成功后自动缓存，重启微信或密钥失效时调用 refresh() 重新获取。
    """

    def __init__(self):
        self._dll: Optional[ctypes.CDLL] = None
        self._initialized = False
        self._cached_key: Optional[str] = None
        self._cached_for_pid: Optional[int] = None
        self._failed_pids = {}  # pid -> last_fail_time

        # DLL 函数引用
        self._init_hook = None
        self._poll_key = None
        self._get_status = None
        self._cleanup_hook = None
        self._get_last_error = None

    def _load_dll(self) -> bool:
        if self._initialized:
            return True
        dll_path = _get_dll_path("sqlite3_secure.dll")
        if not dll_path or not os.path.exists(dll_path):
            logger.error(f"[WCDB密钥] sqlite3_secure.dll 未找到: {dll_path}")
            return False
        try:
            self._dll = ctypes.CDLL(dll_path)

            self._init_hook = self._dll._x_init_session
            self._init_hook.argtypes = [
                ctypes.c_uint32, 
                ctypes.c_char_p, 
                ctypes.c_char_p, 
                ctypes.c_char_p, 
                ctypes.c_int
            ]
            self._init_hook.restype = ctypes.c_bool

            self._poll_key = self._dll._x_poll_session
            self._poll_key.argtypes = [ctypes.c_char_p, ctypes.c_int]
            self._poll_key.restype = ctypes.c_bool

            self._get_status = self._dll._x_status_session
            self._get_status.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            self._get_status.restype = ctypes.c_bool

            self._cleanup_hook = self._dll._x_term_session
            self._cleanup_hook.restype = ctypes.c_bool

            self._get_last_error = self._dll._x_err_session
            self._get_last_error.restype = ctypes.c_char_p

            self._initialized = True
            logger.info(f"[WCDB密钥] sqlite3_secure.dll 已加载: {dll_path}")
            return True
        except Exception as e:
            logger.error(f"[WCDB密钥] 加载 sqlite3_secure.dll 失败: {e}")
            return False

    def get_key(self, timeout_s: float = 30.0, force_refresh: bool = False, pid: Optional[int] = None) -> Optional[str]:
        """
        获取当前微信进程的 WCDB hex 密钥 (64字符)。
        - 优先从本地持久化密钥库或环境变量恢复，免除注入 DLL Hook 过程。
        - 如已缓存且 PID 未变，直接返回缓存值。
        - force_refresh=True 时强制重新注入提取。
        - 返回 None 表示提取失败。
        """
        if pid is None:
            pid = _find_wechat_pid()
        if not pid:
            logger.warning(f"[WCDB密钥] 微信进程不存在 (pid={pid})，无法提取密钥")
            return None

        # 冷却拦截：如果在规定时间内曾注入提取失败过，则跳过本次 Hook 注入尝试，防止狂刷超时日志并杜绝进程崩溃
        now = time.time()
        last_fail = self._failed_pids.get(pid, 0)
        
        is_fresh_needed = True
        wxid_hint_for_cooldown = None
        try:
            from src.wechat_4x.wcdb_key_helpers import _find_wxid_by_pid
            wxid_hint_for_cooldown = _find_wxid_by_pid(pid)
            if wxid_hint_for_cooldown:
                # 🌟 [冷却防护升级] 
                # 只要微信进程能够反查出 WXID，就说明其已经完成了登录并且早已经过了数据库首次打开解密的黄金期。
                # 此时，在任何情况下均绝对没有可能再次通过 Hook 拦截获取到密钥。
                # 我们设为 False，并将冷却期延长到 300 秒（5分钟），彻底封死 5 秒轮询穿透，杜绝无用超时刷屏！
                is_fresh_needed = False
        except Exception:
            pass
            
        # 已登录微信：300 秒冷却（明确不可能再截获，5 分钟内不重试）
        # 未登录/刚启动：5 秒冷却（可能还在登录流程中，稍后可再次尝试）
        cooldown_limit = 5.0 if is_fresh_needed else 300.0
        if not force_refresh and now - last_fail < cooldown_limit:
            logger.debug(f"[WCDB密钥] PID={pid} 在 {cooldown_limit}s 内曾注入提取失败，冷却中，跳过本次 Hook 注入")
            return None

        # 缓存命中：同一微信进程 PID，直接返回
        if not force_refresh and self._cached_key and self._cached_for_pid == pid:
            logger.debug(f"[WCDB密钥] 命中缓存密钥 (PID={pid})")
            return self._cached_key

        # 🚀 优化：如果非强制刷新，在注入 Hook 前，先尝试通过当前进程 PID 进行精确匹配或暴力解密测试。
        # 避免对已登录微信进程重复注入挂钩引发超时 and 崩溃，确保多开/多电脑环境绝对稳定。
        if not force_refresh:
            try:
                from src.utils.wechat_key_store import get_persisted_wechat_key, KEYS_FILE_PATH
                
                # 1. 精准反查微信号并恢复其专属密钥
                target_wxid = _find_wxid_by_pid(pid)
                if target_wxid:
                    persisted_key = get_persisted_wechat_key(target_wxid)
                    if persisted_key and len(persisted_key) == 64:
                        if _validate_key_for_pid(persisted_key, pid):
                            logger.info(f"[WCDB密钥] 成功通过 PID={pid} 精准匹配到 KeyStore 专属密钥 {persisted_key[:6]}...{persisted_key[-6:]}，免除 Hook 注入")
                            self._cached_key = persisted_key
                            self._cached_for_pid = pid
                            return persisted_key

                # 2. 暴力扫描本地所有的密钥，测试哪个密钥能解密该进程的数据库
                if os.path.exists(KEYS_FILE_PATH):
                    with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                        keys_data = json.load(f)
                    if isinstance(keys_data, dict):
                        candidate_keys = set()
                        for k, v in keys_data.items():
                            if isinstance(v, str) and len(v) == 64:
                                candidate_keys.add(v)
                        
                        for cand_key in candidate_keys:
                            if _validate_key_for_pid(cand_key, pid):
                                logger.info(f"[WCDB密钥] 成功通过解密匹配找到与 PID={pid} 绑定的本地历史密钥")
                                self._cached_key = cand_key
                                self._cached_for_pid = pid
                                # 若反查到了 WXID，将此密钥重新与之绑定持久化，自愈绑定关系
                                if target_wxid:
                                    from src.utils.wechat_key_store import persist_wechat_key
                                    persist_wechat_key(cand_key, target_wxid)
                                return cand_key
            except Exception as e_keystore:
                logger.debug(f"[WCDB密钥] 尝试从 KeyStore 恢复/匹配密钥异常: {e_keystore}")

        # ✖︎ [自我防卧] 若该 PID 被 auto_get_key 独占，跳过整个 DLL 注入流程（包括 init / poll / term）
        # 原因： auto_get_key 已安装好 Hook， wcdb_key_extractor 不应再进来占用 DLL buffer，
        # 更不应在自己的 finally 里调用 _x_term_session() 把它卖掉。
        try:
            import sys
            _excl_pids = getattr(sys, "_xm_bot4_exclusive_pids", set())
            if pid in _excl_pids:
                logger.info(f"[WCDB密钥] PID={pid} 被 auto_get_key 独占，跳过 wcdb_key_extractor 注入流程")
                return None
        except Exception:
            pass

        if not self._load_dll():
            self._failed_pids[pid] = time.time()
            return None

        print(f"[WCDB密钥] 开始注入 PID={pid} 提取 WCDB 密钥 (超时={timeout_s}s)...")
        logger.info(f"[WCDB密钥] 开始注入 PID={pid} 提取 WCDB 密钥 (超时={timeout_s}s)...")
        try:
            token = _get_dynamic_token()
            ok = self._init_hook(pid, token, None, None, 0)
            if not ok:
                err = b""
                if self._get_last_error:
                    err = self._get_last_error() or b""
                err_str = err.decode("utf-8", errors="ignore") if isinstance(err, bytes) else str(err)
                if "Hook已经初始化" in err_str or "already initialized" in err_str:
                    logger.warning(f"[WCDB密钥] Hook 已经初始化，跳过注入直接尝试轮询提取密钥。")
                else:
                    logger.error(f"[WCDB密钥] InitializeHook 失败: {err_str or '未知错误'}")
                    self._failed_pids[pid] = time.time()  # 记录失败时间，触发冷却
                    return None

            key_buf = ctypes.create_string_buffer(256)
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if self._poll_key(key_buf, 256):
                    key = key_buf.value.decode("utf-8", errors="ignore").strip()
                    if len(key) == 64:
                        self._cached_key = key
                        self._cached_for_pid = pid
                        print(f"[WCDB密钥] [OK] 密钥提取成功 (PID={pid})")
                        logger.info(f"[WCDB密钥] 密钥提取成功 (PID={pid}, key={key[:8]}...)")
                        # 同步触发图片密钥派生（DLL 此时已加载，kvcomm 可读）
                        try:
                            from src.wechat_4x.image_key_extractor import get_image_keys
                            wxid_hint = _find_wxid_by_pid(pid) or ""
                            img_aes, img_xor = get_image_keys(wxid=wxid_hint, force=True)
                            if img_aes:
                                logger.info(f"[WCDB密钥] 同步获取图片密钥成功: aes={img_aes}")
                        except Exception as _img_e:
                            logger.debug(f"[WCDB密钥] 同步图片密钥提取异常: {_img_e}")
                        return key


                # 读取状态日志
                status_buf = ctypes.create_string_buffer(256)
                level = ctypes.c_int(0)
                if self._get_status(status_buf, 256, ctypes.byref(level)):
                    msg = status_buf.value.decode("utf-8", errors="ignore").strip()
                    if msg:
                        print(f"[WCDB密钥] DLL状态: {msg}")
                        logger.info(f"[WCDB密钥] DLL状态: {msg}")

                time.sleep(0.15)

            print(f"[WCDB密钥] [ERROR] 提取超时 ({timeout_s}s)")
            logger.warning(f"[WCDB密钥] 提取超时 ({timeout_s}s)")

            # 🔄 [方案 C 兜底] Hook 超时后，尝试内存扫描直接提取密钥，避免重启微信（封号风险）
            # 适用场景：微信已登录，sqlite3_key 调用已结束，密钥驻留在进程内存中，
            # 直接用 HMAC-SHA512 交叉验证提取，参考 wechat-decrypt/key_scan_common.py 原理。
            try:
                from src.wechat_4x.wcdb_mem_scanner import scan_wechat_key
                from src.wechat_4x.db_match_helper import get_wechat_base_dirs
                from src.utils.wechat_key_store import persist_wechat_key

                target_wxid = _find_wxid_by_pid(pid)
                print(f"[WCDB密钥] 🔄 启动内存扫描兜底 (PID={pid}, wxid={target_wxid or '未知'})...")

                # 确定 db_storage 目录：优先精确匹配目标账号
                db_storage_dirs = []
                from src.utils.wechat_key_store import clean_wxid
                import hashlib as _hs
                for base_dir in get_wechat_base_dirs():
                    if not os.path.isdir(base_dir):
                        continue
                    for entry in os.listdir(base_dir):
                        entry_clean = clean_wxid(entry) if target_wxid else entry
                        if target_wxid:
                            target_clean = clean_wxid(target_wxid)
                            target_md5 = _hs.md5(target_clean.encode()).hexdigest()
                            if entry_clean not in (target_clean, target_md5):
                                continue
                        db_path = os.path.join(base_dir, entry, "db_storage")
                        if os.path.isdir(db_path):
                            db_storage_dirs.append(db_path)

                if db_storage_dirs:
                    mem_key = scan_wechat_key(pid, db_storage_dirs)
                    if mem_key and len(mem_key) == 64:
                        # ✅ 二次验证：HMAC 匹配后，用真实 DB 解密做最终确认，防止旧进程/跨账号误匹配
                        try:
                            from src.wechat_4x.wcdb_key_helpers import _validate_key_for_pid
                            _db_valid = _validate_key_for_pid(mem_key, pid)
                        except Exception:
                            _db_valid = True  # 验证异常时保守放行

                        if _db_valid:
                            persist_wechat_key(mem_key, target_wxid)
                            self._cached_key = mem_key
                            self._cached_for_pid = pid
                            logger.info(f"[WCDB密钥] ✅ 内存扫描兜底成功 (PID={pid})")
                            return mem_key
                        else:
                            print(f"[WCDB密钥] ⚠️ 内存扫描命中 enc_key={mem_key[:8]}... 但 DB 解密校验失败（可能是旧进程或跨账号误匹配），丢弃")
                            logger.warning(f"[WCDB密钥] 内存扫描命中密钥但 DB 验证失败 PID={pid}，丢弃")
                    else:
                        print("[WCDB密钥] 内存扫描兜底未命中（微信可能未完成登录或 db_storage 尚未初始化）")

                else:
                    print("[WCDB密钥] 内存扫描兜底：未找到可用 db_storage 目录（微信可能尚未登录过）")
            except Exception as _mem_e:
                logger.debug(f"[WCDB密钥] 内存扫描兜底异常: {_mem_e}")

            self._failed_pids[pid] = time.time()  # 记录失败时间，触发冷却
            return None
        finally:
            # ✖︎ [Hook 生命周期防卧] 卒除前必须检查 PID 是否正被 auto_get_key 独占。
            # 根因：若 auto_get_key 在进入微信主页前就装好了 Hook，
            # wcdb_key_extractor 不应用自己的 finally 把它卖掉！
            try:
                import sys
                _excl_pids2 = getattr(sys, "_xm_bot4_exclusive_pids", set())
                _skip_term = pid in _excl_pids2
            except Exception:
                _skip_term = False
 
            if _skip_term:
                logger.info(f"[WCDB密钥] PID={pid} 被 auto_get_key 独占，跳过 _x_term_session（由 auto_get_key finally 负责卸载）")
            elif self._cleanup_hook:
                try:
                    self._cleanup_hook()
                    logger.info("[WCDB密钥] 已成功执行脱钩自清，彻底移除微信内存中的 Hook 监听与 IPC 通信")
                except Exception as e_clean:
                    logger.debug(f"[WCDB密钥] 自动脱钩清理异常: {e_clean}")

    def clear_cache(self):
        """清除密钥缓存，下次调用 get_key() 时会重新提取"""
        self._cached_key = None
        self._cached_for_pid = None
        logger.info("[WCDB密钥] 密钥缓存已清除")


# 全局单例，供 wcdb_session_monitor 使用
_extractor_instance: Optional[WcdbKeyExtractor] = None


def get_wcdb_key_extractor() -> WcdbKeyExtractor:
    global _extractor_instance
    if _extractor_instance is None:
        _extractor_instance = WcdbKeyExtractor()
    return _extractor_instance
