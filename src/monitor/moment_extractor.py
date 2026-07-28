import logging
import asyncio
from typing import Any
from src.crm.profile_manager import ProfileManager

logger = logging.getLogger(__name__)

def extract_worker_loop(manager: Any):
    """后台异步画像情报分析与标签提取线程"""
    logger.info("朋友圈异步画像情报提取线程已启动。")
    while manager._running:
        try:
            task = manager._extract_queue.get(timeout=2.0)
            if task is None:
                break
            
            author_name = task["author_name"]
            post_text = task["post_text"]
            account_id = task["account_id"]
            
            if not manager.ai_service or not manager.ai_service.is_configured():
                continue
            
            # 构建让 AI 提取标签的专属 Prompt
            extract_prompt = (
                f"分析以下朋友圈内容，提炼5个以内的简短标签（如: '爱美食', '经常出差', '家有萌宠'）。\n"
                f"除了标签外不要输出任何其他废话，用逗号分隔。\n"
                f"内容：'{post_text}'"
            )
            
            ai_extracted_tags = []
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    manager.ai_service.start_chat("moment_extractor", extract_prompt, "system", author_name)
                )
                if result and result.get('reply'):
                    tags_str = result.get('reply').strip()
                    loop.close()
                    
                    pm = ProfileManager(account_id=account_id)
                    target_wxid = author_name
                    for p in pm.get_all_profiles():
                        if p.nickname == author_name:
                            target_wxid = p.wxid
                            break
                            
                    pm.add_note(target_wxid, f"AI分析标签: {tags_str}")
                    logger.info(f"[智能化异步] AI 提取 {author_name} 的画像情报: {tags_str}")
                    
                    ai_extracted_tags = [
                        t.strip().strip("'").strip('"')
                        for t in tags_str.replace('，', ',').split(',')
                        if t.strip()
                    ]
                    
                    if ai_extracted_tags:
                        raw_tags_dict = {f"moment_tag_{i}": tag for i, tag in enumerate(ai_extracted_tags)}
                        pm.update_from_ai_tags(
                            wxid=target_wxid,
                            raw_tags=raw_tags_dict,
                            source="moment",
                            nickname=author_name
                        )

                        # 实时的物理同步到微信官方客户端官方标签
                        try:
                            from src.utils.uia_lock import uia_lock, UIATaskPriority
                            from src.utils.uia_task_runner import run_uia_with_timeout

                            def _do_moment_uia_sync():
                                import asyncio
                                async def _async_sync():
                                    try:
                                        logger.info(f"[朋友圈AI打标] 启动 UIA 锁，准备物理同步标签 {ai_extracted_tags} 到 {author_name}")
                                        async with uia_lock(UIATaskPriority.LOW, f"朋友圈AI打标→{author_name[:10]}", timeout=15.0):
                                            await run_uia_with_timeout(
                                                manager.driver.apply_remark_and_tags_from_chat,
                                                20.0,
                                                author_name,
                                                None,  # 备注名保持原样
                                                ai_extracted_tags
                                            )
                                            # 同步成功后标记已完成同步
                                            pm.mark_tags_synced(target_wxid, ai_extracted_tags)
                                    except Exception as ex:
                                        logger.debug(f"[朋友圈AI打标] 物理同步微信客户端官方标签失败: {ex}")

                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                loop.run_until_complete(_async_sync())
                                loop.close()

                            import threading
                            threading.Thread(target=_do_moment_uia_sync, daemon=True, name=f"moment-uia-sync-{author_name[:8]}").start()
                        except Exception as tag_err:
                            logger.error(f"[智能化异步] 触发实时物理标签同步异常: {tag_err}")
                else:
                    loop.close()
            except Exception as e:
                logger.warning(f"[智能化异步] AI 提取画像失败: {e}")
                
        except __import__("queue").Empty:
            continue
        except Exception as e:
            logger.error(f"[智能化异步] 提取线程异常: {e}")
