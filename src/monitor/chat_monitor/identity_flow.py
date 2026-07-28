import logging
import asyncio
from typing import Optional, Any
from src.utils.db_manager import WeChatDBManager
from src.crm.profile_manager import ProfileManager
from src.crm.tag_manager import TagEntry

logger = logging.getLogger(__name__)

# 内存中记录每个微信好友当前的分流盘问状态
# Key 为 f"{account_id}:{session_name}"，Value 为 "ASKED"
IDENTITY_FLOW_STATES = {}


def resolve_qrcode_path(qrcode_path: str) -> Optional[str]:
    """
    自适应二维码图片路径解析：
    1. 若是本机的物理绝对路径且存在，直接使用；
    2. 若是相对文件名，去本地系统的 uploads 目录下寻找并返回其绝对路径；
    3. 若是 http/https 开头的 OSS 云端 URL 链接，则自动下载缓存到本地临时目录并返回其路径。
    """
    if not qrcode_path:
        return None
        
    import os
    from pathlib import Path
    
    # 1. 物理绝对路径直通车
    try:
        if os.path.isabs(qrcode_path) and os.path.exists(qrcode_path):
            return qrcode_path
    except Exception:
        pass
        
    # 2. 匹配本地统一上传文件夹
    try:
        upload_dir = Path.home() / '.xm-ai-bot' / 'uploads'
        local_uploaded_path = upload_dir / os.path.basename(qrcode_path)
        if local_uploaded_path.exists():
            return str(local_uploaded_path)
    except Exception:
        pass
        
    # 3. 匹配云端/OSS 网址，自动下载并放入缓存中
    if qrcode_path.startswith("http://") or qrcode_path.startswith("https://"):
        try:
            import urllib.request
            import tempfile
            import uuid
            
            ext = os.path.splitext(qrcode_path)[1] or ".png"
            if "?" in ext:
                ext = ext.split("?")[0]
                
            temp_dir = Path.home() / '.xm-ai-bot' / 'temp'
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            download_dest = temp_dir / f"download_qr_{uuid.uuid4().hex[:8]}{ext}"
            
            # 使用 HTTP 头避免被防火墙拦截，直接拉取二维码文件
            req = urllib.request.Request(
                qrcode_path, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                with open(download_dest, 'wb') as out_file:
                    out_file.write(response.read())
                    
            if download_dest.exists() and download_dest.stat().st_size > 0:
                logger.info(f"[二维码下载] 成功下载云端二维码至本地缓存: {qrcode_path} -> {download_dest}")
                return str(download_dest)
        except Exception as dl_err:
            logger.warning(f"[二维码下载] 从 URL {qrcode_path} 下载二维码图片发生异常: {dl_err}")
            
    return None


async def handle_identity_routing_flow(engine: Any, session_name: str, message: str, is_group: bool, account_id: str) -> Optional[dict]:
    """
    处理新友身份引导与拉群分流控制流。
    如果需要接管并回复，返回 {"reply": "回复的话术文本"}。
    如果不需要拦截（已有身份或功能未启用），返回 None。
    """
    if is_group:
        return None

    db = WeChatDBManager()
    cfg = db.get_identity_routing()
    if not cfg or not cfg.get("enabled"):
        return None

    rules = cfg.get("rules", [])
    if not rules:
        return None

    # 1. 查找此好友当前是否已具备任何规则定义的身份标签
    profile_mgr = ProfileManager(account_id=account_id)
    
    # 优先从通讯录缓存获取真实 wxid，避免使用临时 nickname 创建重复画像
    contact_wxid = session_name
    try:
        from src.utils.contacts_cache import contacts_cache
        all_friends = contacts_cache.get_friends(account_id)
        found_wxid = next((f.get("wxid", "") for f in all_friends if (f.get("name") or "").strip() == session_name.strip() or (f.get("remark") or "").strip() == session_name.strip()), "")
        if found_wxid:
            contact_wxid = found_wxid
    except Exception:
        pass

    profile = profile_mgr.get_profile(contact_wxid, nickname=session_name)
    
    # 获取该好友当前所有的小类标签值
    existing_tags = set()
    if profile and profile.tags:
        for t in profile.tags:
            val = getattr(t, 'value', '') or (t.get('value', '') if isinstance(t, dict) else '')
            if val:
                existing_tags.add(val)
    
    # 规则中定义的标签列表
    rule_tags = {r.get("tag_name") for r in rules if r.get("tag_name")}

    # 如果该好友已经有了规则里的任何一个身份标签，直接跳过分流引导，正常走 AI/关键词
    if existing_tags.intersection(rule_tags):
        return None

    session_key = f"{account_id}:{session_name}"
    current_state = IDENTITY_FLOW_STATES.get(session_key)
    clean_msg = message.strip()
    matched_rule = None

    continuous_detection = bool(cfg.get("continuous_detection", False))

    if continuous_detection:
        # 动态对话打标模式：只在好友主动说出匹配关键词时触发打标与拉群，日常对话不打扰
        for r in rules:
            keywords = r.get("keywords", [])
            for kw in keywords:
                if kw.strip() and (kw.strip() in clean_msg):
                    matched_rule = r
                    break
            if matched_rule:
                break
        
        if not matched_rule:
            # 日常闲聊不匹配关键词，正常放行，走 AI 聊天
            return None
    else:
        # 首次主动提问模式
        if not current_state:
            # 初始状态：说明是无标签的新消息，机器人开始主动问
            IDENTITY_FLOW_STATES[session_key] = "ASKED"
            logger.info(f"[身份引导] 好友 {session_name} 无身份标签，启动盘问，发送: {cfg.get('ask_prompt')}")
            return {"reply": cfg.get("ask_prompt")}

        # 如果处于 ASKED 状态，说明正在等用户回答。我们尝试匹配用户回答
        for r in rules:
            keywords = r.get("keywords", [])
            for kw in keywords:
                if kw.strip() and (kw.strip() in clean_msg):
                    matched_rule = r
                    break
            if matched_rule:
                break

        if not matched_rule:
            # 匹配失败，发送降级引导语
            logger.info(f"[身份引导] 好友 {session_name} 输入没能识别身份，发送降级话术")
            return {"reply": cfg.get("fallback_prompt")}

    # 匹配成功！进行打标和入群分流操作
    tag_name = matched_rule.get("tag_name")
    group_name = matched_rule.get("group_name")
    backup_group = matched_rule.get("backup_group_name", "")
    join_method = matched_rule.get("join_method", "qrcode")
    qrcode_path = matched_rule.get("qrcode_path", "")

    logger.info(f"[身份引导] 好友 {session_name} 成功识别身份: {tag_name}，分流方案: {join_method}")

    # 1. 给微信好友打上身份标签 (数据库记录)
    try:
        tag_entry = TagEntry(
            category="business",
            subcategory="intent",
            value=tag_name,
            confidence=1.0,
            source="chat"
        )
        profile_mgr.update_tags(contact_wxid, [tag_entry], source="chat", nickname=session_name)
        logger.info(f"[身份引导] 已为好友 {session_name} 自动贴上标签: {tag_name}")
    except Exception as tag_ex:
        logger.error(f"[身份引导] 为好友打标签发生异常: {tag_ex}", exc_info=True)

    # 2. 清理盘问状态
    IDENTITY_FLOW_STATES.pop(session_key, None)

    # 获取模板
    tpl_success = cfg.get("invite_success_reply", "")
    tpl_fail = cfg.get("invite_fail_reply", "")

    # 解析获得二维码的真实物理路径
    real_qrcode_path = resolve_qrcode_path(qrcode_path)

    # 准备后续物理动作数据
    identity_action = {
        "tag_name": tag_name,
        "group_name": group_name,
        "backup_group_name": backup_group,
        "join_method": join_method,
        "qrcode_path": real_qrcode_path or qrcode_path,
        "invite_fail_reply": tpl_fail
    }

    import os
    # 仅当 qrcode 模式，且二维码路径存在时，直接发送群二维码
    if join_method == "qrcode" and real_qrcode_path:
        success_reply = f"已收到！已为您贴上【{tag_name}】身份标签，请识别下方发送的群二维码加入交流群~"
        if tpl_success.strip():
            success_reply = tpl_success.replace("{tag_name}", tag_name).replace("{group_name}", group_name or "")
        return {"reply": success_reply, "file_to_send": real_qrcode_path, "identity_action": identity_action}

    # 如果是拉群模式
    if group_name:
        reply_text = ""
        if tpl_success.strip():
            reply_text = tpl_success.replace("{tag_name}", tag_name).replace("{group_name}", group_name)
        return {"reply": reply_text, "identity_action": identity_action}

    # 兜底
    reply_text = ""
    if tpl_success.strip():
        reply_text = tpl_success.replace("{tag_name}", tag_name).replace("{group_name}", "")
    else:
        reply_text = f"已收到！已为您贴上【{tag_name}】身份标签。欢迎加入我们！"
    return {"reply": reply_text, "identity_action": identity_action}
