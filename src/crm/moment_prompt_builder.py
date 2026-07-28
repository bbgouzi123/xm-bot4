"""
朋友圈及视频媒体提示词生成器 (MomentPromptBuilder)

抽离自 prompt_builder.py，用于遵守 300 行代码限额红线
"""
from typing import Optional
import random
from .industry_config import IndustryProfile


def build_moment_prompt(
    config: Optional[IndustryProfile],
    days: int = 3,
    seed: str = "",
    target_industry_name: str = "",
    daily_count: int = 1,
    wxid: str = None,
) -> str:
    """根据行业配置生成朋友圈图文矩阵 Prompt"""
    if config is None:
        return f"请为【通用营销】行业生成未来 {days} 天的朋友圈，且每天需要生成 {daily_count} 条不同的朋友圈。"

    is_xm_bot4 = (
        config.name == "xm-bot4系统" or 
        config.id == "sys_001"
    )

    day_by_day_hint = ""
    global_avoidance_instruction = ""
    target_industry_name_instruction = ""

    if is_xm_bot4:
        local_random = random.Random(seed) if seed else random.Random()
        if not target_industry_name:
            target_industry_name = local_random.choice([
                "餐饮门店", "服装零售", "教培招生", "汽车车险", "家装定制", 
                "美容养生", "同城货运", "数码回收", "商务企服", "母婴护理"
            ])

        product_hint = f"\n产品/服务：针对【{target_industry_name}】行业的 xm-bot4 AI自动营销数字员工系统"

        # 根据不同的目标行业，设计专属痛点和高价值卖点
        if any(kw in target_industry_name for kw in ["险", "车", "保险", "保"]):
            selling_hint = f"\n核心卖点：自动获取高意向车主客户，自动微信沟通车险报价，替代繁杂人工电销与微信跟进流程，缩短出单周期"
        elif any(kw in target_industry_name for kw in ["教", "学", "培", "训", "幼"]):
            selling_hint = f"\n核心卖点：微信自动裂变与加家长好友，智能解答课程安排，发送试听邀约并自动跟进，提升教培中心招生转化率"
        elif any(kw in target_industry_name for kw in ["零售", "批发", "商", "供"]):
            selling_hint = f"\n核心卖点：各渠道询单引流自动加好友，自动发送批发价格表，24小时在线解答招商加盟，全天候跟单提醒"
        elif any(kw in target_industry_name for kw in ["店", "餐", "饮", "美", "服"]):
            selling_hint = f"\n核心卖点：微信自动派发门店福利与裂变优惠券，提醒预约消费，促成老客高频复购与同城新客到店，减少闲置率"
        else:
            selling_hint = f"\n核心卖点：自动加老板微信进行专业企服介绍，提供智能常见问题解答与资质筛选"

        knowledge_hint = f"\n产品知识（请融入文案）：本系统是一款帮助【{target_industry_name}】商家实现24小时微信托管营销的数字员工。它能够代替真人进行多渠道获客、自动发送首招呼、智能探知客户意向，解决招销售贵、流失率高、跟进不及时等痛点。"

        # 针对目标行业的“痛点 - 作用 - 解决问题 - 实际成效”闭环写作结构
        target_industry_name_instruction = f"""
【目标行业 B2B 深度说服叙事框架】
在撰写针对【{target_industry_name}】行业的推广朋友圈文案时，请设计一个逻辑完整的微型营销闭环，但绝不能模板化：
1. 【行业真实细节】点明【{target_industry_name}】日常经营中某个真实的细节或小插曲（如：客户微信加了很久才通过、下班后收到咨询没及时回、销售离职带走微信号、频繁复制粘贴报价单等）。
2. 【解决方案】自然引出 AI 数字员工系统。说明它是如何在后台工作的（例如静默排队自动打招呼、7x24小时智能解答、多账号沙箱安全隔离等）。
3. 【成效闭环】说明它带来的效率提升（如：线索跟进零漏单、从无谓的工作中解脱出来、客户画像自动归档等），语气要踏实、克制、数据真实。
"""

        themes = [
            {"angle": "【痛点：招人贵留人难】", "focus": f"针对【{target_industry_name}】老板面临的人工客服成本高、流失快的痛点，算账对比数字员工（7x24小时在岗、低成本）与人工的投入产出比。", "forbidden": "熬夜,加人,加好友,翻3倍,获客神器,闭眼入,下班"},
            {"angle": "【业务细节：深夜流量承接与回复】", "focus": f"很多【{target_industry_name}】意向线索来自夜间，人工客服已下班。描述数字员工深夜秒级同意好友并解答、报价，锁死冷意向的真实状态。", "forbidden": "加好友,加人,熬夜,早下班,闭眼入"},
            {"angle": "【避坑：封号风险与防封技术】", "focus": f"科普为什么【{target_industry_name}】私域要防封。对比廉价协议挂，介绍xm-bot4采用UIA模拟真人打字点击的底层合规技术，稳定保障微信号安全。", "forbidden": "熬夜,加人,加好友,神器,翻3倍,下班"},
            {"angle": "【获客：自动通过与破冰话术】", "focus": f"客流进来时，AI数字员工秒级自动通过好友，并根据来源标签发送定制的破冰首招呼，展示高情商高效沟通细节。", "forbidden": "熬夜,加人,加好友,早下班,神器,闭眼入"},
            {"angle": "【精细化：智能画像与自动标签】", "focus": f"详述AI与【{target_industry_name}】客户聊天时，如何识别对方意向并自动打标签；遇到高意向咨询自动弹窗提醒人工跟进，体现人机协同。", "forbidden": "熬夜,加人,加好友,翻3倍,闭眼入,下班"},
            {"angle": "【协同：多号多开与沙箱隔离】", "focus": f"针对多账号运营的【{target_industry_name}】商家，说明系统如何利用安全沙箱技术实现单机10+多开且在统一台集中调度管理，突出抗风控大团队协同。", "forbidden": "熬夜,加好友,加人,神器,早下班"},
            {"angle": "【老友碎碎念：解放时间】", "focus": f"以老友口吻谈心：【{target_industry_name}】老板的时间极其宝贵，如果天天被琐碎的加人、打招呼、发朋友圈等手工活绑架，就没空提升核心业务。AI托管即是解脱。", "forbidden": "熬夜,加人,加好友,翻3倍,闭眼入,神器"},
            {"angle": "【品牌种草：朋友圈AI自动托管】", "focus": f"说明系统如何根据【{target_industry_name}】专属配置，自动规划30天发圈并智能配图，且自动在朋友圈点赞互动，用极低成本维护私域曝光与专业度。", "forbidden": "熬夜,加人,加好友,翻3倍,获客,闭眼入"},
            {"angle": "【服务保障：安心试用机制】", "focus": f"打消【{target_industry_name}】商家的疑虑：承诺3天全功能免费试用、7天无理由退款、工程师远程部署调优，展示踏实靠谱的伙伴担当。", "forbidden": "熬夜,加好友,加人,闭眼入,神器"},
            {"angle": "【对比：AI智能聊天 VS 普通群发】", "focus": f"科普为什么xm-bot4的AI内核在理解【{target_industry_name}】客户多轮复杂对话、处理异议时比普通群发复读机更像真人销冠，突出大模型核心壁垒。", "forbidden": "熬夜,加人,加好友,翻3倍,获客,闭眼入"}
        ]

        shuffled_themes = list(themes)
        local_random.shuffle(shuffled_themes)
        while len(shuffled_themes) < days * daily_count:
            more_themes = list(themes)
            local_random.shuffle(more_themes)
            shuffled_themes.extend(more_themes)

        day_by_day_instructions = []
        for d in range(1, days + 1):
            for c in range(daily_count):
                t_idx = (d - 1) * daily_count + c
                t = shuffled_themes[t_idx]
                day_by_day_instructions.append(
                    f"### 第 {d} 天第 {c + 1} 条朋友圈：\n"
                    f"  - 【主题/角度】：{t['angle']}\n"
                    f"  - 【创作侧重与要点】：{t['focus']}\n"
                    f"  - 【该条文案绝对禁用词】：{t['forbidden']}\n"
                )
        day_by_day_str = "\n".join(day_by_day_instructions)

        day_by_day_hint = f"""
【排期天数与各天朋友圈主题创作指导规划（强制遵循）】
请严格按照以下规划生成每一天的朋友圈。对于指定的每一天，必须按照对应的主题、侧重点 and 禁用词进行内容创作：
{day_by_day_str}
"""

        global_avoidance_instruction = """
【全局写作禁令：绝对禁止套路化/公式化营销】
1. 绝对不要在任何一天的朋友圈中使用以下套话：
   - 严禁出现“还在熬夜加客户？”、“熬夜加人吗”或类似问句。
   - 严禁出现“加人转化率翻3倍”、“业绩暴涨300%”等任何虚假翻倍或夸大宣传。
   - 严禁出现“闭眼入的获客神器”、“闭眼入”、“获客神器”等廉价促销词汇。
   - 严禁出现“老板们终于能早下班陪家人啦”、“提早下班陪家人”等温情/煽情词句。
2. 文案开篇绝不要总是以提问或感叹句开头，可以使用陈述句、场景描写或干货分享直接切入。
3. 语气要踏实、专业、真诚，像一个老练的技术顾问或私域运营专家在低调分享实操心得。
"""
    else:
        product_hint = f"\n产品/服务：{config.product}" if config.product else ""
        selling_hint = f"\n核心卖点：{config.selling_point}" if config.selling_point else ""
        knowledge_hint = f"\n产品知识（请融入文案）：{config.knowledge}" if config.knowledge else ""

    # 朋友圈专属参数
    style_hint = f"\n文案风格要求：{config.moment_style}" if config.moment_style else ""
    tone_hint = f"\n口吻调性：{config.moment_tone}" if config.moment_tone else ""
    keywords_hint = f"\n文案中必须自然植入的关键词：{config.moment_keywords}" if config.moment_keywords else ""
    forbidden_hint = f"\n绝对禁止使用的词汇：{config.moment_forbidden}" if config.moment_forbidden else ""
    image_style_hint = f"\n配图创意风格：{config.moment_image_style}" if config.moment_image_style else ""
    hashtags_hint = f"\n每条文案末尾请带上标签：{config.moment_hashtags}" if config.moment_hashtags else ""

    # 媒体生成偏好
    media_pref = getattr(config, "moment_media_type", "image")
    if media_pref == "video":
        media_pref_hint = "\n媒体生成偏好：全部生成【短视频朋友圈】。请确保返回的每一天朋友圈媒体类型都是视频，用于驱动视频生成模型。"
    elif media_pref == "mixed":
        media_pref_hint = "\n媒体生成偏好：【智能混合生成图文或短视频】。请根据文案内容，交替规划生成图片朋友圈（适合干货避坑等）或视频朋友圈（适合动态展示、镜头动作等）。"
    else:
        media_pref_hint = "\n媒体生成偏好：全部生成【精致图文配图朋友圈】。朋友圈媒体类型全部指定为图片。"

    # 解析国家与风格配置
    country = getattr(config, "moment_image_country", "CN")
    scene_type = getattr(config, "moment_image_scene_type", "real_scene")

    country_hint_map = {
        "CN": "场景归属国家/地域：中国（画面中出现的建筑、人物面孔、路牌招牌、车牌、室内陈设等细节必须完全符合中国大陆的本土真实特征，面孔为亚洲人，招牌若有文字需使用简体中文，禁止出现任何欧美文字、境外车牌或异域风情街景）",
        "US_EU": "场景归属国家/地域：欧美/国际化（画面中出现的建筑、人物面孔、街景等符合欧美/国际化都市特色，面孔为白人或多元化面孔，招牌使用英文，体现出欧美发达国家或现代都市的气息）",
        "JP_KR": "场景归属国家/地域：日韩（画面中出现的建筑、街道等符合日韩特征，带有清新、原木或极简的日式与韩式风情，面孔为日韩面孔）",
        "SEA": "场景归属国家/地域：东南亚（画面中出现的建筑、街景、植被符合东南亚热带特色，带有泰越马印等东南亚异域风情，面孔为东南亚族裔）",
        "ME": "场景归属国家/地域：中东（画面中出现的建筑多为阿拉伯或波斯特色，包含沙漠、绿洲、清真寺或奢华中东都市背景，面孔为中东族裔）",
        "LATAM": "场景归属国家/地域：拉丁美洲（画面色彩鲜明热情，建筑符合拉美城市或热带雨林风格，面孔为拉美/西班牙裔面孔）",
        "AFRICA": "场景归属国家/地域：非洲（画面包含热带稀树草原、旷野自然风光或非洲本土城市背景，面孔为非裔面孔）",
        "RU": "场景归属国家/地域：俄罗斯/东欧（画面包含俄罗斯、东欧古典建筑或冰雪针叶林景观，面孔为东欧族裔面孔）",
        "SA": "场景归属国家/地域：南亚（画面包含南亚/印度特色古老或现代建筑，街景热闹，色彩斑斓，面孔为南亚/印度族裔面孔）",
        "GLOBAL": "场景归属国家/地域：全球通用（不受特定地域限制，风格中立，兼容全球主流商业场景）"
    }
    scene_type_hint_map = {
        "real_scene": "配图艺术风格类型：真实行业场景（追求写实、逼真，画面必须有强烈的现实生活抓拍感，像用手机或 iPhone 镜头直出抓拍的真实工作、产品或服务画面，拒绝虚假死板的商业宣传海报，避免AI塑料质感）",
        "comic": "配图艺术风格类型：手绘漫画风格（采用手绘、趣味漫画、二次元风格，色彩明快，线条清晰，富有创意与趣味性）",
        "abstract": "配图艺术风格类型：扁平插画/抽象风格（采用抽象几何图形、扁平化插画矢量风格，用高概念、艺术化的抽象视觉传达品牌信息，适合表达科技、策略、概念性强的主题）",
        "3d_render": "配图艺术风格类型：3D 渲染立体风格（采用 3D C4D 渲染、立体建模风格，具有立体空间感、磨砂质感和现代电商海报设计感，色彩饱满，适合展示新颖的产品与服务）"
    }

    country_hint = country_hint_map.get(country, country_hint_map["CN"])
    scene_type_hint = scene_type_hint_map.get(scene_type, scene_type_hint_map["real_scene"])

    # 无人办公与产品截图配图风格约束
    if is_xm_bot4:
        if target_industry_name:
            office_style_hint = f"""
【重要配图风格与提示词指导】
为了确保生图质量，当 media_type 为 "image" 时，设计的 `image_prompt` 必须符合以下要求：
1. 【中国本土行业客户】：场景必须定位在中国本土（出镜的客户和从业人员必须是具有中国本土亲切感的亚洲人面孔，背景文字若包含中文必须为简体中文，禁止出现欧美化人像细节）。
2. 【{scene_type_hint}】：追求真实、质朴的生活与工作抓拍镜头，拒绝死板宣传图，杜绝过度渲染的 AI 塑料质感。
3. 【解决行业客户真实痛点的场景融合】：设计的 `image_prompt` 绝对不要再千篇一律生成冰冷的电脑机房或只显示 xm-bot4 软件后台。你必须设计展现解决【{target_industry_name}】行业客户真实痛点、体现业务高效或成果落地的高清写实实景图。
   例如：
   - 若面向装修/全屋定制：针对“设计方案沟通慢、落地差”痛点，描述阳光透入的精美中国普通住宅样板间内，一位精干 of 中国男设计师正拿着平板向一对年轻的中国业主夫妇讲解设计方案，夫妇面带笑容、频频点头的温情实景抓拍；
   - 若面向保险/理财：针对“信任度低、产品条文难懂”痛点，描述在明亮温馨的现代化中式客厅中，一位热情的中国理财规划师正与一对中年中国夫妇坐在沙发上，桌上放着茶杯和保障方案书，理财师用笔细心勾画，夫妇眼神专注、神情轻松的互动抓拍；
   - 若面向教培/招生：针对“生源流失、课后辅导家长焦虑”痛点，描述在光线充足的中国辅导班教室里，一位温和的年轻女老师正弯下腰耐心解答一位中国小学生的作业问题，背景是整齐的书架，充满温度的镜头；
   - 若面向零售/商贸：针对“库存积压、爆单发货手忙脚乱”痛点，描述在井然有序的中国中小型网店仓库发货区，两位中国店员正忙着打包快递，背景是整齐摆满各类本土零售商品的货架，体现高效忙碌且有秩序的实景；
   - 若面向其他企业服务：针对“老板不懂资质代办、跑断腿”痛点，描述采光良好的中国商务会客室中，服务顾问递给中国企业老板一份带有简体中文印章的证书，老板双手接过、面露喜色与释怀神情的交谈画面。
请根据你为该天朋友圈文案设计的具体痛点解决闭环，创作出具有生活气息、人情味、最能直击【{target_industry_name}】行业客户痛点缓解的中国本土真实场景图提示词。不要包含 xm-bot4 英文单词。
"""
        else:
            office_style_hint = f"""
【重要配图风格与提示词指导】
为了确保生图质量，当 media_type 为 "image" 时，设计的 `image_prompt` 必须符合以下要求：
1. 【中国本土行业客户】：展现中国本土职场和客户的风貌（简体中文标识、中国白领/老板的亚洲面孔）。
2. 【{scene_type_hint}】。
请根据当前文案探讨的企业智能化获客、24小时数字员工上班场景，设计生动、具有中国本土写字楼生活温度的办公抓拍：例如深夜里一间温馨的中国企业办公室内，灯光温暖地照在一个整洁 of 办公位上（上面放着水杯、记事本，电脑屏幕上亮着正在运行自动化工作的简体中文控制台，背景是拉上窗帘的安静夜景，表现数字员工深夜替老板默默加班的温馨写实抓拍），拒绝冰冷科幻的电脑机房。
"""
    else:
        product_name = config.product or config.name or "产品"
        office_style_hint = f"""
【重要配图风格与提示词指导】
为了确保生图质量，当 media_type 为 "image" 时，设计的 `image_prompt` 必须符合以下要求：
1. 【中国本土行业客户】：展现中国本土职场 and 客户的风貌（简体中文标识、中国白领/老板的亚洲面孔）。
2. 【{scene_type_hint}】。
请根据文案的主题将上述场景与艺术风格融入 `image_prompt` 中，描述一个符合该行业特色且具备对应风格质感的画面，绝对不要生成与“xm-bot4系统”或本行业无关的内容，也绝对不要包含任何 xm-bot4 的字眼。
"""

    seed_hint = ""
    if seed:
        seed_hint = f"""
【多样性创意随机种子：{seed}】
请以此随机种子作为创作多样性的核心。在生成文案和图片提示词时，必须完全随机化话题切入角度（如：避坑经验、效率提升、数字员工对比真人优势、日常智能场景模拟等）、文案配图创意等，确保多次生成同一个行业的排期时内容完全不重样、不雷同。
"""

    industry_name = config.name or "通用营销"
    if is_xm_bot4 and target_industry_name:
        header = f"请为【{industry_name}】（微信自动营销数字员工系统）生成面向【{target_industry_name}】行业的推广朋友圈排期计划，展示该系统如何帮助【{target_industry_name}】商家/企业自动加人、智能聊天与获客转化。文案和配图创意均需要围绕该目标行业展开。总共需要为未来 {days} 天生成朋友圈排期，且【每天需要生成 {daily_count} 条不同的朋友圈排期】。"
    else:
        header = f"请为【{industry_name}】行业生成未来 {days} 天的朋友圈排期计划，且【每天需要生成 {daily_count} 条不同的朋友圈排期】。"

    diversity_instruction = """
【重要写作约束：杜绝套话、严禁模板化表达】
1. 严禁在不同天数的朋友圈中重复相同的表达套句（例如：连续出现“一台电脑顶3个销售”、“省下万元成本”等完全相同的词句），必须对价值表达进行同义词替换或代入具体业务场景。
2. 每一天的朋友圈开头、语气结构和探讨话题必须完全不同。拒绝公式化写作，建议交替使用以下角度：
   - 痛点反思（如：老板每天加班加人累到怀疑人生，为何不让AI数字员工来代劳？）
   - 趣味场景（如：凌晨2点还在自动给客户报价，这届数字员工卷哭了人工销售）
   - 经营干货/避坑指南（如：分析该目标行业私域加人被加不上的致命原因，并给出数字员工的自动化解法）
3. 文案要求语气真诚、生动自然，像一个熟悉该目标行业的老板或资深运营专家的口吻发的朋友圈，融入2-3个精致Emoji，字数控制在 80 字以内。
"""

    # 融入激活的销冠话术包参考，使朋友圈文案具备金牌销冠情商调性
    sales_hint = ""
    try:
        active_sales_pkg_id = ""
        if wxid:
            try:
                from src.crm.account_data import get_account_settings
                active_sales_pkg_id = get_account_settings(wxid).get("sales_package_id", "")
            except Exception:
                pass
        if not active_sales_pkg_id:
            from src.utils.config_cache import config_cache
            global_configs = config_cache.get("global_api_config") or {}
            active_sales_pkg_id = global_configs.get("active_sales_package_id", "")

        if active_sales_pkg_id:
            from .chat_knowledge_prompt import build_chat_knowledge_section
            sales_section = build_chat_knowledge_section("", wxid=wxid)
            if sales_section:
                sales_hint = f"\n\n【可参考的成交销冠高情商话术调性库】{sales_section.strip()}\n请在撰写朋友圈文案和配图创意时，参考并融入上述成交销冠包的话术人设、痛点切入和高情商应对风格。"
    except Exception:
        pass

    prompt = f"""{header}
{product_hint}{selling_hint}{knowledge_hint}{style_hint}{tone_hint}{keywords_hint}{forbidden_hint}{image_style_hint}{hashtags_hint}{media_pref_hint}{office_style_hint}{seed_hint}{sales_hint}
{diversity_instruction}
{target_industry_name_instruction if is_xm_bot4 and target_industry_name else ""}
{day_by_day_hint}
{global_avoidance_instruction}

【重要输出格式约束】
你必须且只能输出一个最纯净的 JSON 数组，绝对不能包含任何 markdown 代码块包裹标记 (例如 ```json ) 或首尾多余话术。整个回复必须以 "[" 开头，以 "]" 结尾。
数组中每一天的数据必须且只能包含以下 JSON 字段：
- "day_offset": 整数，表示第几天 (从 1 开始)。注意，因为每天生成 {daily_count} 条不同的排期，所以请在数组中为同一天输出 {daily_count} 个 JSON 对象，其 "day_offset" 设为相同的值。
- "text": 朋友圈文案，控制在 80 字以内，带 2-3 个 Emoji，口吻自然，融入产品卖点与知识
- "media_type": 字符串，指定该条朋友圈的配图/视频类型，必须为 "image"（图片）或 "video"（短视频）之一
- "media_urls": 列表，若在同步后端智能体且有媒体生成插件权限，放入生成的真实图片/视频 URL；否则请置为空列表 []
- "image_prompt": 字符串，为该朋友圈设计的 AI 创作提示词。若 media_type 是 "image"，请给出符合上述写实无人办公环境与显示软件截图标准的生图提示词；若 media_type 是 "video"，请给出高清短视频创意与运镜描述提示词
- "industry_tag": 行业名称
"""
    return prompt.strip()


def build_video_prompt(
    config: Optional[IndustryProfile],
    duration: int = 30,
) -> str:
    """根据行业配置生成视频素材 Prompt（预留接口）"""
    if config is None:
        return f"请为【通用营销】行业生成一个 {duration} 秒的朋友圈短视频脚本。"

    product_hint = f"\n产品/服务：{config.product}" if config.product else ""
    selling_hint = f"\n核心卖点：{config.selling_point}" if config.selling_point else ""

    return f"""请为【{config.name}】行业生成一个 {duration} 秒的朋友圈短视频脚本。
{product_hint}{selling_hint}
""".strip()
