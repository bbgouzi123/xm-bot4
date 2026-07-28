import os
import json
import logging
try:
    import win32gui
    import win32process
except ImportError:
    win32gui = None
    win32process = None
import psutil
from typing import Dict, Any, Optional

logger = logging.getLogger("WeChatInstanceSnapshotStore")

class WeChatInstanceSnapshotStore:
    """微信实例快照存储，用于在进程重启后热附着恢复状态"""

    @staticmethod
    def get_snapshot_path() -> str:
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        snapshot_dir = os.path.join(appdata, "xm-bot4", "state")
        os.makedirs(snapshot_dir, exist_ok=True)
        return os.path.join(snapshot_dir, "wechat_instances_snapshot.json")

    @classmethod
    def save_snapshot(cls, instances: Dict[str, Any]) -> bool:
        """保存当前活跃的微信实例快照"""
        try:
            snapshot_data = {}
            for inst_id, inst in instances.items():
                hwnd = inst.get("window_handle")
                if not hwnd or not win32gui.IsWindow(hwnd):
                    continue

                # 获取窗口对应的 PID
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    pid = None

                snapshot_data[inst_id] = {
                    "window_handle": hwnd,
                    "nickname": inst.get("nickname", ""),
                    "wxid": inst.get("wxid", ""),
                    "avatar": inst.get("avatar", ""),
                    "pid": pid,
                    "active": inst.get("active", False)
                }

            snapshot_path = cls.get_snapshot_path()
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(snapshot_data, f, ensure_ascii=False, indent=4)
            logger.debug(f"[快照] 成功保存微信实例快照到 {snapshot_path}，记录数: {len(snapshot_data)}")
            return True
        except Exception as e:
            logger.error(f"[快照] 保存微信实例快照异常: {e}")
            return False

    @classmethod
    def restore_live_instances(cls) -> int:
        """检查并热附着恢复存活的微信实例，返回成功恢复的数量"""
        try:
            snapshot_path = cls.get_snapshot_path()
            if not os.path.exists(snapshot_path):
                return 0

            with open(snapshot_path, "r", encoding="utf-8") as f:
                snapshot_data = json.load(f)

            if not snapshot_data:
                return 0

            from src.utils.instance_manager import InstanceManagerV2
            manager = InstanceManagerV2.get_instance()
            restored_count = 0

            for inst_id, data in snapshot_data.items():
                hwnd = data.get("window_handle")
                pid = data.get("pid")
                nickname = data.get("nickname", "")
                wxid = data.get("wxid", "")
                avatar = data.get("avatar", "")
                active = data.get("active", False)

                if win32gui is None:
                    continue
                if not hwnd or not win32gui.IsWindow(hwnd):
                    continue

                # 检查进程 PID 校验
                is_live = False
                if pid:
                    try:
                        proc = psutil.Process(pid)
                        if "wechat" in proc.name().lower():
                            is_live = True
                    except Exception:
                        pass

                if is_live:
                    logger.info(f"[快照] 发现存活微信进程 (PID={pid}), 正在执行热附着重连: {nickname} (wxid={wxid})")
                    # 注册到实例管理器
                    manager.register_instance(inst_id, hwnd, nickname=nickname)
                    
                    # 恢复其它属性
                    update_data = {"status": "online"}
                    if wxid:
                        update_data["wxid"] = wxid
                    if avatar:
                        update_data["avatar"] = avatar
                    manager.update_instance(inst_id, update_data)
                    
                    if active:
                        manager.set_active_instance(inst_id)
                        
                    # 同步初始化 account_manager 里的驱动
                    try:
                        from app.state import account_manager as am
                        if hwnd not in am._instances:
                            from src.uia.driver import WeChatDriver
                            from src.monitor.chat_monitor import ChatMonitor
                            from src.monitor.multi_account_manager import AccountInstance
                            
                            temp_driver = WeChatDriver()
                            # 热连接
                            temp_driver.connect_by_hwnd(hwnd, extract_info=False)
                            # 手动注入已知的 nickname 和 wxid 避免二次拉取
                            temp_driver._nickname = nickname
                            temp_driver._wxid = wxid
                            
                            mon = ChatMonitor(temp_driver, am.ai_service)
                            from src.monitor.friend_request_monitor import FriendRequestMonitor
                            frm = FriendRequestMonitor(temp_driver, am.ai_service)
                            inst = AccountInstance(
                                hwnd=hwnd, driver=temp_driver, monitor=mon, friend_request_monitor=frm,
                                nickname=nickname, wxid=wxid,
                            )
                            am._instances[hwnd] = inst
                            logger.info(f"[快照] 已成功为热附着实例建立驱动与监控通道: {nickname}")
                    except Exception as am_ex:
                        logger.error(f"[快照] 热附着同步至 account_manager 异常: {am_ex}")
                    
                    restored_count += 1

            return restored_count
        except Exception as e:
            logger.error(f"[快照] 恢复微信实例异常: {e}")
            return 0
