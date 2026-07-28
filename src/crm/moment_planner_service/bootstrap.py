import os
import sys
import time
import json
import uuid
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from urllib.parse import quote

from . import state

logger = logging.getLogger(__name__)

def expire_stale_pending_moments_and_collect_due(
    now: Optional[datetime] = None,
    lateness_sec: int = state.MOMENT_SCHEDULE_LATENESS_SEC,
) -> Tuple[int, List[dict]]:
    """将过久仍为 pending 的排期标记为 failed，并返回当前投递窗口内待执行的排期副本。"""
    from src.crm.account_data import get_active_account
    active_bot = get_active_account() or "default"

    if now is None:
        now = datetime.now()
    threshold = now - timedelta(seconds=lateness_sec)
    stale_count = 0
    due: List[dict] = []
    with state._schedule_lock:
        for s in state._schedules:
            # 数据隔离校验：只处理属于当前活跃微信账号的排期任务！
            s_bot = s.get("bot_wxid") or "default"
            if s_bot != active_bot:
                continue

            if s.get("status") != "pending":
                continue
            sid = s.get("id")
            if sid in state._executed_ids:
                continue
            st_raw = state._schedule_time_str(s)
            if not st_raw:
                continue
            st_dt = state._parse_schedule_datetime(st_raw)
            if st_dt is None:
                logger.warning("[日历引擎] 排期 #%s 时间无法解析: %r", sid, st_raw)
                continue
            if st_dt > now:
                continue
            if st_dt < threshold:
                s["status"] = "failed"
                s["error_msg"] = "已错过自动发送窗口（超过允许延迟，不再补发）"
                stale_count += 1
                continue
            due.append(dict(s))
    due.sort(key=state._schedule_sort_ts)
    return stale_count, due

def _flush_local_schedule_file(schedules_copy: List[dict], next_id_val: int) -> None:
    try:
        normalized = [state._normalize_schedule_item(s) for s in schedules_copy]
        state.get_local_schedule_dir().mkdir(parents=True, exist_ok=True)
        payload = {
            "schedules": normalized,
            "next_id": next_id_val,
            "saved_at": datetime.now().isoformat(),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)

        def _write_atomic() -> None:
            tmp = state.get_local_schedule_dir() / f".moment_sched_{os.getpid()}_{uuid.uuid4().hex[:12]}.tmp"
            try:
                tmp.write_text(text, encoding="utf-8")
                last_err = None
                for attempt in range(4):
                    try:
                        os.replace(str(tmp), str(state.get_local_schedule_file()))
                        return
                    except OSError as e:
                        last_err = e
                        if sys.platform == "win32" and getattr(e, "winerror", None) == 5:
                            time.sleep(0.05 * (attempt + 1))
                        else:
                            raise
                if last_err:
                    raise last_err
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

        try:
            _write_atomic()
        except OSError as e:
            if sys.platform == "win32" and getattr(e, "winerror", None) == 5:
                try:
                    state.get_local_schedule_file().write_text(text, encoding="utf-8")
                except OSError as e2:
                    raise e2 from e
            else:
                raise
        logger.debug(f"[日历引擎] 本地排期快照已保存 ({len(schedules_copy)} 条)")
    except Exception as e:
        logger.warning(f"[日历引擎] 本地排期快照保存失败: {e}")

def _load_schedules_from_local_file() -> bool:
    """从本地快照加载排期（支持空列表 -- 用户已删除所有排期的场景）。"""
    if not state.get_local_schedule_file().exists():
        return False
    try:
        raw = json.loads(state.get_local_schedule_file().read_text(encoding="utf-8"))
        schedules = raw.get("schedules", [])
        if not isinstance(schedules, list):
            return False
        nid = raw.get("next_id")
        with state._schedule_lock:
            state._schedules[:] = [state._normalize_schedule_item(s) for s in schedules]
            id_nums = [state._coerce_schedule_id(s.get("id")) for s in state._schedules]
            max_id = max(id_nums) if id_nums else 0
            if isinstance(nid, int) and nid > max_id:
                state._next_id = nid
            else:
                state._next_id = max_id + 1
        logger.info(f"[日历引擎] 从本地快照恢复 {len(state._schedules)} 条排期")
        return True
    except Exception as e:
        logger.warning(f"[日历引擎] 本地排期快照加载失败: {e}")
        return False

