"""
行业配置数据模型 — IndustryProfile 与 chat_eq 工具函数

单个行业配置（支持聊天 + 朋友圈 + 视频多场景参数）
"""
from typing import Dict, List, Any

# 聊天「高情商」行为开关（与前端 chat_eq 键一致）；默认全开，旧数据缺失时按此合并
CHAT_EQ_DEFAULTS: Dict[str, bool] = {
    "mirror_emotion": True,
    "match_style": True,
    "empathy_first": True,
    "light_humor": True,
    "open_questions": True,
    "short_bubbles": True,
    "natural_emoji": True,
}


def merge_chat_eq(raw: Any) -> Dict[str, bool]:
    d = dict(CHAT_EQ_DEFAULTS)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in d:
                d[k] = bool(v)
    return d


class IndustryProfile:
    """单个行业配置（支持聊天 + 朋友圈 + 视频多场景参数）"""

    def __init__(self):
        self.id = ""
        self.name = ""           
        self.icon = "i-carbon-bot"         
        self.created = ""        
        self.product = ""        
        self.selling_point = ""  
        self.persona = ""        
        self.forbidden = ""      
        self.knowledge = ""      
        self.intensity = 2       
        self.tags_enabled = True  
        self.price_list = []           # 产品定价列表
        self.chat_eq: Dict[str, bool] = dict(CHAT_EQ_DEFAULTS)  # 高情商对话开关（聊天智能体）
        self.industry_id = ""          # 关联全局行业字典 (xm-user global_industries.id)
        self.materials: List[str] = [] # 专属营销配图/物料 URL 数组 (存储在 xm-oss 上)
        self.knowledge_files: List[Dict] = []  # 私域知识库上传文件列表 [{id, name, url, size, mime_type, uploaded_at}]
        self.homepage_link = ""        # 专属官网/介绍网页链接
        self.product_link = ""         # 产品链接
        self.enable_live_record = True # 是否开启10秒实时演示录屏自动发送
        self.phone = ""                # 官方联系电话
        self.address = ""              # 官方详细地址
        self.material_send_mode = "all" # 营销物料发送模式: all(全发), random_1(随机1个), random_limit(随机限制数量)
        self.material_send_limit = 3   # 物料最大发送数
        self.agent_routes = {"tags": [], "groups": []} # 个性化智能体路由策略 (标签/群聊路由)
        # ===== 朋友圈素材生成参数 =====
        self.moment_style = ""         # 文案风格（如：客户案例+避坑指南交替）
        self.moment_tone = ""          # 口吻调性（如：轻松有趣 / 专业权威 / 温暖走心）
        self.moment_keywords = ""      # 必带关键词（逗号分隔）
        self.moment_forbidden = ""     # 朋友圈禁词（逗号分隔）
        self.moment_image_style = ""   # 配图创意风格描述
        self.moment_hashtags = ""      # 常用标签（如：#装修避坑 #全屋定制）
        self.moment_media_type = "image" # 朋友圈配图偏好（image=仅图片, video=仅视频, mixed=混合）
        self.moment_image_country = "CN" # 配图场景国家/地域 (CN=中国, US_EU=欧美, JP_KR=日韩)
        self.moment_image_scene_type = "real_scene" # 配图场景类型 (real_scene=真实场景, comic=漫画/手绘, abstract=扁平插画, 3d_render=3D渲染)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "created": self.created,
            "product": self.product,
            "selling_point": self.selling_point,
            "persona": self.persona,
            "forbidden": self.forbidden,
            "knowledge": self.knowledge,
            "intensity": self.intensity,
            "tags_enabled": self.tags_enabled,
            "price_list": self.price_list,
            "chat_eq": self.chat_eq,
            "industry_id": self.industry_id,
            "materials": self.materials,
            "knowledge_files": self.knowledge_files,
            "homepage_link": self.homepage_link,
            "product_link": self.product_link,
            "enable_live_record": self.enable_live_record,
            "phone": self.phone,
            "address": self.address,
            "material_send_mode": self.material_send_mode,
            "material_send_limit": self.material_send_limit,
            "agent_routes": self.agent_routes,
            # 朋友圈参数
            "moment_style": self.moment_style,
            "moment_tone": self.moment_tone,
            "moment_keywords": self.moment_keywords,
            "moment_forbidden": self.moment_forbidden,
            "moment_image_style": self.moment_image_style,
            "moment_hashtags": self.moment_hashtags,
            "moment_media_type": self.moment_media_type,
            "moment_image_country": self.moment_image_country,
            "moment_image_scene_type": self.moment_image_scene_type,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IndustryProfile":
        p = cls()
        p.id = d.get("id", "")
        p.name = d.get("name", "")
        raw_icon = d.get("icon", "i-carbon-bot")
        # 自动迁移旧数据中的 emoji 图标为 UnoCSS 图标类名
        p.icon = raw_icon if isinstance(raw_icon, str) and raw_icon.startswith("i-") else "i-carbon-bot"
        p.created = d.get("created", "")
        p.product = d.get("product", "")
        p.selling_point = d.get("selling_point", "")
        p.persona = d.get("persona", "")
        p.forbidden = d.get("forbidden", "")
        p.knowledge = d.get("knowledge", "")
        p.intensity = d.get("intensity", 2)
        p.tags_enabled = d.get("tags_enabled", True)
        p.price_list = d.get("price_list", [])
        p.chat_eq = merge_chat_eq(d.get("chat_eq"))
        p.industry_id = d.get("industry_id", "")
        p.materials = d.get("materials", [])
        p.knowledge_files = d.get("knowledge_files", [])
        p.homepage_link = d.get("homepage_link", "")
        p.product_link = d.get("product_link", "")
        p.enable_live_record = d.get("enable_live_record", True)
        p.phone = d.get("phone", "")
        p.address = d.get("address", "")
        p.material_send_mode = d.get("material_send_mode", "all")
        p.material_send_limit = int(d.get("material_send_limit", 3))
        p.agent_routes = d.get("agent_routes", {"tags": [], "groups": []})
        # 朋友圈参数
        p.moment_style = d.get("moment_style", "")
        p.moment_tone = d.get("moment_tone", "")
        p.moment_keywords = d.get("moment_keywords", "")
        p.moment_forbidden = d.get("moment_forbidden", "")
        p.moment_image_style = d.get("moment_image_style", "")
        p.moment_hashtags = d.get("moment_hashtags", "")
        p.moment_media_type = d.get("moment_media_type", "image")
        p.moment_image_country = d.get("moment_image_country", "CN")
        p.moment_image_scene_type = d.get("moment_image_scene_type", "real_scene")
        return p

    def __repr__(self):
        return f"IndustryProfile({self.icon} {self.name}, intensity={self.intensity})"
