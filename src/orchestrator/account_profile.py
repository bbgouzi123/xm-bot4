"""账号级拟人节奏配置与节流器工厂
========================================

在 UIBus 执行每一条命令前，节流器会被调用一次，返回 None/0 表示放行；
返回正数 N 表示这个账号需要再延后 N 秒才能被调度。配合 UIBus 的"塞回
队尾 + sleep"逻辑，天然实现人类工作节奏：

    - 休息时间段（rest_time）内：长延迟直到醒来
    - 相邻两条命令之间：至少 ``min_interval`` 秒间隔（含随机抖动）
    - 可按工作日/周末差异化 tempo，未来还能接行业画像

只要 main.py 启动时 `ui_bus.set_default_throttle_factory(make_account_throttle)`，
新账号第一次入队就会自动拿到一个节流器，无需任何业务层代码配合。
"""
from __future__ import annotations

import datetime
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class AccountTempo:
    """账号节奏画像（一个账号一份）。"""
    # 两条命令之间的随机最短间隔（秒）
    min_interval: float = 1.5
    max_interval: float = 3.5
    # 休息时段被触发时，一次回退多少秒（越大越省 CPU）
    rest_backoff: float = 30.0
    # 命中休息时是否打印日志（第一次命中后去重，避免刷屏）
    verbose_rest_log: bool = True
    # 最近一次放行时间戳（内部使用）
    last_pass_ts: float = 0.0
    # 最近一次休息提示日志时间，用于去重
    last_rest_log_ts: float = 0.0
    # 配额熔断后的回退秒数：等配额重置或手动提升后再动
    quota_backoff: float = 600.0
    # 最近一次配额熔断的 (dim, ts)：用来去重日志
    last_quota_block: tuple = ("", 0.0)


# 默认画像（未来可以按行业/账号等级差异化）
DEFAULT_TEMPO = AccountTempo()


class _AccountThrottle:
    """单账号节流器。UIBus worker 线程会反复调用 ``__call__()``。"""

    def __init__(self, wxid: str, tempo: Optional[AccountTempo] = None):
        self.wxid = wxid
        # 每个账号一份独立状态，拷贝默认画像防止共享污染
        if tempo is None:
            tempo = AccountTempo(
                min_interval=DEFAULT_TEMPO.min_interval,
                max_interval=DEFAULT_TEMPO.max_interval,
                rest_backoff=DEFAULT_TEMPO.rest_backoff,
                verbose_rest_log=DEFAULT_TEMPO.verbose_rest_log,
            )
        self.tempo = tempo
        self._lock = threading.Lock()

    # --- 对外接口：ui_bus 的节流器签名 ---------------------------------
    # UIBus 会先尝试 throttle(cmd)，TypeError 时回退到 throttle()。
    # 我们同时支持两种调用方式，cmd 传入时可按 kind 做配额熔断。
    def __call__(self, cmd=None) -> Optional[float]:
        # 1) 休息时段：整体阻塞
        if self._in_rest_time():
            self._log_rest_once()
            return self.tempo.rest_backoff

        # 2) 每日配额熔断：某些 kind 已经打爆配额，直接长退避
        if cmd is not None:
            quota_delay = self._quota_backoff_for(cmd)
            if quota_delay is not None:
                return quota_delay

        # 3) 拟人节奏：每两条命令间强制最小间隔
        now = time.time()
        with self._lock:
            delta = now - self.tempo.last_pass_ts
            target = random.uniform(
                self.tempo.min_interval, self.tempo.max_interval,
            )
            if delta < target:
                return round(target - delta, 2)
            # 放行：把"本次放行时间"记下来，供下一次间隔判断
            self.tempo.last_pass_ts = now
        return None

    # --- 内部 ---------------------------------------------------------
    # UICommandKind.value → DailyCounter 维度的映射。巡游命中 like 配额满
    # 时整轮也不让跑；送消息走 auto_reply 维度；加好友两个 kind 都算 add_friend
    _KIND_DIM: Dict[str, str] = {
        "send_message": "auto_reply",
        "publish_moment": "moment_post",
        "moment_interact": "like",
        "add_friend": "add_friend",
        "accept_friend": "add_friend",
    }

    def _quota_backoff_for(self, cmd) -> Optional[float]:
        """根据 cmd.kind 找 DailyCounter 维度。配额耗尽返回 quota_backoff。"""
        try:
            kind_str = getattr(cmd.kind, "value", str(cmd.kind))
        except Exception:
            return None
        dim = self._KIND_DIM.get(kind_str)
        if not dim:
            return None
        try:
            from src.utils.daily_counter import DailyCounter
            counter = DailyCounter()
            if not counter.can_do(dim, self.wxid or "main"):
                self._log_quota_once(dim)
                return self.tempo.quota_backoff
        except Exception as e:
            # 计数器故障：宁可放行也不要卡死业务
            logger.debug(f"[AccountThrottle] quota 检查异常 dim={dim}: {e}")
            return None
        return None

    def _log_quota_once(self, dim: str) -> None:
        """配额熔断去重日志（同一维度 10 分钟打一次）。"""
        now = time.time()
        last_dim, last_ts = self.tempo.last_quota_block
        if last_dim == dim and now - last_ts < 600:
            return
        self.tempo.last_quota_block = (dim, now)
        logger.warning(
            f"[AccountThrottle] 账号 {self.wxid or '<primary>'} "
            f"维度 {dim} 已打满日配额，UIBus 将暂停该类命令 "
            f"{int(self.tempo.quota_backoff)}s"
        )

    def _in_rest_time(self) -> bool:
        try:
            from src.utils.rest_time import is_rest_time
            # 不传 action_type：按时间段整体屏蔽；force_awake 生效时自动跳过
            return is_rest_time(account_id=self.wxid or None)
        except Exception as e:
            # 任何异常都视为放行，不能因为节流器故障卡死账号
            logger.debug(f"[AccountThrottle] rest_time 检查异常: {e}")
            return False

    def _log_rest_once(self) -> None:
        if not self.tempo.verbose_rest_log:
            return
        now = time.time()
        # 每 5 分钟打一次日志，避免 worker loop 刷屏
        if now - self.tempo.last_rest_log_ts > 300:
            self.tempo.last_rest_log_ts = now
            logger.info(
                f"[AccountThrottle] 账号 {self.wxid or '<primary>'} "
                f"正处于休息时段，UIBus 已暂停该账号调度"
            )


