"""
微信朋友圈自动互动：随机赞朋友圈封面。
流程：
1. 点击用户头像进入好友资料卡
2. 点击资料卡中“朋友圈”这一栏，进入该好友个人朋友圈主页
3. 定位封面背景图，执行右键物理点击，呼出“赞封面”右键菜单
4. 点击“赞封面”，然后点击左上角返回按钮返回朋友圈大厅
"""
import logging
import time
import random
import os
import json
from datetime import datetime, timedelta
import uiautomation as uia

from src.uia.retry import random_delay, try_click, physical_click
from src.uia.retry.clicks import physical_right_click
from src.utils.safe_uia import safe_exists, safe_get_children

logger = logging.getLogger(__name__)

def _get_cover_likes_file(account_id: str = None) -> str:
    """获取本地封面点赞日志文件路径"""
    try:
        from src.crm.account_data import get_account_data_dir
        return os.path.join(get_account_data_dir(account_id), "moment_cover_likes.json")
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".xm-ai-bot", "moment_cover_likes.json")

def has_liked_cover_recently(author: str, account_id: str = None) -> bool:
    """检查 7 天内是否赞过此用户的封面，防止高频重复访问打扰"""
    path = _get_cover_likes_file(account_id)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        ts_str = data.get(author)
        if not ts_str:
            return False
        ts = datetime.fromisoformat(ts_str)
        if datetime.now() - ts < timedelta(days=7):
            return True
    except Exception:
        pass
    return False

def record_cover_like(author: str, account_id: str = None):
    """记录封面点赞到本地 JSON"""
    path = _get_cover_likes_file(account_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    if not isinstance(data, dict):
        data = {}
    data[author] = datetime.now().isoformat()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def try_like_user_cover(manager, item, sns_window, settings, account_id) -> bool:
    """
    尝试进入个人朋友圈主页并点赞封面。
    """
    from src.uia.message_direction import get_dpi_scale
    
    try:
        # 获取列表项几何边界以精确定位头像
        rect = item.BoundingRectangle
        if not rect or rect.right <= rect.left or rect.bottom <= rect.top:
            return False
            
        scale = get_dpi_scale()
        # 根据 UI 规范，使用相对偏置坐标点击头像
        x = rect.left + int(38 * scale)
        y = rect.top + int(30 * scale)
        
        logger.info(f"[赞封面] 准备物理点击头像坐标: ({x}, {y})")
        physical_click(x, y)
        time.sleep(1.5)
        
        # 循环探测资料卡弹出
        profile_win = None
        for _ in range(15):
            from src.uia.message_direction import find_profile_hwnd
            hwnd = find_profile_hwnd()
            if hwnd:
                profile_win = uia.ControlFromHandle(hwnd)
                break
            time.sleep(0.1)
            
        if not profile_win:
            logger.warning("[赞封面] 未检测到 ProfileUniquePop 弹窗")
            return False
            
        logger.info("[赞封面] 成功捕捉资料卡名片窗口")
        
        # 寻找朋友圈行入口并点击
        clicked_moments = False
        for ctrl, _ in uia.WalkControl(profile_win, maxDepth=8):
            try:
                name = (ctrl.Name or "").strip()
                ctype = ctrl.ControlTypeName or ""
                if name == "朋友圈":
                    c_rect = ctrl.BoundingRectangle
                    if c_rect:
                        # 往右侧空白处偏置 100 像素以稳妥触发列表项的点击事件
                        px = c_rect.left + 100
                        py = (c_rect.top + c_rect.bottom) // 2
                        logger.info(f"[赞封面] 点击资料卡朋友圈栏坐标: ({px}, {py})")
                        physical_click(px, py)
                        clicked_moments = True
                        break
            except Exception:
                continue
                
        if not clicked_moments:
            logger.warning("[赞封面] 资料卡中未找到“朋友圈”栏锚点")
            try:
                profile_win.SendKeys("{ESC}")
            except:
                pass
            return False
            
        time.sleep(2.0)  # 等待新版朋友圈过渡动画
        
        # 定位封面区域
        list_ctrl = sns_window.ListControl(ClassName='mmui::TimeLineListView')
        if not safe_exists(list_ctrl, 1.0):
            list_ctrl = sns_window.ListControl()
            
        if not safe_exists(list_ctrl, 1.0):
            logger.warning("[赞封面] 未能定位个人朋友圈主页列表")
            return False
            
        items = safe_get_children(list_ctrl)
        cover_item = None
        from src.monitor.moment_utils import is_cover_item
        for c_item in items:
            if is_cover_item(c_item, list_ctrl):
                cover_item = c_item
                break
        if not cover_item and items:
            cover_item = items[0]
            
        if not cover_item:
            logger.warning("[赞封面] 无法提取封面 ListItem")
            return False
            
        cover_rect = cover_item.BoundingRectangle
        if not cover_rect:
            return False
            
        # 几何计算封面中心区，右键物理点击
        cx = (cover_rect.left + cover_rect.right) // 2
        cy = cover_rect.top + int(100 * scale)
        
        logger.info(f"[赞封面] 物理右键点击封面位置: ({cx}, {cy})")
        physical_right_click(cx, cy, settle=0.15, restore_cursor=True)
        time.sleep(0.8)
        
        # 匹配上下文菜单
        menu = uia.MenuControl(ClassName='CMenuWnd')
        liked_ok = False
        if menu.Exists(2.0):
            for child in menu.GetChildren():
                name = child.Name or ""
                if "赞封面" in name:
                    try:
                        child.GetInvokePattern().Invoke()
                    except Exception:
                        child.Click(simulateMove=False)
                    logger.info("[赞封面] 成功触发 '赞封面' 菜单事件")
                    liked_ok = True
                    break
                    
        if not liked_ok:
            logger.warning("[赞封面] 呼出右键菜单后，未能找到或激活 '赞封面' 选项")
            
        # 安全返回：寻找大厅返回按钮
        logger.info("[赞封面] 返回朋友圈主页大厅...")
        back_ok = False
        sw_rect = sns_window.BoundingRectangle
        best_btn = None
        min_dist = 999999
        
        for ctrl, _ in uia.WalkControl(sns_window, maxDepth=4):
            try:
                ctype = ctrl.ControlTypeName or ""
                name = ctrl.Name or ""
                if ctype == "ButtonControl" and (name == "返回" or "back" in name.lower()):
                    ctrl.Click()
                    back_ok = True
                    break
                if ctype == "ButtonControl":
                    rect = ctrl.BoundingRectangle
                    dist = ((rect.left - (sw_rect.left + 20))**2 + (rect.top - (sw_rect.top + 20))**2)**0.5
                    if dist < 100 and dist < min_dist:
                        min_dist = dist
                        best_btn = ctrl
            except Exception:
                continue
                
        if not back_ok and best_btn:
            logger.info(f"[赞封面] 降级点击左上角最近按钮返回，距离={min_dist}")
            best_btn.Click()
            back_ok = True
            
        if not back_ok:
            logger.warning("[赞封面] 未能定位返回按钮，发送 ESC 尝试回退")
            uia.SendKeys("{ESC}")
            
        time.sleep(1.0)
        return liked_ok
    except Exception as e:
        logger.exception(f"[赞封面] 整个赞封面链式流程发生错误: {e}")
        try:
            uia.SendKeys("{ESC}")
        except:
            pass
        return False
