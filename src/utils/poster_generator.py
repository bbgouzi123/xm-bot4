import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def generate_poster(prompt: str, title_text: str, sub_text: str, save_path: Path):
    """使用 Pillow 在本地绘制营销渐变海报并保存"""
    width, height = 800, 1000
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    
    color_start = (18, 147, 254)  # 科技蓝
    color_end = (153, 51, 250)   # 极光紫
    
    low_prompt = prompt.lower()
    if "金" in low_prompt or "gold" in low_prompt or "尊" in low_prompt:
        color_start = (20, 20, 20)      # 暗夜黑
        color_end = (212, 175, 55)     # 奢华金
    elif "热" in low_prompt or "红" in low_prompt or "活动" in low_prompt:
        color_start = (255, 65, 108)   # 热力红
        color_end = (255, 75, 43)      # 活力橙
    elif "绿" in low_prompt or "健康" in low_prompt or "环保" in low_prompt:
        color_start = (17, 153, 142)   # 森林绿
        color_end = (56, 239, 125)     # 嫩绿
        
    # 1. 绘制背景渐变
    for y in range(height):
        factor = y / height
        r = int(color_start[0] + (color_end[0] - color_start[0]) * factor)
        g = int(color_start[1] + (color_end[1] - color_start[1]) * factor)
        b = int(color_start[2] + (color_end[2] - color_start[2]) * factor)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
        
    # 2. 使用 RGBA 浮层制作半透明圆角磨砂卡片
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    
    card_margin = 60
    # 填充半透明白色（Alpha = 35）和半透明白色边框（Alpha = 180）
    overlay_draw.rounded_rectangle(
        [(card_margin, card_margin), (width - card_margin, height - card_margin)],
        radius=30,
        fill=(255, 255, 255, 35),
        outline=(255, 255, 255, 180),
        width=2
    )
    image.paste(overlay, (0, 0), overlay)
    
    # 3. 加载中文字体
    font_path = "C:\\Windows\\Fonts\\msyh.ttc"
    if not os.path.exists(font_path):
        font_path = "C:\\Windows\\Fonts\\msyhbd.ttc"
    if not os.path.exists(font_path):
        font_path = "C:\\Windows\\Fonts\\simhei.ttf"
        
    try:
        title_font = ImageFont.truetype(font_path, 46) if os.path.exists(font_path) else ImageFont.load_default()
        body_font = ImageFont.truetype(font_path, 24) if os.path.exists(font_path) else ImageFont.load_default()
        footer_font = ImageFont.truetype(font_path, 18) if os.path.exists(font_path) else ImageFont.load_default()
    except Exception:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        footer_font = ImageFont.load_default()
        
    # 4. 绘制文字和排版线
    draw.text((width // 2, 240), title_text, fill=(255, 255, 255), font=title_font, anchor="mm")
    
    line_w = 120
    draw.line([(width // 2 - line_w, 290), (width // 2 + line_w, 290)], fill=(255, 255, 255, 200), width=3)
    
    max_chars = 20
    lines = []
    for i in range(0, len(sub_text), max_chars):
        lines.append(sub_text[i:i+max_chars])
        
    start_y = 360
    for idx, line in enumerate(lines):
        draw.text((width // 2, start_y + idx * 45), line, fill=(245, 245, 245), font=body_font, anchor="mm")
        
    # 5. 绘制带有圆角背景的 QR 码区域
    qr_size = 140
    qr_x = width // 2 - qr_size // 2
    qr_y = 660
    
    # QR 码背景使用纯白
    draw.rounded_rectangle([(qr_x, qr_y), (qr_x + qr_size, qr_y + qr_size)], radius=15, fill=(255, 255, 255))
    
    qr_draw = ImageDraw.Draw(image)
    for i in range(12):
        for j in range(12):
            if (i+j) % 3 == 0 or (i*j) % 5 == 2:
                bx = qr_x + 10 + i * 10
                by = qr_y + 10 + j * 10
                qr_draw.rectangle([(bx, by), (bx + 8, by + 8)], fill=color_start)
                
    draw.text((width // 2, qr_y + qr_size + 40), "长按识别 · 极速连接", fill=(255, 255, 255), font=footer_font, anchor="mm")
    draw.text((width // 2, height - 100), "Powered by xm-bot4 AI 数字人引擎", fill=(255, 255, 255, 120), font=footer_font, anchor="mm")

    image.save(save_path, "PNG")