# 进程级账号→节流器缓存
_throttle_cache: Dict[str, _AccountThrottle] = {}
_cache_lock = threading.Lock()


def make_account_throttle(wxid: str) -> Callable[[], Optional[float]]:
    """节流器工厂（提供给 UIBus.set_default_throttle_factory）。

    同一个 wxid 重复调用会拿到同一份 _AccountThrottle 实例，保证节奏状态
    跨命令连续。未来可改成从 DB 读取账号画像。
    """
    key = wxid or "__primary__"
    with _cache_lock:
        existing = _throttle_cache.get(key)
        if existing is not None:
            return existing
        throttle = _AccountThrottle(wxid=wxid)
        _throttle_cache[key] = throttle
        logger.info(
            f"[AccountThrottle] 账号 {key} 已装载拟人节奏节流器 "
            f"interval={throttle.tempo.min_interval}~{throttle.tempo.max_interval}s"
        )
        return throttle


def configure_tempo(wxid: str, **kwargs) -> None:
    """在运行时调整某个账号的节奏画像（用于前端"加速/放慢"按钮）。"""
    throttle = _throttle_cache.get(wxid or "__primary__")
    if not throttle:
        throttle = _AccountThrottle(wxid=wxid)
        _throttle_cache[wxid or "__primary__"] = throttle
    for k, v in kwargs.items():
        if hasattr(throttle.tempo, k):
            setattr(throttle.tempo, k, v)


def get_tempo_snapshot() -> Dict[str, Dict[str, float]]:
    """给前端驾驶舱输出当前各账号的节奏快照。"""
    out: Dict[str, Dict[str, float]] = {}
    with _cache_lock:
        for k, t in _throttle_cache.items():
            out[k] = {
                "min_interval": t.tempo.min_interval,
                "max_interval": t.tempo.max_interval,
                "rest_backoff": t.tempo.rest_backoff,
                "quota_backoff": t.tempo.quota_backoff,
                "last_pass_ts": t.tempo.last_pass_ts,
            }
    return out
