import os
import re
import tempfile
import uuid
import logging
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

def draw_table_to_image(headers: List[str], rows: List[List[str]]) -> str:
    """
    使用 Pillow 将解析出的表格数据动态渲染为极简高雅的 PNG 表格长图。
    """
    # 默认寻找系统中已安装的微软系列中文字体
    font_path = "C:\\Windows\\Fonts\\msyh.ttc"
    if not os.path.exists(font_path):
        font_path = "C:\\Windows\\Fonts\\msyh.ttf"
    if not os.path.exists(font_path):
        font_path = "C:\\Windows\\Fonts\\simsun.ttc"

    try:
        font_title = ImageFont.truetype(font_path, 15)
        font_body = ImageFont.truetype(font_path, 13)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()

    # 测量尺寸辅助
    temp_img = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(temp_img)

    def get_text_width(text, font):
        try:
            bbox = font.getbbox(str(text))
            return bbox[2] - bbox[0]
        except AttributeError:
            return draw.textsize(str(text), font=font)[0]

    def get_text_height(text, font):
        try:
            bbox = font.getbbox(str(text))
            return bbox[3] - bbox[1]
        except AttributeError:
            return draw.textsize(str(text), font=font)[1]

    # 自适应列宽计算
    col_widths = []
    for col_idx in range(len(headers)):
        max_w = get_text_width(headers[col_idx], font_title)
        for row in rows:
            if col_idx < len(row):
                w = get_text_width(row[col_idx], font_body)
                if w > max_w:
                    max_w = w
        # 加上内边距
        col_widths.append(max_w + 32)

    row_height = 36
    header_height = 40
    
    # 限制列宽最大与最小值防止错乱
    col_widths = [max(60, min(w, 400)) for w in col_widths]
    
    table_width = sum(col_widths) + 40
    table_height = header_height + len(rows) * row_height + 40

    # 实例化画布
    img = Image.new("RGB", (table_width, table_height), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    header_y_start = 20
    # 1. 绘制表头深蓝色现代感色块背景
    draw.rectangle([20, header_y_start, table_width - 20, header_y_start + header_height], fill="#3B82F6")

    # 2. 绘制行背景（斑马线相间色）
    for row_idx in range(len(rows)):
        y_start = header_y_start + header_height + row_idx * row_height
        bg_color = "#F9FAFB" if row_idx % 2 == 1 else "#FFFFFF"
        draw.rectangle([20, y_start, table_width - 20, y_start + row_height], fill=bg_color)

    # 3. 填充表头文本
    x_offset = 20
    for col_idx, header in enumerate(headers):
        w = get_text_width(header, font_title)
        h = get_text_height(header, font_title)
        text_x = x_offset + (col_widths[col_idx] - w) // 2
        text_y = header_y_start + (header_height - h) // 2
        draw.text((text_x, text_y), header, fill="#FFFFFF", font=font_title)
        x_offset += col_widths[col_idx]

    # 4. 填充数据行文本
    for row_idx, row in enumerate(rows):
        y_start = header_y_start + header_height + row_idx * row_height
        x_offset = 20
        for col_idx in range(len(headers)):
            val = row[col_idx] if col_idx < len(row) else ""
            # 若单元格文字超长，做折断截切
            if get_text_width(val, font_body) > (col_widths[col_idx] - 10):
                while len(val) > 3 and get_text_width(val + "...", font_body) > (col_widths[col_idx] - 10):
                    val = val[:-1]
                val += "..."
            
            w = get_text_width(val, font_body)
            h = get_text_height(val, font_body)
            # 文字垂直居中，水平微距左对齐
            text_x = x_offset + 12
            text_y = y_start + (row_height - h) // 2
            draw.text((text_x, text_y), val, fill="#1F2937", font=font_body)
            x_offset += col_widths[col_idx]

    # 5. 绘制精美边框和网格线
    # 表格主边框
    draw.rectangle([20, header_y_start, table_width - 20, table_height - 20], outline="#E5E7EB", width=1)
    
    # 横向分割线
    for row_idx in range(len(rows)):
        y = header_y_start + header_height + row_idx * row_height
        draw.line([20, y, table_width - 20, y], fill="#E5E7EB", width=1)

    # 纵向分割线
    x_offset = 20
    for col_idx in range(len(headers) - 1):
        x_offset += col_widths[col_idx]
        draw.line([x_offset, header_y_start, x_offset, table_height - 20], fill="#E5E7EB", width=1)

    # 保存图片
    temp_dir = os.path.join(tempfile.gettempdir(), "xm_bot4_materials")
    os.makedirs(temp_dir, exist_ok=True)
    out_path = os.path.join(temp_dir, f"table_{uuid.uuid4().hex[:12]}.png")
    img.save(out_path, "PNG")
    logger.info(f"[TableCompiler] Markdown表格已动态编译为原生PNG长图: {out_path}")
    return out_path


def extract_and_convert_tables(text: str) -> Tuple[str, List[str]]:
    """
    提取文本中的 Markdown 表格结构，并转换为实体 PNG 表格图片。
    在原文本中，将表格结构剔除。
    """
    image_paths = []
    lines = text.split("\n")
    new_lines = []
    in_table = False
    table_lines = []

    def flush_table(tbl_lines) -> str:
        if not tbl_lines or len(tbl_lines) < 2:
            return "\n".join(tbl_lines)
            
        # 校验是否有分割行结构
        has_divider = False
        for l in tbl_lines:
            if re.search(r'^\s*\|\s*:?-+:?\s*\|', l) or re.search(r'^\s*\|\s*:?-+\s*\|\s*:?-+\s*\|', l):
                has_divider = True
                break
        if not has_divider:
            return "\n".join(tbl_lines)

        headers = []
        rows = []
        for line in tbl_lines:
            parts = [p.strip() for p in line.split("|")]
            if parts and parts[0] == "":
                parts.pop(0)
            if parts and parts[-1] == "":
                parts.pop()
            
            if not parts:
                continue

            # 分割行剔除
            if all(re.match(r'^:?-+:?$', p) for p in parts):
                continue

            if not headers:
                headers = parts
            else:
                rows.append(parts)

        if headers:
            try:
                img_path = draw_table_to_image(headers, rows)
                if img_path:
                    image_paths.append(img_path)
                return "" # 将文字里的表格直接擦除，发物理图片即可
            except Exception as e:
                logger.error(f"[TableCompiler] 表格图片编译过程异常: {e}")
                return "\n".join(tbl_lines)
        return "\n".join(tbl_lines)

    for line in lines:
        if line.strip().startswith("|"):
            in_table = True
            table_lines.append(line)
        else:
            if in_table:
                res = flush_table(table_lines)
                if res:
                    new_lines.append(res)
                table_lines = []
                in_table = False
            new_lines.append(line)

    if in_table:
        res = flush_table(table_lines)
        if res:
            new_lines.append(res)

    # 过滤掉冗余空行
    cleaned_text = "\n".join(new_lines)
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text).strip()
    return cleaned_text, image_paths
