import logging
import random
import time
from typing import List, Optional, Any
from src.uia.retry import physical_click, random_delay
from src.monitor.moment_utils import parse_moment_item

logger = logging.getLogger(__name__)

class MomentGroup:
    """
    聚合朋友圈动态对象：
    新版 Qt 微信会将一条动态拆分为多个平级的 Cell（头像/内容/图片/评论等），
    本类将这些分散的 Cell 在内存中重新归并聚合成结构化的独立动态。
    同时完美向下兼容老版微信（单个大容器直接包装为 group）。
    """
    def __init__(self, is_legacy: bool = False, raw_item: Any = None):
        self.is_legacy = is_legacy
        self.raw_item = raw_item
        
        self.avatar_cell = raw_item if is_legacy else None
        self.content_cell = raw_item if is_legacy else None
        self.media_cell = None
        self.comment_cell = None
        
        self.publisher = ""
        self.content = ""
        self.time_str = ""

    def parse_info(self) -> bool:
        """解析朋友圈发布者昵称和正文内容"""
        if self.is_legacy:
            if not self.raw_item:
                return False
            info = parse_moment_item(self.raw_item.Name)
            if info:
                self.publisher = info.get("publisher", "")
                self.content = info.get("content", "")
                self.time_str = info.get("time_str", "")
                return True
            return False
        
        # 新版聚合解析：回退使用 media_cell 或 avatar_cell 提取昵称
        target_cell = self.content_cell or self.media_cell or self.avatar_cell
        if target_cell:
            info = parse_moment_item(target_cell.Name)
            if info:
                self.publisher = info.get("publisher", "")
                self.content = info.get("content", "")
                self.time_str = info.get("time_str", "")
                return True
        return False


def aggregate_timeline_cells(items: List[Any]) -> List[MomentGroup]:
    """
    核心归并聚合算法：
    将列表下扁平排布的 TimelineCell、TimelineContentCell 归并组合为 MomentGroup 数组。
    """
    groups: List[MomentGroup] = []
    current_group: Optional[MomentGroup] = None
    
    for item in items:
        try:
            cls = item.ClassName or ""
            ctype = item.ControlTypeName or ""
            if ctype != 'ListItemControl':
                continue
                
            # 旧版微信直接包装
            if cls not in ("mmui::TimelineCell", "mmui::TimelineContentCell", 
                           "mmui::TimelineGridImageCell", "mmui::TimelineCommentCell"):
                groups.append(MomentGroup(is_legacy=True, raw_item=item))
                continue
                
            # 新版微信扁平 Cell 归并
            if cls == "mmui::TimelineCell":
                # 出现 TimelineCell，代表新的一条动态开始（或者大占位符）
                rect = item.BoundingRectangle
                height = rect.bottom - rect.top if rect else 0
                
                # 新版微信封面过滤：列表首个高大（大于 280px）的 TimelineCell 必定是个人封面，予以跳过
                is_first_cell = (len(groups) == 0 and current_group is None)
                if is_first_cell and height > 280:
                    continue
                
                # 头像 Cell 往往具有一定的高度（如 40px+ 或者至少大于 15px）
                if height > 15:
                    if current_group:
                        groups.append(current_group)
                    current_group = MomentGroup(is_legacy=False)
                    current_group.avatar_cell = item
                elif current_group and not current_group.avatar_cell:
                    current_group.avatar_cell = item
                    
            elif cls == "mmui::TimelineContentCell":
                if not current_group:
                    current_group = MomentGroup(is_legacy=False)
                current_group.content_cell = item
                
            elif cls == "mmui::TimelineGridImageCell":
                if not current_group:
                    current_group = MomentGroup(is_legacy=False)
                current_group.media_cell = item
                
            elif cls == "mmui::TimelineCommentCell":
                if not current_group:
                    current_group = MomentGroup(is_legacy=False)
                current_group.comment_cell = item
                # 遇到评论 Cell 表明当前动态已告一段落
                groups.append(current_group)
                current_group = None
        except Exception as e:
            logger.debug(f"[聚合] 处理单个 Cell 异常: {e}")
            
    if current_group:
        groups.append(current_group)
        
    # 填充解析结果并过滤出有效的 Group
    valid_groups = []
    for g in groups:
        if g.avatar_cell and g.parse_info() and g.publisher:
            valid_groups.append(g)
            
    return valid_groups


def click_moment_avatar_physical(group: MomentGroup) -> bool:
    """分辨率自适应物理模拟点击朋友圈头像"""
    try:
        cell = group.avatar_cell
        if not cell:
            return False
        rect = cell.BoundingRectangle
        if not rect or rect.right <= rect.left:
            return False
            
        # 头像位于 TimelineCell 区域的最左侧，物理位置高度垂直居中
        click_x = rect.left + 30
        click_y = rect.top + (rect.bottom - rect.top) // 2
        logger.info(f"[RPA物理点击] 头像坐标自适应点击: ({click_x}, {click_y})")
        physical_click(click_x, click_y, settle=0.3)
        return True
    except Exception as e:
        logger.warning(f"[RPA物理点击] 头像点击异常: {e}")
        return False


def click_moment_interaction_area_physical(group: MomentGroup) -> bool:
    """物理模拟点击新旧版微信动态右下角的赞/评论互动触发点"""
    try:
        if group.is_legacy:
            # 旧版微信回退到原始点击逻辑
            from src.monitor.moment_utils import click_interaction_area
            return click_interaction_area(group.raw_item)
            
        # 新版微信：互动点一般在内容 Cell 或媒体 Cell 的右下角
        target_cell = group.media_cell or group.content_cell
        if not target_cell:
            target_cell = group.avatar_cell
            
        if not target_cell:
            return False
            
        rect = target_cell.BoundingRectangle
        if not rect or rect.right <= rect.left:
            return False
            
        # 互动点大约在右边偏移 55px，底边向上偏移 22px
        click_x = rect.right - 55
        click_y = rect.bottom - 22
        logger.info(f"[RPA物理点击] 赞评触发点物理点击: ({click_x}, {click_y})")
        physical_click(click_x, click_y, settle=0.2)
        return True
    except Exception as e:
        logger.warning(f"[RPA物理点击] 触发点点击异常: {e}")
        return False
