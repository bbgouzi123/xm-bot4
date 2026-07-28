import random

def calculate_bezier_curve(x1: int, y1: int, x2: int, y2: int, points_num: int = 20) -> list:
    """纯 Python 生成二阶贝塞尔曲线平滑点集，免除第三方库依赖"""
    control_x = (x1 + x2) / 2 + random.randint(-40, 40)
    control_y = (y1 + y2) / 2 + random.randint(-40, 40)
    
    path = []
    for i in range(points_num):
        t = i / (points_num - 1) if points_num > 1 else 1.0
        px = (1.0 - t) ** 2 * x1 + 2.0 * (1.0 - t) * t * control_x + t ** 2 * x2
        py = (1.0 - t) ** 2 * y1 + 2.0 * (1.0 - t) * t * control_y + t ** 2 * y2
        path.append((int(px), int(py)))
    return path
