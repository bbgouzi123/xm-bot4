import os
import json
import asyncio
import logging
from . import state
from .bootstrap import (
    expire_stale_pending_moments_and_collect_due,
    _persist_schedules_after_mutation,
)

logger = logging.getLogger(__name__)

class RunnerMixin:
    """排期执行巡检的 Mixin"""

    def _sync_schedules_to_cloud(self):
        _persist_schedules_after_mutation()

    async def check_and_execute_schedules(self, wechat_driver):
        try:
            from src.utils.user_activity import is_user_active

            stale_count, pending_tasks = expire_stale_pending_moments_and_collect_due()
            if stale_count:
                self._sync_schedules_to_cloud()
                logger.info(
                    "[日历引擎] 已将 %s 条过期未发送的排期标记为失败", stale_count
                )

            if not pending_tasks:
                return

            if is_user_active(check_caret=True):
                logger.info("[日历引擎] 用户正在操作键鼠或输入，本轮跳过发圈")
                return

            pending_tasks = pending_tasks[:1]

            for task in pending_tasks:
                task_id = task["id"]
                text = task["content_text"]
                media_raw = task.get("media_urls", [])
                if isinstance(media_raw, str):
                    try:
                        media_raw = json.loads(media_raw)
                    except Exception:
                        media_raw = []
                media_paths = media_raw

                state._executed_ids.add(task_id)
                logger.info(f"[日历引擎] 触发发圈任务 #{task_id} ...")

                try:
                    from src.monitor.moment_post import MomentPost
                    poster = MomentPost(wechat_driver)

                    def resolve_media_paths(urls):
                        resolved = []
                        import tempfile, requests, uuid
                        from urllib.parse import urlparse
                        for url in urls:
                            if url.startswith('/api/file/download/'):
                                file_id = url.split('/')[-1]
                                from src.api.file_api import UPLOAD_DIR
                                local_p = UPLOAD_DIR / file_id
                                if local_p.exists():
                                    resolved.append(str(local_p))
                            elif url.startswith('/'):
                                # /api/xm-oss/... 相对路径 OSS 重定向链接，通过本地 HTTP 下载
                                try:
                                    from app.constants import BOT4_PORT
                                    full_url = f"http://127.0.0.1:{BOT4_PORT}{url}"
                                    resp = requests.get(full_url, timeout=30, allow_redirects=True)
                                    if resp.status_code == 200:
                                        content_type = resp.headers.get('Content-Type', 'image/png')
                                        ext = '.jpg' if 'jpeg' in content_type else '.png'
                                        tmp_p = os.path.join(tempfile.gettempdir(), f"direct_bg_{uuid.uuid4().hex}{ext}")
                                        with open(tmp_p, 'wb') as f:
                                            f.write(resp.content)
                                        resolved.append(tmp_p)
                                except Exception as e:
                                    logger.error(f"[日历引擎] 下载OSS重定向图片失败 {url}: {e}")
                            elif url.startswith('http://') or url.startswith('https://'):
                                try:
                                    resp = requests.get(url, timeout=30)
                                    if resp.status_code == 200:
                                        ext = os.path.splitext(urlparse(url).path)[1]
                                        if not ext: ext = '.png'
                                        tmp_p = os.path.join(tempfile.gettempdir(), f"ai_img_{uuid.uuid4().hex}{ext}")
                                        with open(tmp_p, 'wb') as f:
                                            f.write(resp.content)
                                        resolved.append(tmp_p)
                                except Exception as e:
                                    logger.error(f"[日历引擎] 下载网络图片失败 {url}: {e}")
                            else:
                                if os.path.exists(url):
                                    resolved.append(url)
                        return resolved

                    def _notify(msg: str):
                        try:
                            from src.uia.uia_ws_notify import notify_frontend
                            notify_frontend("status_update", msg)
                        except Exception:
                            pass

                    if media_paths:
                        _notify(f"正在下载底图（共 {len(media_paths)} 张）...")
                        local_paths = resolve_media_paths(media_paths)
                        _notify(f"底图已就绪，正在发布朋友圈...")
                        result = poster.publish_with_images(text, local_paths)
                    else:
                        _notify("正在发布朋友圈（纯文字）...")
                        result = poster.publish_text(text)

                    new_status = "published" if (result and result.get("success")) else "failed"
                    if new_status == "published":
                        _notify(f"✅ 朋友圈发布成功 #{task_id}")
                    else:
                        err_msg = result.get("error", "未知错误") if result else "未知错误"
                        _notify(f"❌ 发布失败：{err_msg}")
                    with state._schedule_lock:
                        for s in state._schedules:
                            if s["id"] == task_id:
                                s["status"] = new_status
                                if new_status == "failed":
                                    s["error_msg"] = result.get("error", "Unknown") if result else "Unknown"
                                break

                except Exception as e:
                    logger.error(f"[日历引擎] 发送任务时物理引擎异常: {e}")

                await asyncio.sleep(15)

            self._sync_schedules_to_cloud()

        except Exception as e:
            logger.error(f"[日历引擎] 巡检管线异常: {e}")