def _local_snapshot_saved_at() -> Optional[datetime]:
    """读取本地快照中的 saved_at 时间戳。"""
    try:
        if not state.get_local_schedule_file().exists():
            return None
        raw = json.loads(state.get_local_schedule_file().read_text(encoding="utf-8"))
        ts = raw.get("saved_at")
        if ts:
            return datetime.fromisoformat(str(ts))
    except Exception:
        pass
    return None

def _bootstrap_schedules():
    """启动时加载排期：自适应同步后端服务（本地 Rust 端口 42040 或远程同步后端） vs 本地 JSON 快照，取最新版本。

    核心逻辑：如果本地快照的 saved_at 在最近 120 秒内（说明刚做过删除等
    关键操作但同步后端推送可能尚未成功），优先信任本地快照，避免被旧数据覆盖。
    """
    LOCAL_FRESHNESS_SEC = 120

    # ---- 先尝试读取本地快照时间戳 ----
    local_saved_at = _local_snapshot_saved_at()
    local_is_fresh = False
    if local_saved_at:
        age = (datetime.now() - local_saved_at).total_seconds()
        if 0 <= age <= LOCAL_FRESHNESS_SEC:
            local_is_fresh = True
            logger.info(f"[日历引擎] 本地快照 {age:.0f}s 前刚保存，优先加载本地")

    # ---- 如果本地刚写入，优先加载本地快照（防止被旧同步数据覆盖删除） ----
    if local_is_fresh:
        if _load_schedules_from_local_file():
            # 异步将本地数据再推一次到同步后端服务，确保最终一致
            with state._schedule_lock:
                data = list(state._schedules)
            def _reconcile():
                try:
                    from src.utils.cloud_sync import get_cloud_client
                    get_cloud_client().sync_moment_schedules(data)
                    logger.info("[日历引擎] 启动时本地快照已重新推送到同步后端服务")
                except Exception as e:
                    logger.debug(f"[日历引擎] 启动时后端重推失败: {e}")
            threading.Thread(target=_reconcile, daemon=True, name="schedule-reconcile").start()
            return

    # ---- 正常路径：先尝试自适应同步后端服务（本地 Rust 42040 或线上同步后端） ----
    cloud_ok = False
    try:
        from src.utils.cloud_sync import get_cloud_client
        cloud = get_cloud_client()
        from src.crm.account_data import get_active_account
        bot_q = quote(get_active_account() or "", safe="")
        data = cloud._get(f"/api/v1/moments/schedules?bot_wxid={bot_q}", need_auth=True)
        if data and isinstance(data, list) and len(data) > 0:
            with state._schedule_lock:
                state._schedules[:] = [state._normalize_schedule_item(s) for s in data]
                id_nums = [state._coerce_schedule_id(s.get("id")) for s in state._schedules]
                state._next_id = (max(id_nums) if id_nums else 0) + 1
            cloud_ok = True
            logger.info(f"[日历引擎] 从同步后端服务加载 {len(data)} 条排期")
    except Exception as e:
        logger.debug(f"[日历引擎] 同步后端服务排期加载跳过: {e}")

    if cloud_ok:
        with state._schedule_lock:
            snap, nid = list(state._schedules), state._next_id
        _flush_local_schedule_file(snap, nid)
        return

    if not _load_schedules_from_local_file():
        logger.debug("[日历引擎] 无同步后端排期且无本地快照，从空列表开始")

