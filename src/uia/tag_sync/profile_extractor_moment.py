import logging
from src.crm.profile_manager import ProfileManager
from src.crm.customer_profile import CustomerProfile
from src.crm.tag_manager import TagEntry

logger = logging.getLogger(__name__)

def extract_and_sync_profile_from_moment(driver, profile_win, author_name: str):
    """头像点击后打开的名片弹窗，在这里提取用户资料并完善通讯录"""
    try:
        # 1. 提取资料
        details = {
            "nickname": author_name,
            "wxid": "",
            "region": "",
            "signature": "",
            "source": "",
            "remark": "",
            "tags": [],
        }
        
        queue = [(profile_win, 0)]
        max_depth = 12
        visited_count = 0
        
        while queue:
            ctrl, depth = queue.pop(0)
            visited_count += 1
            if depth > max_depth or visited_count > 400:
                continue
            
            try:
                name = (ctrl.Name or "").strip()
                cls = ctrl.ClassName or ""
                ctype = ctrl.ControlTypeName or ""
                
                # 提取微信号
                if ctype == "TextControl" and name in ("微信号：", "微信号:"):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        details["wxid"] = (sibling.Name or "").strip()
                elif "微信号：" in name or "微信号:" in name:
                    details["wxid"] = name.split("：")[-1].split(":")[-1].strip()
                
                # 地区
                if ctype == "TextControl" and name in ("地区：", "地区:"):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        details["region"] = (sibling.Name or "").strip()
                
                # 来源（双通道兼容：支持老版冒号匹配 + 兄弟节点深挖 + 智能来源内容特征提取）
                if ctype == "TextControl" and name in ("来源：", "来源:", "来源"):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        sibling_name = (sibling.Name or "").strip()
                        # 如果兄弟节点没有名字，尝试在兄弟的子孙节点中深挖文本
                        if not sibling_name:
                            try:
                                import uiautomation as uia_lib
                                for sub_c, _d in uia_lib.WalkControl(sibling, maxDepth=3):
                                    if sub_c.ControlTypeName == "TextControl" and sub_c.Name:
                                        sibling_name = sub_c.Name.strip()
                                        break
                            except Exception:
                                pass
                        if sibling_name:
                            details["source"] = sibling_name
                
                # 🌟 特征文本智能匹配：直接在整个节点树中捞取符合微信好友来源格式的文本（微信来源特征句式）
                if ctype == "TextControl" and not details["source"]:
                    # "通过xxx添加", "来自xxx", "通过xxx导入", "通过xxx分享"
                    if (name.startswith("通过") and any(k in name for k in ("添加", "分享", "导入", "推荐", "扫一扫"))) or \
                       (name.startswith("来自") and len(name) < 40):
                        details["source"] = name
                        logger.info(f"[朋友圈资料抓取] 通过文案特征智能匹配到来源 => 来源: {name}")
                        
                # 个性签名
                if ctype == "TextControl" and name in ("个性签名：", "个性签名:"):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        details["signature"] = (sibling.Name or "").strip()
                        
                # 备注（双通道兼容：支持老版 TextControl + 新版 mmui::XLineField 备注输入域）
                if ctype == "TextControl" and name in ("备注名：", "备注名:", "备注：", "备注:"):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        details["remark"] = (sibling.Name or "").strip()
                else:
                    auto_id = ""
                    try:
                        auto_id = getattr(ctrl, "AutomationId", "") or ""
                    except Exception:
                        pass
                    if "remark_line" in auto_id or cls == "mmui::XLineField":
                        if name and name != "添加备注名":
                            details["remark"] = name
                            logger.info(f"[朋友圈资料抓取] 成功匹配新版微信备注特征 => 备注: {name}")
                        
                # 标签（双通道兼容：支持老版 TextControl + 新版 mmui::XMouseEventView 鼠标标签视图）
                if ctype == "TextControl" and name in ("标签：", "标签:"):
                    sibling = ctrl.GetNextSiblingControl()
                    if sibling:
                        tags_str = (sibling.Name or "").strip()
                        if tags_str:
                            details["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]
                else:
                    full_desc = ""
                    try:
                        full_desc = getattr(ctrl, "FullDescription", "") or ""
                    except Exception:
                        pass
                    if "修改标签" in full_desc or cls == "mmui::XMouseEventView" or name == "修改标签":
                        if name and name not in ("修改标签", "添加标签", "标签"):
                            import re
                            parsed_tags = [t.strip() for t in re.split(r'[,;，；\s]+', name) if t.strip()]
                            if parsed_tags:
                                details["tags"] = parsed_tags
                                logger.info(f"[朋友圈资料抓取] 成功匹配新版微信标签特征 => 标签: {parsed_tags}")
                            
                if depth < max_depth:
                    for child in ctrl.GetChildren():
                        queue.append((child, depth + 1))
            except Exception:
                continue

        # 如果没有提取到微信号，用 nickname
        if not details["wxid"]:
            details["wxid"] = f"nick_{author_name}"
            
        print(f"[朋友圈资料抓取] 成功获取用户 {author_name} 资料: {details}")
        
        # 2. 完善通讯录 (CRM ProfileManager)
        bot_wxid = getattr(driver, "bot_wxid", None) or getattr(driver, "_wxid", "main")
        pm = ProfileManager(account_id=bot_wxid)
        
        profile = pm.get_profile(details["wxid"])
        if not profile:
            for p in pm.get_all_profiles():
                if p.nickname == author_name or p.remark == details["remark"]:
                    profile = p
                    break
        
        tags_objs = []
        for t in details["tags"]:
            tags_objs.append(TagEntry(category="interest", subcategory="tag_sync", value=t, confidence=0.9, source="moments"))
            
        if profile:
            profile.nickname = details["nickname"] or profile.nickname
            profile.remark = details["remark"] or profile.remark or details["nickname"]
            profile.region = details["region"] or profile.region
            profile.signature = details["signature"] or profile.signature
            profile.source = details["source"] or profile.source
            # 合并标签
            current_tags = {t.value for t in profile.tags}
            for t in tags_objs:
                if t.value not in current_tags:
                    profile.tags.append(t)
            pm.save_profile(profile)
            print(f"[朋友圈资料抓取] 更新 CRM 联系人资料成功: {profile.nickname}")
        else:
            new_profile = CustomerProfile(details["wxid"])
            new_profile.nickname = details["nickname"]
            new_profile.remark = details["remark"] or details["nickname"]
            new_profile.region = details["region"]
            new_profile.signature = details["signature"]
            new_profile.source = details["source"]
            new_profile.tags = tags_objs
            pm.save_profile(new_profile)
            print(f"[朋友圈资料抓取] 新增 CRM 联系人资料成功: {new_profile.nickname}")
            
    except Exception as ex:
        logger.error(f"[朋友圈资料抓取] 提取并更新用户资料异常: {ex}")
