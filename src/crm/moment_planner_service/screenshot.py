import os
import uuid
import random
import logging
from pathlib import Path
from typing import Optional
from PIL import Image

logger = logging.getLogger(__name__)

def try_composite_screenshot(account_id: str, item_idx: int, industry_tag: str = "", industry_profile = None) -> Optional[str]:
    """尝试读取本地产品截图并透视形变叠加到该环境模版中"""
    current_dir = Path(os.path.dirname(os.path.abspath(__file__)))

    try:
        office_environments = {
            "car_insurance": [
                {
                    "path": str(Path(__file__).parent / "car_insurance_office_1.png"),
                    "monitors": [
                        [(102, 566), (428, 566), (428, 732), (102, 732)],
                        [(567, 566), (896, 566), (896, 732), (567, 732)]
                    ]
                }
            ],
            "education": [
                {
                    "path": str(Path(__file__).parent / "education_office_1.png"),
                    "monitors": [
                        [(145, 528), (416, 524), (418, 706), (152, 815)],
                        [(523, 456), (675, 452), (678, 595), (526, 638)]
                    ]
                }
            ],
            "generic": [
                {
                    "path": str(Path(__file__).parent / "unmanned_office_4.png"),
                    "monitors": [
                        [(52, 690), (250, 683), (250, 804), (52, 816)],
                        [(259, 683), (438, 690), (438, 822), (259, 815)],
                        [(568, 683), (741, 683), (741, 815), (568, 822)],
                        [(750, 682), (948, 696), (948, 814), (750, 805)]
                    ]
                },
                {
                    "path": str(Path(__file__).parent / "unmanned_office_2.png"),
                    "monitors": [
                        [(64, 786), (283, 781), (283, 919), (64, 925)],
                        [(289, 782), (487, 796), (487, 917), (289, 898)],
                        [(523, 782), (720, 782), (720, 924), (523, 916)],
                        [(726, 780), (944, 792), (944, 925), (726, 902)]
                    ]
                }
            ]
        }

        def get_env_key(name_str: str) -> str:
            name_lower = str(name_str or "").lower()
            if any(kw in name_lower for kw in ["车", "险", "car", "insurance", "auto"]):
                return "car_insurance"
            if any(kw in name_lower for kw in ["教", "育", "培", "训", "edu", "school", "class", "teach"]):
                return "education"
            return "generic"

        ind_name = ""
        if industry_profile:
            ind_name = getattr(industry_profile, "name", "")
        if not ind_name:
            ind_name = industry_tag or ""

        is_xm_bot4 = (
            ind_name == "xm-bot4系统" or 
            industry_tag == "xm-bot4系统" or
            (industry_profile and getattr(industry_profile, "id", "") == "sys_001")
        )

        industry_id = getattr(industry_profile, "id", "") if industry_profile else ""

        screenshot_path = None
        if is_xm_bot4:
            p_sys001 = current_dir.parent.parent / "assets" / "product_screenshot_sys_001.png"
            if p_sys001.exists():
                screenshot_path = p_sys001
            else:
                p_default = current_dir.parent.parent / "assets" / "product_screenshot.png"
                if p_default.exists():
                    screenshot_path = p_default
                else:
                    workspace_root = Path("d:/code/xm-core")
                    p_ws = workspace_root / "products/xm-bot4/image.png"
                    if p_ws.exists():
                        screenshot_path = p_ws
                    else:
                        p_local = Path("products/xm-bot4/image.png")
                        if p_local.exists():
                            screenshot_path = p_local
        else:
            if industry_id:
                p_ind = current_dir.parent.parent / "assets" / f"product_screenshot_{industry_id}.png"
                if p_ind.exists():
                    screenshot_path = p_ind

        if not screenshot_path or not screenshot_path.exists():
            logger.info(f"[日历生成] 行业 '{ind_name}' ({industry_id}) 未配置专属截图，降级为 AI 自动绘图。")
            return None

        selected_env = None
        if is_xm_bot4:
            try:
                from src.crm.industry_config import IndustryConfigManager
                icm = IndustryConfigManager(account_id=account_id)
                profiles = icm.get_all_profiles()
                other_names = [
                    p.name for p in profiles 
                    if p.name != "xm-bot4系统" and p.id != "sys_001" and p.name
                ]
                if other_names:
                    random.seed(item_idx)
                    chosen_name = random.choice(other_names)
                    selected_env = get_env_key(chosen_name)
                    random.seed()
            except Exception as e:
                logger.warning(f"[日历生成] 从行业列表随机提取办公背景抛出异常: {e}")

        if not selected_env:
            if is_xm_bot4:
                random.seed(item_idx)
                selected_env = random.choice(["car_insurance", "education", "generic"])
                random.seed()
            else:
                selected_env = get_env_key(ind_name)

        templates = office_environments.get(selected_env, office_environments["generic"])
        tpl = templates[item_idx % len(templates)]
        if not os.path.exists(tpl["path"]):
            return None

        bg = Image.open(tpl["path"]).convert("RGBA")
        screenshot = Image.open(str(screenshot_path)).convert("RGBA")
        sw, sh = screenshot.size
        src_pts = [(0, 0), (sw, 0), (sw, sh), (0, sh)]

        composite = bg.copy()
        active_monitors = tpl["monitors"]
        if len(active_monitors) > 1 and random.random() < 0.25:
            k = random.randint(1, len(active_monitors) - 1)
            active_monitors = random.sample(tpl["monitors"], k)

        def get_perspective_coeffs(s_pts, d_pts):
            matrix = []
            for (x, y), (u, v) in zip(d_pts, s_pts):
                matrix.append([x, y, 1, 0, 0, 0, -x*u, -y*u])
                matrix.append([0, 0, 0, x, y, 1, -x*v, -y*v])
            
            # 使用纯 Python 高斯消元算法求解 8x8 线性方程组，避免 numpy 底层 MKL/BLAS 内存冲突引发段错误闪退
            B = [p for pt in s_pts for p in pt]
            n = 8
            # 构造增广矩阵 M (8x9)
            M = [matrix[i] + [B[i]] for i in range(n)]
            for i in range(n):
                # 寻找主元
                max_row = i
                for r in range(i + 1, n):
                    if abs(M[r][i]) > abs(M[max_row][i]):
                        max_row = r
                if max_row != i:
                    M[i], M[max_row] = M[max_row], M[i]
                
                pivot = M[i][i]
                if abs(pivot) < 1e-12:
                    raise ValueError("Perspective transform matrix is singular")
                
                # 归一化当前行
                for j in range(i, n + 1):
                    M[i][j] /= pivot
                
                # 消去其它行
                for r in range(n):
                    if r != i:
                        factor = M[r][i]
                        for j in range(i, n + 1):
                            M[r][j] -= factor * M[i][j]
            return [M[k][n] for k in range(n)]

        for dst_pts in active_monitors:
            coeffs = get_perspective_coeffs(src_pts, dst_pts)
            warped = screenshot.transform(bg.size, Image.PERSPECTIVE, coeffs, Image.BICUBIC)
            composite = Image.alpha_composite(composite, warped)

        from src.api.file_api import UPLOAD_DIR
        unique_name = f"composite_moment_{uuid.uuid4().hex}.png"
        save_path = UPLOAD_DIR / unique_name
        composite.convert("RGB").save(save_path, "PNG")

        return f"/api/file/download/{unique_name}"

    except Exception as e:
        logger.error(f"[日历生成] 本地合成截图抛出异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None
