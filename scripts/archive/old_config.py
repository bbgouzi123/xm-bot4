"""
多行业配置管理器 — 支持创建/切换/编辑多个行业配置

数据存储：xm-core（xm-bot4 后端）/ AccountDatabaseManager (SQLite) bot_config 表
"""
import json
import uuid
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class IndustryProfile:
    """单个行业配置"""

    def __init__(self):
        self.id = ""
        self.name = ""           
        self.icon = "🤖"         
        self.created = ""        
        self.product = ""        
        self.selling_point = ""  
        self.persona = ""        
        self.forbidden = ""      
        self.knowledge = ""      
        self.intensity = 2       
        self.tags_enabled = True  

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
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IndustryProfile":
        p = cls()
        p.id = d.get("id", "")
        p.name = d.get("name", "")
        p.icon = d.get("icon", "🤖")
        p.created = d.get("created", "")
        p.product = d.get("product", "")
        p.selling_point = d.get("selling_point", "")
        p.persona = d.get("persona", "")
        p.forbidden = d.get("forbidden", "")
        p.knowledge = d.get("knowledge", "")
        p.intensity = d.get("intensity", 2)
        p.tags_enabled = d.get("tags_enabled", True)
        return p

    def __repr__(self):
        return f"IndustryProfile({self.icon} {self.name}, intensity={self.intensity})"


