"""
实例管理器（已升级为支持 mmap 跨进程共享内存同步，且适配 4.1.x Qt 窗口）
"""
import mmap
import json
import os
import asyncio
from typing import Dict, Optional, List
import logging
try:
    import win32gui
except ImportError:
    win32gui = None


logger = logging.getLogger(__name__)

from src.utils.mmap_shared_state import (
    MMAP_SIZE,
    _read_mmap_state,
    _write_mmap_state,
    _broadcast_instances_changed
)


class InstanceManagerV2:
    """微信实例管理器 V2（通过 mmap 支持多开跨进程共享内存同步，且适配 4.1.x Qt 窗口）"""
    _instance = None

    def __init__(self):
        # 共享内存存储初始化
        from src.crm.account_data import get_account_data_dir
        common_dir = get_account_data_dir("common")
        os.makedirs(common_dir, exist_ok=True)
        self._mmap_path = os.path.join(common_dir, "wechat_instances.mmap")
        
        # 初始化 mmap 文件
        if not os.path.exists(self._mmap_path) or os.path.getsize(self._mmap_path) != MMAP_SIZE:
            try:
                with open(self._mmap_path, "wb") as f:
                    f.write(b'\x00' * MMAP_SIZE)
                _write_mmap_state(self._mmap_path, {"instances": {}, "active_instance_id": None})
            except Exception as e:
                logger.error(f"[实例管理] 创建 wechat_instances.mmap 失败: {e}")

    def _restore_snapshot_once(self):
        """一次性从快照静默热恢复存活的微信实例"""
        if getattr(self, '_snapshot_restored', False):
            return
        self._snapshot_restored = True
        try:
            from src.utils.instance_snapshot import WeChatInstanceSnapshotStore
            restored = WeChatInstanceSnapshotStore.restore_live_instances()
            if restored > 0:
                logger.info(f"[实例管理] 成功从快照静默热恢复了 {restored} 个存活的微信实例")
        except Exception as e:
            logger.error(f"[实例管理] 尝试从快照恢复实例异常: {e}")

    @classmethod
    def get_instance(cls) -> 'InstanceManagerV2':
        """单例模式"""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._restore_snapshot_once()
        return cls._instance

    def register_instance(self, instance_id: str, window_handle: int, nickname: str = '') -> bool:
        """注册微信实例 (适配 4.1.x 窗口属性校验和 mmap 跨进程共享)"""
        try:
            if win32gui is None:
                is_valid = True
            else:
                cls_name = win32gui.GetClassName(window_handle)
                is_valid = cls_name.endswith("Qt51514QWindowIcon") or cls_name.endswith("WeChatMainWndForPC")
            if not is_valid:
                logger.warning(f"[实例管理] 警告：尝试注册非标准微信窗口类名 {cls_name!r}")
        except Exception:
            pass

        state = _read_mmap_state(self._mmap_path)
        instances = state.setdefault("instances", {})
        active_id = state.get("active_instance_id")

        # 🌟 强力去重：清理具有相同物理窗口句柄或相同微信号标识的旧临时注册项
        dead_keys = []
        for k, v in list(instances.items()):
            if k == instance_id:
                continue
            # 1. 窗口句柄完全相同
            if v.get('window_handle') == window_handle:
                dead_keys.append(k)
            # 2. 正式微信号 wxid 完全相同 (排除以 wx_ 开头的临时 UUID 注册项)
            elif instance_id and not instance_id.startswith("wx_"):
                if v.get('wxid') == instance_id or k == instance_id:
                    dead_keys.append(k)

        for dk in dead_keys:
            logger.info(f"[实例管理] 发现重复或句柄冲突的实例项，自动清理旧键: {dk}")
            if dk in instances:
                del instances[dk]
            if active_id == dk:
                state["active_instance_id"] = instance_id

        instances[instance_id] = {
            'window_handle': window_handle,
            'nickname': nickname,
            'active': False,
            'status': 'online',
        }

        # 如果当前无活跃实例，自动将此实例设为活跃
        if not state.get("active_instance_id") or state.get("active_instance_id") == instance_id:
            state["active_instance_id"] = instance_id
            instances[instance_id]['active'] = True

        success = _write_mmap_state(self._mmap_path, state)
        if success:
            logger.info(f'[实例管理] 注册实例成功 (mmap 共享): {instance_id} ({nickname})')
            try:
                from src.utils.instance_snapshot import WeChatInstanceSnapshotStore
                WeChatInstanceSnapshotStore.save_snapshot(instances)
            except Exception:
                pass
            _broadcast_instances_changed()
        return success

    def get_active_instance(self) -> Optional[dict]:
        """获取当前活跃实例"""
        instances = self.get_all_instances()  # 触发清理
        state = _read_mmap_state(self._mmap_path)
        active_id = state.get("active_instance_id")
        if active_id and active_id in instances:
            return instances[active_id]
        return None

    def get_active_instance_id(self) -> Optional[str]:
        """获取当前活跃实例 ID"""
        self.get_all_instances()  # 触发清理
        state = _read_mmap_state(self._mmap_path)
        return state.get("active_instance_id")

    def set_active_instance(self, instance_id: str) -> bool:
        """设置活跃实例 (mmap 共享同步)"""
        state = _read_mmap_state(self._mmap_path)
        instances = state.get("instances", {})

        from src.utils.offline_detector import get_known_wxids
        known_wxids = get_known_wxids()

        # 允许通过 wxid 属性匹配在线实例
        target_key = instance_id
        if instance_id not in instances and instance_id not in known_wxids:
            matched_alive = None
            for k, v in instances.items():
                if v.get("wxid") == instance_id:
                    matched_alive = k
                    break
            if matched_alive:
                target_key = matched_alive
            else:
                return False

        # 取消原有活跃实例状态
        active_id = state.get("active_instance_id")
        if active_id and active_id in instances:
            instances[active_id]['active'] = False

        state["active_instance_id"] = target_key
        if target_key in instances:
            instances[target_key]['active'] = True

        success = _write_mmap_state(self._mmap_path, state)
        if success:
            try:
                from src.utils.instance_snapshot import WeChatInstanceSnapshotStore
                WeChatInstanceSnapshotStore.save_snapshot(instances)
            except Exception:
                pass
            _broadcast_instances_changed()
        return success

    def update_instance(self, instance_id: str, data: dict) -> bool:
        """更新微信实例属性 (mmap 共享同步)"""
        state = _read_mmap_state(self._mmap_path)
        instances = state.setdefault("instances", {})
        if instance_id not in instances:
            return False
        instances[instance_id].update(data)
        success = _write_mmap_state(self._mmap_path, state)
        if success:
            try:
                from src.utils.instance_snapshot import WeChatInstanceSnapshotStore
                WeChatInstanceSnapshotStore.save_snapshot(instances)
            except Exception:
                pass
            _broadcast_instances_changed()
        return success

    def get_all_instances(self) -> Dict[str, dict]:
        """获取所有实例 (混合在线实例与历史离线实例)"""
        state = _read_mmap_state(self._mmap_path)
        instances = state.get("instances", {})
        
        # 自动清理失效的窗口句柄实例
        dead_keys = []
        for inst_id, inst in list(instances.items()):
            # 只要是正在登录或在线状态的实例，就进行失效检测，不再依赖 active 属性，从而正确回收未激活/扫码中的临时实例
            if inst.get("status") in ("online", "login_pending"):
                hwnd = inst.get("window_handle")
                if hwnd:
                    try:
                        if win32gui is None:
                            pass
                        elif not win32gui.IsWindow(int(hwnd)):
                            dead_keys.append(inst_id)
                    except Exception:
                        dead_keys.append(inst_id)
                else:
                    dead_keys.append(inst_id)
                
        if dead_keys:
            for k in dead_keys:
                inst = instances.get(k) or {}
                nickname = inst.get("nickname") or k
                try:
                    # 只有正式在线实例发生掉线才发送报警，临时扫码实例静默清理不打扰用户
                    is_temp = k.startswith("wx_") or "instance" in k or any(c in k for c in ("微信", "分身", "多开", "隔离"))
                    if not is_temp and inst.get("status") == "online":
                        from src.utils.alert_notifier import alert_notifier
                        import asyncio, threading
                        body = f"微信实例 [{nickname}] 已掉线，请重新扫码恢复托管"
                        try:
                            import asyncio
                            try:
                                loop = asyncio.get_running_loop()
                                loop.create_task(alert_notifier.send_user_notification("⚠️ 微信账号下线警报", body, "system"))
                            except RuntimeError:
                                import threading
                                threading.Thread(
                                    target=lambda: asyncio.run(alert_notifier.send_user_notification("⚠️ 微信账号下线警报", body, "system")),
                                    daemon=True
                                ).start()
                        except Exception as e_notify:
                            logger.warning(f"[实例管理] 发送掉线警报失败: {e_notify}")
                except Exception:
                    pass
                if k in instances:
                    is_temp = k.startswith("wx_") or "instance" in k or any(c in k for c in ("微信", "分身", "多开", "隔离"))
                    if is_temp:
                        # 临时扫码实例直接物理删除，防止脏数据堆积霸占多开席位
                        del instances[k]
                    else:
                        instances[k]['status'] = 'offline'
                        instances[k]['window_handle'] = None
                        instances[k]['active'] = False
            # 顺便检查 active_instance_id 并保持更新到 state 里
            active_id = state.get("active_instance_id")
            if active_id in dead_keys:
                state["active_instance_id"] = next((id for id, item in instances.items() if item.get('status') in ('online', 'login_pending')), None)
                if state["active_instance_id"]:
                    instances[state["active_instance_id"]]['active'] = True
            state["instances"] = instances
            success = _write_mmap_state(self._mmap_path, state)
            if success:
                try:
                    from src.utils.instance_snapshot import WeChatInstanceSnapshotStore
                    WeChatInstanceSnapshotStore.save_snapshot(instances)
                except Exception:
                    pass
                _broadcast_instances_changed()
            
        # 🌟 混合历史已提取密钥或本地已登录过的微信账号作为 Offline 实例展示
        try:
            from src.utils.offline_detector import get_offline_instances_dict
            offline_insts = get_offline_instances_dict()
            for wxid, inst_data in offline_insts.items():
                is_alive = False
                for inst_id, inst in list(instances.items()):
                    if inst_id == wxid or inst.get("wxid") == wxid:
                        is_alive = True
                        break
                if not is_alive:
                    instances[wxid] = inst_data
        except Exception as e_off:
            logger.debug(f"[实例] 探测离线账号异常: {e_off}")

        # 4. 快速更新各微信号的 has_key 属性
        try:
            from src.utils.wechat_key_store import KEYS_FILE_PATH, clean_wxid
            key_data = {}
            if os.path.exists(KEYS_FILE_PATH):
                with open(KEYS_FILE_PATH, "r", encoding="utf-8") as f:
                    key_data = json.load(f)
            for inst_id, inst in instances.items():
                wxid_clean = clean_wxid(inst.get('wxid') or inst_id)
                inst['has_key'] = bool(key_data.get(wxid_clean)) if (wxid_clean and isinstance(key_data, dict)) else False
        except Exception:
            pass

        return instances


    def remove_instance(self, instance_id: str):
        """移除实例 (mmap 共享同步)"""
        state = _read_mmap_state(self._mmap_path)
        instances = state.get("instances", {})
        if instance_id in instances:
            del instances[instance_id]
            active_id = state.get("active_instance_id")
            if active_id == instance_id:
                state["active_instance_id"] = next(iter(instances), None)
                if state["active_instance_id"]:
                    instances[state["active_instance_id"]]['active'] = True
            success = _write_mmap_state(self._mmap_path, state)
            if success:
                try:
                    from src.utils.instance_snapshot import WeChatInstanceSnapshotStore
                    WeChatInstanceSnapshotStore.save_snapshot(instances)
                except Exception:
                    pass
                _broadcast_instances_changed()

    @classmethod
    def get_active_instance_config(cls):
        """获取活跃实例的配置管理器（兼容 xm-bot4 调用方式）"""
        return cls.get_instance()


class InstanceManagerV3(InstanceManagerV2):
    """实例管理器 V3（继承 V2，扩展异步支持）"""

    async def async_set_active(self, instance_id: str) -> bool:
        """异步设置活跃实例"""
        return self.set_active_instance(instance_id)

    async def async_get_active(self) -> Optional[dict]:
        """异步获取活跃实例"""
        return self.get_active_instance()
