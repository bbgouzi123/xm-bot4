import mmap
import json
import os
import logging

logger = logging.getLogger(__name__)
MMAP_SIZE = 65536  # 64KB 共享内存空间

def _read_mmap_state(mmap_path: str) -> dict:
    """从共享内存中读取所有实例的状态"""
    if not os.path.exists(mmap_path):
        return {"instances": {}, "active_instance_id": None}
    try:
        with open(mmap_path, "r+b") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                data = mm.read(MMAP_SIZE)
                end_idx = data.find(b'\x00')
                if end_idx == 0:
                    return {"instances": {}, "active_instance_id": None}
                elif end_idx > 0:
                    data = data[:end_idx]
                try:
                    return json.loads(data.decode("utf-8"))
                except Exception:
                    return {"instances": {}, "active_instance_id": None}
    except Exception:
        return {"instances": {}, "active_instance_id": None}


def _write_mmap_state(mmap_path: str, state: dict) -> bool:
    """向共享内存写入最新的实例状态"""
    try:
        # 确保共享内存物理文件存在且大小正确
        if not os.path.exists(mmap_path) or os.path.getsize(mmap_path) != MMAP_SIZE:
            os.makedirs(os.path.dirname(mmap_path), exist_ok=True)
            with open(mmap_path, "wb") as f:
                f.write(b'\x00' * MMAP_SIZE)
                
        payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
        if len(payload) >= MMAP_SIZE:
            logger.error(f"[实例管理] 状态字节数超过共享内存上限 {MMAP_SIZE}")
            return False
            
        with open(mmap_path, "r+b") as f:
            with mmap.mmap(f.fileno(), MMAP_SIZE, access=mmap.ACCESS_WRITE) as mm:
                mm.seek(0)
                mm.write(payload)
                # 清空多余的空间
                mm.write(b'\x00' * (MMAP_SIZE - len(payload)))
                mm.flush()
        return True
    except Exception as e:
        logger.error(f"[实例管理] 写入 mmap 共享内存异常: {e}")
        return False


def _broadcast_instances_changed():
    """向前端广播实例发生变化的事件"""
    try:
        from src.utils.websocket_manager import ws_manager
        import asyncio
        import threading
        
        payload = {"type": "instances_changed", "data": {}}
        loop = ws_manager.loop
        
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), loop)
        else:
            try:
                curr_loop = asyncio.get_event_loop()
                if curr_loop.is_running():
                    curr_loop.create_task(ws_manager.broadcast(payload))
                else:
                    curr_loop.run_until_complete(ws_manager.broadcast(payload))
            except Exception:
                threading.Thread(
                    target=lambda: asyncio.run(ws_manager.broadcast(payload)),
                    daemon=True
                ).start()
    except Exception as e:
        logger.warning(f"[实例管理] 广播实例变更失败: {e}")