class IndustryConfigManager:
    """多行业配置管理器 (已切换至 AccountDatabaseManager SQLite)"""

    def __init__(self, config_path: str = None, account_id: str = "main"):
        """初始化
        如果传入的 config_path (遗留参数) 被传入，将被忽略，强制走 SQLite 单例。
        """
        self.account_id = account_id
        if self.account_id == "main":
            from src.crm.account_data import get_active_account
            self.account_id = get_active_account()
        from src.utils.account_db import AccountDatabaseManager
        self.db = AccountDatabaseManager(self.account_id)
        
        self._profiles: List[IndustryProfile] = []
        self._active_id: str = ""
        self._load()

    def _load(self):
        """从 SQLite 加载行业配置"""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT config_value FROM bot_config WHERE config_key = 'industry_config_data'")
                row = cursor.fetchone()
                if row and row['config_value']:
                    industry = json.loads(row['config_value'])
                    self._active_id = industry.get("active_profile_id", "")
                    profiles_data = industry.get("profiles", [])
                    self._profiles = [IndustryProfile.from_dict(p) for p in profiles_data]
                    logger.debug(f"[CRM] 加载 {len(self._profiles)} 个行业配置, 活跃={self._active_id}")
        except Exception as e:
            logger.error(f"[CRM] 加载行业配置失败: {e}")

        if not self._profiles:
            defaults = [
                {"name": "xm-bot4全系AI代理", "icon": "🚀", "product": "xm-bot4矩阵营销系统、AI软件", "selling_point": "独家AI底层矩阵技术，解放双手自动精准拓客"},
                {"name": "房产经纪代理", "icon": "🏢", "product": "新房代理、二手房买卖、优质租赁", "selling_point": "内部底价房源，无套路专业解读购房政策与首付"},
                {"name": "高端医美机构", "icon": "✨", "product": "热玛吉、光电抗衰、轻医美微整", "selling_point": "执业医师亲诊，正品仪器授权，重塑自然美且效果可见"},
                {"name": "汽车销售顾问", "icon": "🚗", "product": "新能源汽车、豪车二手车置换", "selling_point": "超低首付零息方案，现车直提送终身整车质保"},
                {"name": "餐饮加盟招商", "icon": "🥘", "product": "品牌餐饮店加盟、供应链提供", "selling_point": "三个月高速回本模型，全程保姆式开店扶持帮扶"},
                {"name": "知识付费讲师", "icon": "📚", "product": "自媒体操盘课、商业变现训练营", "selling_point": "实战干货坚决不废话，手把手教你如何提升私域转化率"},
                {"name": "律所法律咨询", "icon": "⚖️", "product": "企业法律顾问、婚姻财产、债务追讨", "selling_point": "资深高胜率律师团队，案情免费专业评估，无忧胜诉"},
                {"name": "金融理财保险", "icon": "💰", "product": "企业融资贷款、家庭抗风险保险", "selling_point": "低息快速绿色下款，定制抗风险家族传承定制方案"},
                {"name": "二手名表奢品", "icon": "⌚", "product": "劳力士百达翡丽、爱马仕香奈儿回收", "selling_point": "高于同行回收价15%，中检鉴定保真，绝对当面打款"},
                {"name": "家政保洁服务", "icon": "🧹", "product": "日常保洁、开荒保洁、月嫂育儿嫂", "selling_point": "背景核查极严，阿姨均持证上岗，随叫随到无死角"},
                {"name": "婚纱摄影策划", "icon": "💍", "product": "婚纱摄影、婚礼策划、旅拍定制", "selling_point": "首席摄影总监亲掌镜，彻底拒绝套路消费，底片全送"},
                {"name": "教育培训机构", "icon": "🎓", "product": "K12辅导、考研辅导、公考培训、艺考舞蹈", "selling_point": "名师一对一精准提分，不过协议全额退费，陪伴式督学"},
                {"name": "母婴用品专卖", "icon": "🍼", "product": "奶粉尿裤、婴儿车童装、辅食营养品", "selling_point": "全球原产地直采，正品溯源码保障，高级会员长线批发价"},
                {"name": "宠物活体生活馆", "icon": "🐶", "product": "纯种猫狗繁育售卖、宠物洗浴修剪、进口主粮", "selling_point": "全透明繁育基地直供，包测细小犬瘟无后遗症，终身VIP售后"},
                {"name": "美容美发美甲", "icon": "💅", "product": "高级烫染、潮流美甲、头皮抗衰护理", "selling_point": "日韩系进修总监阿玛尼级手艺，使用纯天然进口有机药水"},
                {"name": "跨境电商出海", "icon": "🚢", "product": "亚马逊代运营、TikTok短视频带货、海外短阵", "selling_point": "零基础包教包会出海淘金，绝密对接真实第一手海外渠道"},
                {"name": "财税代理记账", "icon": "🧾", "product": "公司注册、代理记账、税务筹划、资质代办", "selling_point": "资深注册会计师亲自操刀，合法合理避税，账套100%全规"},
                {"name": "装修建材家居", "icon": "🛋️", "product": "全屋定制、软装搭配、智能家居系统", "selling_point": "源头工厂直接砍掉差价，零甲醛环保板材，拎包入住十年质保"},
                {"name": "旅游定制地接", "icon": "✈️", "product": "高端定制游、精品小包团、特价机票度假村", "selling_point": "纯玩无购物拒绝各种填坑套路，老司机带路打卡最美私藏小众秘境"},
                {"name": "猎头劳务派遣", "icon": "👷", "product": "灵活用工、企业中高端猎头招聘、蓝领劳务", "selling_point": "海量新鲜简历库极速漏斗匹配，三天内必推精准高质量真候选人"},
                {"name": "心理咨询疗愈", "icon": "🪷", "product": "婚姻情感挽回、抑郁焦虑疏导、亲子沟通", "selling_point": "国家级心理咨询师高度保密会谈，直击潜意识痛点，拥抱内心创伤"},
                {"name": "五金机械设备", "icon": "⚙️", "product": "工程机械、五金工具批零", "selling_point": "源头重工现货秒发，绝对耐磨损高精度，全国范围极速上门保修"},
                {"name": "中医药房养生", "icon": "🌿", "product": "名贵中药材、理疗推拿、老中医把脉", "selling_point": "道地药材以次充好假一赔十，三代祖传老中医对症下药，标本绝对兼治"},
                {"name": "农林生鲜特产", "icon": "🌾", "product": "原生态高端农副产品、高山野味特产、有机水果", "selling_point": "田间地头现摘闪电冷链现发，原生态不打任何违禁农药，找回儿时真味道"},
                {"name": "安防监控弱电", "icon": "📹", "product": "视频监控安装、网络综合布线、门禁人脸道闸", "selling_point": "大厂一级核心代理，勘察、设计、施工一条龙包干，售后一小时极速到达"},
                {"name": "物流快递同城", "icon": "🚚", "product": "整车零担干线运、同城急送、仓配无忧一体化", "selling_point": "全国直达不绕路不暴力中转，丢损24小时内全额光速直赔"},
                {"name": "健身瘦身瑜伽", "icon": "🏋️", "product": "减脂塑形私教、产后精修康复、各类瑜伽普拉提", "selling_point": "明星级金牌教练带教，签对赌协议包掉秤不变硬，完全开辟趣味锻炼路线"},
                {"name": "体育赛事团单", "icon": "🏆", "product": "企业团建极限拓展、少儿高尔夫等体育考级", "selling_point": "退役省队及国家队运动员执教，寓教于乐中，潜移默化增强身体巅峰素质"},
                {"name": "广告传媒公关", "icon": "🎬", "product": "企业宣传片拍摄、电梯广告包月投放、危机公关", "selling_point": "全域全媒体整合全案营销策划，院线工业级资深拍摄团队，让每一分预算砸出大回声"},
                {"name": "珠宝翡翠玉石", "icon": "💎", "product": "钻戒顶级定制、翡翠原石明料批发、国检黄金", "selling_point": "源头神秘矿区裸钻直供避开层层利润加价，带权威NGTC鉴定检测证书，支持全球复检"},
            ]
            
            for idx, d in enumerate(defaults):
                p = IndustryProfile()
                p.id = str(uuid.uuid4())
                p.name = d["name"]
                p.icon = d["icon"]
                p.product = d["product"]
                p.selling_point = d["selling_point"]
                p.created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._profiles.append(p)
                if idx == 0:
                    self._active_id = p.id
            
            logger.info("[CRM] 初始化了 10 个常见行业人设大礼包至数据库")
            self._save()

    def _save(self):
        """保存行业配置到 SQLite"""
        try:
            data = {
                "active_profile_id": self._active_id,
                "profiles": [p.to_dict() for p in self._profiles],
            }
            json_str = json.dumps(data, ensure_ascii=False)
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO bot_config (config_key, config_value, updated_at)
                    VALUES ('industry_config_data', ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(config_key) DO UPDATE SET 
                    config_value = excluded.config_value, 
                    updated_at = CURRENT_TIMESTAMP
                ''', (json_str,))
                conn.commit()
            logger.debug(f"[CRM] 保存行业配置成功")
        except Exception as e:
            logger.error(f"[CRM] 保存行业配置失败: {e}")

    def get_active_profile(self) -> Optional[IndustryProfile]:
        """获取当前激活的行业配置"""
        if not self._active_id:
            return self._profiles[0] if self._profiles else None
        for p in self._profiles:
            if p.id == self._active_id:
                return p
        return self._profiles[0] if self._profiles else None

    def get_all_profiles(self) -> List[IndustryProfile]:
        """获取所有行业配置"""
        return self._profiles.copy()

    def get_profile_by_id(self, profile_id: str) -> Optional[IndustryProfile]:
        for p in self._profiles:
            if p.id == profile_id:
                return p
        return None

    def switch_profile(self, profile_id: str) -> bool:
        for p in self._profiles:
            if p.id == profile_id:
                self._active_id = profile_id
                self._save()
                logger.info(f"[CRM] 切换行业: {p.icon} {p.name}")
                return True
        return False

    def create_profile(
        self,
        name: str,
        product: str,
        selling_point: str = "",
        persona: str = "",
        forbidden: str = "",
        knowledge: str = "",
        intensity: int = 2,
        icon: str = "🤖",
    ) -> IndustryProfile:
        profile = IndustryProfile()
        profile.id = f"profile_{uuid.uuid4().hex[:8]}"
        profile.name = name
        profile.icon = icon
        profile.created = datetime.now().strftime("%Y-%m-%d")
        profile.product = product
        profile.selling_point = selling_point
        profile.persona = persona
        profile.forbidden = forbidden
        profile.knowledge = knowledge
        profile.intensity = intensity

        self._profiles.append(profile)
        if len(self._profiles) == 1:
            self._active_id = profile.id

        self._save()
        logger.info(f"[CRM] 创建行业配置: {profile}")
        return profile

    def update_profile(self, profile_id: str, updates: dict) -> bool:
        for p in self._profiles:
            if p.id == profile_id:
                for key, value in updates.items():
                    if hasattr(p, key):
                        setattr(p, key, value)
                self._save()
                logger.info(f"[CRM] 更新行业配置: {p.name}")
                return True
        return False

    def delete_profile(self, profile_id: str) -> bool:
        for i, p in enumerate(self._profiles):
            if p.id == profile_id:
                self._profiles.pop(i)
                if self._active_id == profile_id:
                    self._active_id = (self._profiles[0].id if self._profiles else "")
                self._save()
                logger.info(f"[CRM] 删除行业配置: {p.name}")
                return True
        return False


INDUSTRY_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "car_insurance": {
        "name": "车险顾问",
        "icon": "🚗",
        "product": "汽车保险全流程服务",
        "selling_point": "交强险950起，商业险85折优惠",
        "persona": "专业车险顾问，亲切友善，有耐心",
        "forbidden": "不能承诺100%赔付，不能诋毁同行公司",
        "knowledge": ("交强险：5座私家车首年固定保费950元\n"
                      "商业险参考：三者100万约1500元，车损险按车价计算\n"
                      "新能源车保费通常比燃油车高10%-20%"),
        "intensity": 2,
    },
    "real_estate": {
        "name": "房产经纪人",
        "icon": "🏠",
        "product": "二手房买卖租赁服务",
        "selling_point": "专业评估，佣金透明，成交快",
        "persona": "资深房产经纪人，专业可信赖",
        "forbidden": "不能虚报房价，不能隐瞒房屋缺陷",
        "knowledge": "",
        "intensity": 3,
    },
    "beauty": {
        "name": "美妆顾问",
        "icon": "💄",
        "product": "进口护肤品代购",
        "selling_point": "正品保证，比专柜便宜30%",
        "persona": "闺蜜般的美妆顾问，懂护肤也懂你",
        "forbidden": "不能声称有医疗效果，不能贬低其他品牌",
        "knowledge": "",
        "intensity": 2,
    },
    "education": {
        "name": "教育顾问",
        "icon": "📚",
        "product": "K12教育培训课程",
        "selling_point": "名师辅导，提分有保障",
        "persona": "专业教育顾问，关心孩子成长",
        "forbidden": "不能承诺具体分数提升，不能贬低学校教育",
        "knowledge": "",
        "intensity": 2,
    },
    "fitness": {
        "name": "健身教练",
        "icon": "💪",
        "product": "私人健身训练课程",
        "selling_point": "科学训练计划，专属营养方案",
        "persona": "阳光专业的健身教练",
        "forbidden": "不能承诺具体减重数字，不能推荐药物",
        "knowledge": "",
        "intensity": 3,
    },
}
