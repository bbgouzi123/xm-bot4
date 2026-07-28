import time
import logging

logger = logging.getLogger(__name__)

def wait_if_screenshot_active(guard_inst):
    """如果最近触发过物理截屏快捷键，则挂起当前线程进行物理模拟避让，以防点击截屏遮罩"""
    last_time = getattr(guard_inst, "_last_screenshot_time", 0.0)
    if not last_time:
        return
    
    elapsed = time.time() - last_time
    if elapsed < 6.0:
        remaining = 6.0 - elapsed
        logger.info(f"[InputGuard] 检测到最近 {elapsed:.1f} 秒前触发了截屏，物理模拟避让挂起 {remaining:.1f} 秒...")
        
        start_wait = time.time()
        while time.time() - start_wait < remaining:
            if guard_inst._interrupt_requested:
                break
            try:
                guard_inst.update_status(f"微信截图避让中，剩余 {max(0.0, remaining - (time.time() - start_wait)):.1f} 秒...")
            except Exception:
                pass
            time.sleep(0.1)
