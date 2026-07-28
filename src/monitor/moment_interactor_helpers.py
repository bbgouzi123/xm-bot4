"""
朋友圈互动辅助函数 — 从 moment_interactor.py 拆分（遵循 300 行规范）

包含：
- _is_btn_visible: 检测按钮可见性（对齐竞品底部50px算法）
- _find_toast: 查找互动浮层（支持新旧版微信）
- _intel_radar: 情报雷达，抓取动态信息落库
- _sync_tags: 同步待打标签
- _ai_precheck: AI 预判是否需要互动（对齐竞品"无需评论跳过整条"逻辑）
- _click_interaction_area_adaptive: 分辨率自适应像素点击互动区域
"""
import logging
import time

logger = logging.getLogger(__name__)


def _is_btn_visible(btn, list_rect) -> bool:
    """检测评论/赞按钮是否在列表可见区域内。

    对齐竞品 _is_item_fully_visible 算法（L1252-1279）：
    - 只检查 item 底部约 50px 条带是否在容器可见范围内
    - 因为评论触发区（两个小点）位于 item 右下角，只要底部50px可见即可点击
    - 交集必须 > min_visible_w=15 且 > min_visible_h=40 才算"真正可见"
    （旧算法检查整个按钮矩形 → 过于严格，导致大量 item 被错误跳过）
    """
    try:
        br = btn.BoundingRectangle
        # 取底部 50px 条带（按钮区域 ≈ item 底部）
        strip_height = 50
        strip_top    = max(br.top, br.bottom - strip_height)
        strip_bottom = br.bottom
        strip_left   = br.left
        strip_right  = br.right

        # 与列表可见区域求交集
        inter_left   = max(strip_left,   list_rect.left)
        inter_top    = max(strip_top,    list_rect.top)
        inter_right  = min(strip_right,  list_rect.right)
        inter_bottom = min(strip_bottom, list_rect.bottom)

        inter_w = inter_right  - inter_left
        inter_h = inter_bottom - inter_top
        return inter_w > 15 and inter_h > 40
    except Exception:
        return False


def _click_interaction_area_adaptive(item) -> bool:
    """分辨率自适应像素点击朋友圈互动区域（对齐竞品 _click_item_comment_area L1281-1305）。

    竞品实测偏移量：
    - 常规屏：dx=40~50, dy=10~15 (距 item 右/底部)
    - 4K 屏 (sh>=2160)：dx=55~65（更大偏移）
    - 2560×1600：x 再减 5px 修正
    """
    try:
        import random
        import win32api
        from src.uia.retry import physical_click

        rect = item.BoundingRectangle
        if not rect or rect.right <= rect.left or rect.bottom <= rect.top:
            return False

        # 基础偏移（对齐竞品 dx=40~50, dy=10~15）
        dx = random.randint(40, 50)
        dy = random.randint(10, 15)

        # 分辨率自适应修正（对齐竞品 L1292-1301）
        try:
            sw = win32api.GetSystemMetrics(0)   # 屏幕宽度
            sh = win32api.GetSystemMetrics(1)   # 屏幕高度
            if sh >= 2160:
                # 4K 屏：偏移更大
                dx = random.randint(55, 65)
            if sw == 2560 and sh == 1600:
                # 2560×1600 特殊修正
                dx = dx + 5   # 等效 x = right - dx - 5
        except Exception:
            sw, sh = 0, 0

        x = rect.right  - dx
        y = rect.bottom - dy

        logger.debug(f"[互动] 分辨率自适应像素点击: ({x},{y}) [dx={dx} dy={dy}]")
        physical_click(x, y, settle=random.uniform(0.1, 0.2))
        return True
    except Exception as e:
        logger.debug(f"[互动] 像素坐标点击互动区域失败: {e}")
        return False


from src.monitor.moment_patrol_utils import _find_toast


