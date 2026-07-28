"""
朋友圈素材管理器（基于本地目录扫描架构）
支持计划名(Plans) -> 素材组(Groups) -> 素材文件(.txt, 图片)的三级层级结构
"""
import os
import logging
import subprocess
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)

class MomentMaterialManager:
    """朋友圈素材管理器（操作本地文件夹）"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MomentMaterialManager, cls).__new__(cls)
            cls._instance._init_once()
        return cls._instance
        
    def _init_once(self):
        # 默认存在用户目录下的 .xm-ai-bot/materials 文件夹
        self.base_dir = Path.home() / ".xm-ai-bot" / "materials"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # 初始化一个默认发圈计划
        default_plan = self.base_dir / "默认计划"
        if not default_plan.exists():
            default_plan.mkdir(parents=True, exist_ok=True)
            default_group = default_plan / "01_示例素材组"
            default_group.mkdir(parents=True, exist_ok=True)
            (default_group / "文案.txt").write_text("这是一条自动生成的示例朋友圈文案。\n第二行。", encoding="utf-8")
        
    def open_folder(self, folder_path: str = None) -> bool:
        """在资源管理器中打开指定系统目录"""
        target_dir = self.base_dir
        if folder_path:
            target_dir = self.base_dir / folder_path
            
        target_dir.mkdir(parents=True, exist_ok=True)
            
        try:
            if os.name == 'nt':
                os.startfile(str(target_dir))
            else:
                subprocess.Popen(['explorer', str(target_dir)]) # for mac use ['open', ...] but OS is windows
            return True
        except Exception as e:
            logger.error(f"打开素材目录失败: {e}")
            return False

    def select_folder(self) -> str:
        """返回当前的图库根目录路径（供前端展示）"""
        return str(self.base_dir)
        
    def list_plans(self) -> List[str]:
        """获取所有发圈计划名称（根目录下一级文件夹）"""
        if not self.base_dir.exists():
            return []
        return sorted([p.name for p in self.base_dir.iterdir() if p.is_dir()])
        
    def list_groups(self, plan_name: str) -> List[Dict]:
        """获取特定发圈计划下的所有素材组及其文案和图片"""
        plan_dir = self.base_dir / plan_name
        if not plan_dir.exists() or not plan_dir.is_dir():
            return []
            
        groups = []
        for group_dir in sorted(plan_dir.iterdir()):
            if group_dir.is_dir():
                txt_content = ""
                images = []
                # 寻找 txt
                for txt_file in group_dir.glob("*.txt"):
                    try:
                        txt_content = txt_file.read_text(encoding='utf-8')
                        break
                    except Exception:
                        try:
                            txt_content = txt_file.read_text(encoding='gbk', errors='ignore')
                            break
                        except Exception:
                            pass
                
                # 寻找图片
                for ext in ["*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", 
                            "*.JPG", "*.JPEG", "*.PNG", "*.GIF", "*.WEBP"]:
                    for img_file in group_dir.glob(ext):
                        img_path = str(img_file)
                        if img_path not in images:
                            images.append(img_path)
                
                groups.append({
                    "name": group_dir.name,
                    "text": txt_content,
                    "images": sorted(images),
                })
        return groups
