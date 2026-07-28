"""
图片合成服务 — 将文案文字渲染到底图上

核心功能：
1. compose_text_on_image：将文案绘制到底图指定位置（蒙版 + 白色文字）
2. calculate_publish_times：在指定时段内均匀分布发布时间，并加随机抖动
3. split_copy_text：按指定分隔方式拆分文案文本
"""
import os
import random
import logging
from uuid import uuid4
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

from src.api.file_api import UPLOAD_DIR

logger = logging.getLogger(__name__)

# ===== 字体路径优先级 =====
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """按优先级加载字体，全部失败时回退到默认字体"""
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    logger.warning("[图片合成] 未找到任何可用中文字体，使用 Pillow 默认字体")
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    """根据字体和最大宽度，将文案自动换行"""
    lines: List[str] = []
    current_line = ""

    for char in text:
        # 遇到显式换行符直接换行
        if char == "\n":
            lines.append(current_line)
            current_line = ""
            continue

        test_line = current_line + char
        bbox = font.getbbox(test_line)
        line_width = bbox[2] - bbox[0]

        if line_width <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = char

    if current_line:
        lines.append(current_line)

    return lines if lines else [""]


def compose_text_on_image(
    background_path: str,
    text: str,
    output_dir: str = None,
    position: str = "bottom",       # bottom / center / top
    font_size: int = 36,
    padding: int = 40,
    overlay_opacity: int = 160,     # 蒙版透明度 0~255
    font_color: str = "#FFFFFF",    # 文字颜色 hex
) -> str:
    """
    将文案文字渲染到底图上。

    流程：
    1. 打开底图（支持 PNG/JPG/WEBP），转为 RGBA
    2. 根据底图宽度和字号自动将文案换行
    3. 在指定位置绘制半透明黑色蒙版条
    4. 在蒙版区域居中渲染文字
    5. 保存合成图并返回文件路径

    Args:
        background_path: 底图文件路径
        text: 要渲染的文案文字
        output_dir: 输出目录，默认 UPLOAD_DIR
        position: 文字位置 (bottom / center / top)
        font_size: 字号
        padding: 蒙版内边距
        overlay_opacity: 蒙版透明度 (0=全透明, 255=不透明)
        font_color: 文字颜色 hex 字符串 (如 '#FFFFFF')

    Returns:
        合成图文件的绝对路径
    """
    # 解析 font_color hex 为 RGBA 元组
    fc = font_color.lstrip("#")
    if len(fc) == 6:
        r, g, b = int(fc[0:2], 16), int(fc[2:4], 16), int(fc[4:6], 16)
    else:
        r, g, b = 255, 255, 255
    fill_color = (r, g, b, 255)

    # 1. 打开底图并转为 RGBA
    img = Image.open(background_path).convert("RGBA")
    img_width, img_height = img.size

    # 2. 加载字体并自动换行
    font = _load_font(font_size)
    line_spacing = int(font_size * 0.4)  # 行间距
    text_max_width = img_width - padding * 2
    lines = _wrap_text(text, font, text_max_width)

    # 3. 计算蒙版尺寸
    total_text_height = len(lines) * (font_size + line_spacing) - line_spacing
    overlay_height = total_text_height + padding * 2

    # 4. 计算蒙版 Y 坐标
    if position == "top":
        overlay_y = 0
    elif position == "center":
        overlay_y = (img_height - overlay_height) // 2
    else:  # bottom（默认）
        overlay_y = img_height - overlay_height

    # 5. 绘制半透明黑色蒙版
    overlay = Image.new("RGBA", (img_width, overlay_height), (0, 0, 0, overlay_opacity))
    img.paste(overlay, (0, overlay_y), overlay)

    # 6. 在蒙版区域居中渲染文字
    draw = ImageDraw.Draw(img)
    current_y = overlay_y + padding

    for line in lines:
        bbox = font.getbbox(line)
        line_width = bbox[2] - bbox[0]
        x = (img_width - line_width) // 2
        draw.text((x, current_y), line, fill=fill_color, font=font)
        current_y += font_size + line_spacing

    # 7. 保存合成图
    save_dir = Path(output_dir) if output_dir else UPLOAD_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    filename = f"manual_compose_{uuid4().hex}.png"
    save_path = save_dir / filename
    img.save(str(save_path), "PNG")

    logger.info(f"[图片合成] 成功：{save_path}（{len(lines)} 行文字，位置={position}，颜色={font_color}）")
    return str(save_path)


