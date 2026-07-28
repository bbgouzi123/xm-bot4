"""
朋友圈互动 AI 预判模块

对齐竞品 YokoAIBot_v2 auto_moment_comment 逻辑（L1491-1497）：
在执行点赞/评论前先询问 AI 是否值得互动，
若 AI 返回"无需评论"则整条跳过（连赞也不点），
同时返回已生成的评论文本供后续直接发送（避免二次 AI 调用）。
"""
import logging

logger = logging.getLogger(__name__)


def ai_precheck(manager, post_text: str, media_hint: str, author_name: str,
                settings: dict, account_id: str) -> str:
    """AI 预判：在执行点赞/评论之前，先询问 AI 是否需要互动。

    对齐竞品 auto_moment_comment 逻辑（L1491-1497）：
    - AI 返回 '无需评论' → skip_action=True → 连赞都不点，整条跳过
    - 避免对纯广告、无意义内容骚扰式互动

    Returns:
        ''           → 无 AI / AI 无法判断，走原有逻辑（仍可点赞/评论）
        '__SKIP__'   → AI 明确判断无需互动，整条跳过（含点赞）
        其他非空字符串 → 已生成的评论文本（通过安全网），可直接复用，跳过二次 AI 调用
    """
    try:
        ai_svc = getattr(manager, 'ai_service', None)
        if not ai_svc or not ai_svc.is_configured():
            return ''   # 无 AI 服务时不预判，走原有逻辑

        content_desc  = post_text if post_text.strip() else "（纯图片/视频动态，无文字）"
        media_context = f"，媒体：{media_hint}" if media_hint else ""

        comment_prompt = (
            f"你正在刷微信朋友圈，请针对以下好友动态写一句简短实在的评论"
            f"（严格控制在15个字以内，像熟人好友之间的语气，不要像客服或机器）。\n"
            f"如果该动态实在无法评论（如纯广告、无意义内容），请回复'无需评论'。\n"
            f"朋友圈内容：'{content_desc}'{media_context}\n"
            f"要求：只输出应该发出去的那句话，不加任何解释文字或引号标识。"
        )

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            bot_id = getattr(ai_svc, 'agent_id', '') or ''
            if bot_id:
                ai_coro = ai_svc.start_chat(
                    agent_id=bot_id,
                    message=comment_prompt,
                    session_id="moment_precheck",
                    user_name=author_name,
                    cache_session=False,
                )
            elif hasattr(ai_svc, 'generate_comment'):
                ai_coro = ai_svc.generate_comment(
                    content=post_text or "（纯图片/视频动态）",
                    session_id=author_name,
                    user_name=author_name,
                    account_id=account_id,
                )
            else:
                ai_coro = ai_svc.start_chat(
                    message=comment_prompt,
                    session_id="moment_precheck",
                    user_name=author_name,
                    cache_session=False,
                )
            ai_result = loop.run_until_complete(
                asyncio.wait_for(ai_coro, timeout=25.0)
            )
        except asyncio.TimeoutError:
            logger.warning(f"[AI预判] 超时(>25s)，跳过预判继续执行: {author_name}")
            loop.close()
            return ''
        loop.close()

        reply_text = ''
        if ai_result and ai_result.get('success'):
            raw = (ai_result.get('content') or ai_result.get('reply') or '').strip()
            # 兼容 Coze JSON 输出（parse_comment_and_insight 拆评论和画像 JSON）
            try:
                from src.monitor.moment_insight_crm import parse_comment_and_insight
                reply_text, _ = parse_comment_and_insight(raw)
            except Exception:
                reply_text = raw

        if not reply_text:
            return ''

        if '无需评论' in reply_text:
            logger.info(f"[AI预判] AI判断无需互动，整条跳过（连赞也不点）: {author_name}")
            return '__SKIP__'

        # 通过安全网后返回评论文本（供后续 do_like_and_comment 复用，避免二次调用）
        try:
            from src.utils.moment_config import validate_comment
            is_safe, reason, cleaned = validate_comment(reply_text, post_text, settings)
            if not is_safe:
                logger.warning(f"[AI预判·安全网] 拦截: {reason}")
                return ''
            return cleaned
        except Exception:
            return reply_text

    except Exception as e:
        logger.warning(f"[AI预判] 执行异常，跳过预判: {e}")
        return ''
