import time
import logging
import win32gui
import uiautomation as uia

logger = logging.getLogger(__name__)

def get_moment_list_snapshot(list_ctrl) -> dict:
    """生成当前朋友圈列表的第一项和最后一项标识以及位置信息快照。"""
    try:
        from src.utils.safe_uia import safe_get_children
        children = safe_get_children(list_ctrl)
        if not children:
            return {}
        
        # 排除顶部的封面区域项，只监控真实的好友动态项
        from src.monitor.moment_utils import is_cover_item
        effective_children = [c for c in children if not is_cover_item(c, list_ctrl)]
        if not effective_children:
            effective_children = children

        first = effective_children[0]
        last = effective_children[-1]
        
        first_rect = first.BoundingRectangle
        last_rect = last.BoundingRectangle
        
        return {
            "count": len(effective_children),
            "first_key": f"{first.ClassName}|{getattr(first, 'Name', '')[:30]}",
            "last_key": f"{last.ClassName}|{getattr(last, 'Name', '')[:30]}",
            "first_top": first_rect.top,
            "last_bottom": last_rect.bottom
        }
    except Exception as e:
        logger.debug(f"[滚动快照] 采集朋友圈列表快照失败: {e}")
        return {}


def verify_scroll_displacement(before: dict, after: dict) -> bool:
    """比对滚动前后首尾项标志变化或具体坐标高度差判定滑动是否成功（阈值 8 像素）"""
    if not before or not after:
        return False
    if before["first_key"] != after["first_key"] or before["last_key"] != after["last_key"]:
        return True
    
    delta_top = abs(before["first_top"] - after["first_top"])
    delta_bottom = abs(before["last_bottom"] - after["last_bottom"])
    return max(delta_top, delta_bottom) >= 8


def handle_scroll_block(sns_window) -> tuple:
    """向朋友圈发送 ESC 退出键。返回 (是否到底/关闭, 是否成功恢复)"""
    logger.warning("[熔断检测] 朋友圈滑动未产生位移，怀疑遭遇弹窗卡屏。发送 ESC 尝试清除阻塞...")
    
    # 模拟按下 ESC 键
    try:
        uia.SendKeys("{ESC}")
        time.sleep(0.8)
    except Exception as e:
        logger.debug(f"[熔断检测] 发送 ESC 异常: {e}")
        return False, False
        
    # 再次检查朋友圈主窗口是否存在
    try:
        hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "朋友圈") or win32gui.FindWindow("mmui::SNSWindow", "朋友圈") or win32gui.FindWindow("SNSWnd", None)
        if not hwnd or not win32gui.IsWindow(hwnd):
            logger.info("[熔断检测] 朋友圈窗口已销毁。判定由于滑动至尽头按 ESC 导致窗口关闭，巡游正常收尾。")
            return True, False
            
        # 窗口还在，说明刚才确实清除了阻碍滑动的二级弹窗 (例如“该朋友圈已被删除”的 XDialog 等)
        logger.info("[熔断检测] 朋友圈窗口依然存在，判定为二级弹窗阻塞清理成功，正在重新置前恢复...")
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return False, True
    except Exception:
        return False, False