def calculate_publish_times(
    target_date: str,       # "2026-06-02"
    deadline_time: str,     # "18:00"
    count: int,             # 文案条数
    start_hour: int = 8,    # 最早发布时间
) -> List[str]:
    """
    在 start_hour ~ deadline_time 之间均匀分布 count 个时间点。

    每个时间点加 ±2~5 分钟随机抖动（模拟人类行为），
    返回 "YYYY-MM-DD HH:MM:SS" 格式的时间字符串列表。

    Args:
        target_date: 目标日期，格式 "YYYY-MM-DD"
        deadline_time: 截止时间，格式 "HH:MM"
        count: 需要分布的时间点数量
        start_hour: 最早发布小时（24小时制）

    Returns:
        排好序的时间字符串列表
    """
    if count <= 0:
        return []

    # 解析起止时间
    clean_date = target_date.split("T")[0].split(" ")[0] if target_date else datetime.now().strftime("%Y-%m-%d")
    base_date = datetime.strptime(clean_date, "%Y-%m-%d")
    start_dt = base_date.replace(hour=start_hour, minute=0, second=0)

    deadline_parts = deadline_time.split(":")
    deadline_hour = int(deadline_parts[0])
    deadline_minute = int(deadline_parts[1]) if len(deadline_parts) > 1 else 0
    end_dt = base_date.replace(hour=deadline_hour, minute=deadline_minute, second=0)

    # ── 关键修复：如果排期日期是今天，且当前时间已经晚于 start_hour，
    #    则将起始时间推迟到「当前时间 + 1分钟」，避免生成已过去的发布时间。
    now = datetime.now()
    if base_date.date() == now.date() and now > start_dt:
        # 向后推 1 分钟作为缓冲，确保排期不会在提交瞬间就过期
        start_dt = now + timedelta(minutes=1)
        start_dt = start_dt.replace(second=0)
        logger.info(f"[发布时间] 今日排期：起始时间调整为当前时间 {start_dt.strftime('%H:%M')}")

    # 安全检查：截止时间必须在开始时间之后
    if end_dt <= start_dt:
        logger.warning(f"[发布时间] 截止时间 {deadline_time} 不晚于起始 {start_dt.strftime('%H:%M')}，使用默认区间")
        end_dt = base_date.replace(hour=22, minute=0, second=0)

    total_seconds = (end_dt - start_dt).total_seconds()

    results: List[str] = []

    if count == 1:
        # 单条时放在中间位置
        mid_dt = start_dt + timedelta(seconds=total_seconds / 2)
        jitter = random.randint(-5, 5) * 60  # ±5 分钟
        mid_dt += timedelta(seconds=jitter)
        results.append(mid_dt.strftime("%Y-%m-%d %H:%M:%S"))
    else:
        # 均匀分布
        interval = total_seconds / (count - 1) if count > 1 else total_seconds
        for i in range(count):
            base_offset = interval * i
            # ±2~5 分钟随机抖动
            jitter_minutes = random.choice([-1, 1]) * random.randint(2, 5)
            jitter_seconds = jitter_minutes * 60
            actual_offset = base_offset + jitter_seconds

            # 钳制在合法范围内
            actual_offset = max(0, min(actual_offset, total_seconds))

            point_dt = start_dt + timedelta(seconds=actual_offset)
            results.append(point_dt.strftime("%Y-%m-%d %H:%M:%S"))

    # 按时间排序
    results.sort()
    logger.info(f"[发布时间] 已计算 {count} 个时间点：{target_date} {start_hour}:00 ~ {deadline_time}")
    return results


def split_copy_text(
    text: str,
    mode: str = "newline",         # 'newline' | 'blankline' | 'space' | 'custom'
    custom_delimiter: str = "",
) -> List[str]:
    """
    按指定分隔方式拆分文案文本，过滤空白项。

    Args:
        text: 原始文案文本
        mode: 拆分模式
            - newline: 按单个换行符拆分
            - blankline: 按空行（连续两个换行）拆分
            - space: 按空格拆分
            - custom: 按自定义分隔符拆分
        custom_delimiter: 自定义分隔符（仅 mode='custom' 时生效）

    Returns:
        过滤空白后的字符串列表
    """
    if not text or not text.strip():
        return []

    if mode == "blankline":
        # 按连续空行拆分（兼容 \r\n 和 \n）
        import re
        parts = re.split(r"\n\s*\n|\r\n\s*\r\n", text)
    elif mode == "space":
        parts = text.split(" ")
    elif mode == "custom" and custom_delimiter:
        parts = text.split(custom_delimiter)
    else:
        # 默认按单个换行符拆分
        parts = text.splitlines()

    # 过滤空白项
    result = [p.strip() for p in parts if p.strip()]
    logger.info(f"[文案拆分] 模式={mode}，拆分结果：{len(result)} 条")
    return result