def _persist_schedules_after_mutation():
    with state._schedule_lock:
        data = list(state._schedules)
        nid = state._next_id
    _flush_local_schedule_file(data, nid)

    def _push():
        try:
            from src.utils.cloud_sync import get_cloud_client
            get_cloud_client().sync_moment_schedules(data)
        except Exception as e:
            logger.debug(f"[日历引擎] 同步后端推送失败: {e}")

    threading.Thread(target=_push, daemon=True, name="schedule-push").start()

def _persist_schedules_sync(max_retries: int = 2) -> bool:
    """同步持久化排期（本地写入 + 同步云推送带重试）。用于删除等关键操作，确保同步后端一致。"""
    with state._schedule_lock:
        data = list(state._schedules)
        nid = state._next_id
    _flush_local_schedule_file(data, nid)

    for attempt in range(max_retries + 1):
        try:
            from src.utils.cloud_sync import get_cloud_client
            result = get_cloud_client().sync_moment_schedules(data)
            if result:
                logger.info(f"[日历引擎] 排期同步服务成功 ({len(data)} 条)")
                return True
            else:
                logger.warning(f"[日历引擎] 排期同步服务返回 None (尝试 {attempt+1}/{max_retries+1})")
        except Exception as e:
            logger.warning(f"[日历引擎] 排期同步服务异常 (尝试 {attempt+1}/{max_retries+1}): {e}")
        if attempt < max_retries:
            time.sleep(0.3 * (attempt + 1))
    logger.error("[日历引擎] 排期同步服务最终失败，本地快照已保存，下次重启将使用本地数据")
    return False

def reload_schedules_from_cloud_for_active_bot():
    try:
        from src.utils.cloud_sync import get_cloud_client
        from src.crm.account_data import get_active_account
        cloud = get_cloud_client()
        active_bot = get_active_account() or "default"
        bot_q = quote(active_bot, safe="")
        data = cloud._get(f"/api/v1/moments/schedules?bot_wxid={bot_q}", need_auth=True)
        with state._schedule_lock:
            if data and isinstance(data, list) and len(data) > 0:
                for s in data:
                    if isinstance(s, dict):
                        s["bot_wxid"] = active_bot
                state._schedules[:] = [state._normalize_schedule_item(s) for s in data]
                id_nums = [state._coerce_schedule_id(s.get("id")) for s in state._schedules]
                state._next_id = (max(id_nums) if id_nums else 0) + 1
                snap, nid = list(state._schedules), state._next_id
                _flush_local_schedule_file(snap, nid)
            else:
                # 优先从该账号的本地快照文件中加载恢复数据，防止冷启动或同步服务空响应把本地缓存覆写清空
                if not _load_schedules_from_local_file():
                    state._schedules[:] = []
                    state._next_id = 1
                    snap, nid = list(state._schedules), state._next_id
                    _flush_local_schedule_file(snap, nid)
                else:
                    for s in state._schedules:
                        s["bot_wxid"] = active_bot
                    snap, nid = list(state._schedules), state._next_id
                    _flush_local_schedule_file(snap, nid)
        logger.info(f"[日历引擎] 已按接管微信 {active_bot} 刷新排期 {len(state._schedules)} 条")
    except Exception as e:
        logger.warning(f"[日历引擎] 切换后排期云拉取失败: {e}")

_schedules_initialized = False
_schedules_init_lock = threading.Lock()

def bootstrap_schedules_lazy():
    """懒加载/就绪后初始化排期日历引擎，确保有有效 Token 才会向云端发起请求"""
    global _schedules_initialized
    if _schedules_initialized:
        return
    with _schedules_init_lock:
        if _schedules_initialized:
            return
        from src.utils.cloud_sync.helpers import try_load_sso_token
        token = try_load_sso_token()
        if not token:
            logger.debug("[日历引擎] SSO Token 尚未就绪，暂不联网初始化")
            return
        
        # 已有有效 Token，启动线程异步向云端同步
        threading.Thread(target=_bootstrap_schedules, daemon=True, name="schedule-init").start()
        _schedules_initialized = True