# _intel_radar / _sync_tags 已迁移到 moment_interact_utils.py（保证300行规范）
from src.monitor.moment_interact_utils import _intel_radar, _sync_tags  # noqa: F401

# _ai_precheck 已迁移到 moment_ai_precheck.py（保证300行规范）
from src.monitor.moment_ai_precheck import ai_precheck as _ai_precheck  # noqa: F401


def _process_single_moment_item(
    item, item_idx, manager, sns_window, list_ctrl,
    settings, account_id, bot_nickname,
    processed_moments, user_comment_counts,
    per_friend_limit,
    like_count, comment_count, daily_like_limit, daily_comment_limit,
    should_like, should_comment, skip_self, skip_ads,
    cur_like_prob, cur_comment_prob, screen_idx,
):
    """处理单条朋友圈动态条目。
    
    返回: (stop_this_round, interacted_delta, liked_delta, commented_delta)
    stop_this_round=True 表示发现旧动态应就此终止本屏。
    """
    import random
    import time
    from src.uia.retry import random_delay, try_click, physical_click
    from src.uia.input_guard import UIAInterruptError
    from src.utils.stop_signal import stop_signal
    from src.utils.moment_config import (
        generate_moment_fingerprint, has_interacted, is_moment_interact_excluded,
        human_delay,
    )
    from src.monitor.moment_utils import (
        parse_moment_item, parse_publish_timestamp,
        click_interaction_area, dismiss_popup,
    )

    from src.utils.safe_uia import safe_control_type, safe_get_name

    try:
        ctrl_type = safe_control_type(item)
        if ctrl_type != 'ListItemControl':
            logger.info(f"[朋友圈·条目{item_idx}] 非ListItem({ctrl_type})，跳过")
            return False, 0, 0, 0
    except Exception:
        return False, 0, 0, 0

    item_name = ""
    try:
        item_name = safe_get_name(item)
    except Exception:
        logger.info(f"[朋友圈·条目{item_idx}] Name读取失败，跳过")
        return False, 0, 0, 0
    ns = item_name.strip()
    if not ns:
        logger.info(f"[朋友圈·条目{item_idx}] Name为空，跳过")
        return False, 0, 0, 0

    # 过滤系统提示条目（非用户动态），避免"余下N条"/"新消息"等被当作昵称
    import re as _re
    _SYSTEM_HINTS = ('余下', '新消息', '以上是', '没有更多', '加载中', '刷新', '评论区')
    if (any(ns.startswith(h) for h in _SYSTEM_HINTS)
            or _re.match(r'^[\d\s条个]+$', ns)
            or _re.search(r'余下\d+条', ns)):
        logger.info(f"[朋友圈] 跳过系统提示条目: {ns[:30]}")
        return False, 0, 0, 0

    logger.info(f"[朋友圈·条目{item_idx}] Name: {ns[:80]}")

    parsed = parse_moment_item(item_name)
    if not parsed:
        if ":" in item_name or "：" in item_name:
            sep = ":" if ":" in item_name else "："
            parts = item_name.split(sep, 1)
            parsed = {'publisher': parts[0].strip(),
                      'content': parts[1].strip() if len(parts) > 1 else '',
                      'time_str': '', 'media_hint': ''}
        else:
            words = ns.split(None, 1)
            if words and len(words[0]) <= 20:
                parsed = {'publisher': words[0],
                          'content': words[1] if len(words) > 1 else '',
                          'time_str': '', 'media_hint': ''}
    if not parsed or not parsed.get('publisher'):
        logger.debug(f"[互动] 解析失败: {ns[:50]}")
        return False, 0, 0, 0

    author_name = parsed['publisher']
    post_text   = parsed['content']
    media_hint  = parsed.get('media_hint', '')
    time_str    = parsed.get('time_str', '')

    if time_str:
        pub_ts = parse_publish_timestamp(time_str)
        max_post_age_hours = int(settings.get("max_post_age_hours", 48))
        cutoff_seconds = max_post_age_hours * 3600
        if pub_ts and time.time() - pub_ts > cutoff_seconds:
            if screen_idx == 0:
                logger.info(f"[朋友圈] 第一屏发现超过 {max_post_age_hours} 小时旧动态({time_str})，跳过互动，但不终止巡游")
                return False, 0, 0, 0
            else:
                logger.warning(f"[朋友圈] 非首屏发现超过 {max_post_age_hours} 小时旧动态({time_str})，触发终止巡游信号")
                return True, 0, 0, 0

    moment_id = f"{author_name}_{(post_text or '')[:20]}"
    if moment_id in processed_moments:
        return False, 0, 0, 0
    processed_moments.add(moment_id)

    if skip_self and bot_nickname and author_name == bot_nickname:
        logger.debug(f"[互动] 跳过自己: {author_name}")
        return False, 0, 0, 0
    if skip_ads:
        # 1. 检查 ListItem 本身的 ClassName 是否有微信原生广告特征
        cls_name = getattr(item, 'ClassName', '') or ''
        if 'adgrid' in cls_name.lower() or 'timelinead' in cls_name.lower() or cls_name.startswith('mmui::TimelineAd'):
            logger.info(f"[互动] 检测到微信原生广告类名 {cls_name}，跳过互动: {author_name}")
            return False, 0, 0, 0

        # 2. 检查子元素中是否有显式的“广告”标记按钮
        try:
            ad_btn = item.ButtonControl(Name='广告')
            if ad_btn.Exists(0.05):
                logger.info(f"[互动] 检测到子元素中包含“广告”标记，跳过互动: {author_name}")
                return False, 0, 0, 0
        except Exception:
            pass

        # 合并默认广告词与用户自定义的过滤敏感词
        user_ad_words = settings.get("skip_ad_words", ["广告", "推广", "链接", "优惠", "券", "打折", "点击", "下单", "微商", "代理"])
        ad_kws = set([w.lower().strip() for w in user_ad_words if w.strip()] + ['广告', 'ad', 'sponsored', '#ad'])
        content_for_check = (post_text + item_name[:30]).lower()
        if any(kw in content_for_check for kw in ad_kws):
            logger.debug(f"[互动] 命中广告/微商敏感词，跳过互动: {author_name}")
            return False, 0, 0, 0
    # ⚡️ 高优先级修复：_acc 必须取到真实微信号，否则黑名单读的是错误账号的配置
    _acc = getattr(manager.driver, "bot_wxid", None)
    if not _acc:
        _acc = getattr(manager.driver, "_wxid", None)
    if not _acc:
        try:
            from src.crm.account_data import get_active_account
            _acc = get_active_account()
        except Exception:
            pass
    if not _acc:
        _acc = "main"
        logger.warning("[互动·黑名单] 无法获取真实 bot_wxid，回退为 'main'，黑名单过滤可能跨账号失效！")
    if is_moment_interact_excluded(author_name, _acc):
        logger.debug(f"[互动] 在排除名单中，跳过: {author_name}")
        return False, 0, 0, 0

    # ===== 附加逻辑：随机赞朋友圈封面 =====
    like_cover_enabled = settings.get("like_cover_enabled", False)
    if like_cover_enabled:
        from src.monitor.moment_cover_liker import has_liked_cover_recently, try_like_user_cover, record_cover_like
        if not has_liked_cover_recently(author_name, _acc):
            if random.random() < 0.2:
                logger.info(f"[赞封面] 选中好友 {author_name}，开启封面赞流程...")
                success = try_like_user_cover(manager, item, sns_window, settings, _acc)
                if success:
                    record_cover_like(author_name, _acc)
                    record_interaction(author_name, post_text, "like_cover", fingerprint, _acc)
                    return False, 1, 1, 0

    # 好友防打扰冷却期限制
    cooling_hours = int(settings.get("cooling_hours", 48))
    if cooling_hours > 0:
        from src.utils.moment_dedup import has_interacted_friend_recently
        if has_interacted_friend_recently(author_name, cooling_hours, _acc):
            logger.info(f"[互动] 好友 {author_name} 处于 {cooling_hours} 小时防打扰冷却期内，跳过本条动态")
            return False, 0, 0, 0
    if user_comment_counts.get(author_name, 0) >= per_friend_limit:
        return False, 0, 0, 0
    content_too_short = bool(post_text.strip()) and len(post_text.strip()) < 8

    roll_like    = random.random()
    roll_comment = random.random()
    this_like    = should_like    and roll_like    < cur_like_prob    and like_count    < daily_like_limit
    this_comment = should_comment and roll_comment < cur_comment_prob and comment_count < daily_comment_limit

    # 逐项诊断日志（info 级别，方便排查）
    logger.info(
        f"[互动·概率] {author_name} | "
        f"赞: roll={roll_like:.2f} vs 阈值={cur_like_prob:.2f} → {'✅命中' if this_like else '❌未中'} | "
        f"评论: roll={roll_comment:.2f} vs 阈值={cur_comment_prob:.2f} → "
        f"{'✅命中' if this_comment else '❌未中'}"
        + (f" (内容过短<8字)" if content_too_short else "")
        + (f" (今日已赞{like_count}/{daily_like_limit})" if like_count >= daily_like_limit else "")
        + (f" (今日已评{comment_count}/{daily_comment_limit})" if comment_count >= daily_comment_limit else "")
    )

    if not this_like and not this_comment:
        return False, 0, 0, 0

    fingerprint = generate_moment_fingerprint(author_name, post_text)
    if has_interacted(fingerprint, account_id):
        logger.debug(f"[互动] 已互动过: {author_name}")
        return False, 0, 0, 0

    # ===== 对齐竞品：AI 预判（should_comment 时先问 AI，无需评论则整条跳过含点赞） =====
    precheck_comment = ''   # '' = 未预判 / '__SKIP__' = 整条跳过 / 其他 = 已生成评论文本
    if this_comment and not content_too_short:
        try:
            from src.uia.input_guard import uia_lock as _uia_lock
            _uia_lock.update_status(f"🤖 AI 生成评论中... → {author_name}")
        except Exception:
            pass
        precheck_comment = _ai_precheck(
            manager, post_text, media_hint, author_name, settings, account_id
        )
        if precheck_comment == '__SKIP__':
            # AI 明确说"无需评论" → 对齐竞品 skip_action=True，连赞都不点
            logger.info(f"[互动] AI预判无需互动，整条跳过（含点赞）: {author_name}")
            try:
                from src.uia.input_guard import uia_lock as _uia_lock
                _uia_lock.update_status(f"⏭️ AI判断无需互动，跳过 → {author_name}")
            except Exception:
                pass
            return False, 0, 0, 0

    logger.info(f"✅ 准备互动: {author_name} | {post_text[:30]}")
    try:
        from src.uia.input_guard import uia_lock as _uia_lock
        action_desc = []
        if this_like:
            action_desc.append("点赞")
        if this_comment and not content_too_short:
            action_desc.append("评论")
        _uia_lock.update_status(
            f"👋 正在{'&'.join(action_desc)}: {author_name} | {(post_text or '纯图片/视频')[:18]}"
        )
    except Exception:
        pass
    from src.monitor.moment_interact_utils import _execute_moment_interaction
    return _execute_moment_interaction(
        item=item, manager=manager, sns_window=sns_window,
        list_ctrl=list_ctrl, author_name=author_name,
        post_text=post_text, media_hint=media_hint, item_name=item_name,
        fingerprint=fingerprint, account_id=account_id, settings=settings,
        screen_idx=screen_idx, this_like=this_like, this_comment=this_comment,
        content_too_short=content_too_short, precheck_comment=precheck_comment,
        user_comment_counts=user_comment_counts,
    )






