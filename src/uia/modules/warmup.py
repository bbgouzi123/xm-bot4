import logging
import random
import time
import hashlib
import pyperclip
import win32gui as _w32
import uiautomation as uia

from src.uia.elements import WxClass
from src.uia.retry import exists_with_timeout, is_shift_pressed, click_at_absolute, try_click

logger = logging.getLogger("WeChatDriver")

class WeChatWarmupMixin:
    def check_and_interact_moment_messages(self) -> bool:
        """检查并消除朋友圈新消息红点并进行曝光交互"""
        if not self.is_connected():
            return False

        try:
            from src.uia.input_guard import uia_lock
            with self._lock, uia_lock("正在检查朋友圈新消息", hwnd=self.hwnd):
                moment_window = self._open_moments_window()
                if not moment_window:
                    return False
                
                self._ensure_moments_foreground()
                random_delay(0.5, 1.0)

                # 检索包含“新消息”或“条新消息”的按钮
                msg_btn = None
                for ctrl, _ in uia.WalkControl(moment_window, maxDepth=10):
                    try:
                        name = ctrl.Name or ''
                        if ctrl.ControlTypeName == 'ButtonControl' and ('新消息' in name or '条新消息' in name):
                            msg_btn = ctrl
                            break
                    except Exception:
                        continue

                if msg_btn:
                    logger.info(f"[养号消红点] 发现未读朋友圈互动: {msg_btn.Name}，正在点开互动...")
                    try_click(msg_btn, max_retries=2, delay=0.5)
                    random_delay(1.5, 2.5) # 模拟人在看互动列表
                    
                    # 查找弹出的小消息窗口（或者是子面板）并按 Esc 关闭它以消除红点
                    uia.SendKeys('{Esc}')
                    random_delay(0.5, 1.0)
                    
                    self._close_moments(moment_window)
                    self._ensure_chat_page(force=True)
                    return True
                else:
                    logger.info("[养号消红点] 朋友圈暂无未读消息")
                    self._close_moments(moment_window)
                    return False
        except Exception as e:
            logger.error(f"[养号消红点] 异常: {e}")
            return False

    def browse_moments_with_history(self, limit: int = 5, ai_service=None) -> dict:
        """基于已读历史特征游标刷朋友圈，并在安全红线内进行随机点赞或AI评论"""
        if not self.is_connected():
            return {"success": False, "interacted": 0}

        # 惰性初始化已读朋友圈历史哈希游标
        if not hasattr(self, '_moment_read_history'):
            self._moment_read_history = set()

        result = {"success": True, "interacted": 0, "reached_previous_cursor": False}
        from src.uia.input_guard import uia_lock
        from src.uia.retry import get_dpi_scale
        
        try:
            with self._lock, uia_lock("正在刷朋友圈养号中", hwnd=self.hwnd):
                moment_window = self._open_moments_window()
                if not moment_window:
                    return {"success": False, "interacted": 0}
                
                self._ensure_moments_foreground()
                random_delay(0.8, 1.5)
                
                list_view = moment_window.ListControl(ClassName=WxClass.TIMELINE_LIST)
                if not list_view or not exists_with_timeout(list_view, 2):
                    self._close_moments(moment_window)
                    return {"success": False, "interacted": 0}

                interacted_count = 0
                max_scroll_attempts = 6 # 最大滚动次数，防止陷入无限死循环
                
                for attempt in range(max_scroll_attempts):
                    if is_shift_pressed() or interacted_count >= limit:
                        break

                    items = list_view.GetChildren()
                    new_item_found = False

                    for item in items:
                        if getattr(item, 'ClassName', '') != WxClass.TIMELINE_CONTENT:
                            continue
                        
                        raw_text = getattr(item, 'Name', '') or ''
                        if not raw_text:
                            continue

                        # 基于发帖人和正文生成唯一的 MD5 历史游标哈希
                        item_hash = hashlib.md5(raw_text.encode('utf-8')).hexdigest()
                        
                        # 1. 🌟 判断是否刷到了上次读过的游标位置！如果是，则立刻终止
                        if item_hash in self._moment_read_history:
                            logger.info("[养号刷圈] 📍 检测到已刷到上次看到的游标位置，优雅结束本次浏览。")
                            result["reached_previous_cursor"] = True
                            break
                        
                        # 记录进入已读历史
                        self._moment_read_history.add(item_hash)
                        new_item_found = True
                        
                        # 解析发帖人和内容
                        publisher = "未知好友"
                        content_text = raw_text
                        if ':' in raw_text:
                            parts = raw_text.split(':', 1)
                            publisher = parts[0].strip()
                            content_text = parts[1].strip()

                        # 2. 模拟真人随机互动 (例如 25% 概率点赞，15% 概率 AI 评论，其他仅流式阅读)
                        action_roll = random.random()
                        
                        rect = item.BoundingRectangle
                        _s = get_dpi_scale()
                        btn_x = rect.right - int(60 * _s)
                        btn_y = rect.bottom - int(20 * _s)

                        if action_roll < 0.25: # 点赞
                            logger.info(f"[养号刷圈] 💖 选中点赞好友 {publisher} 的动态")
                            click_at_absolute(btn_x, btn_y)
                            random_delay(0.3, 0.6)
                            
                            # 弹出小菜单后寻找“赞”按钮 (一般为第一个 ButtonControl 或根据 Name)
                            like_btn = moment_window.ButtonControl(Name="赞")
                            if like_btn.Exists(0.5):
                                try_click(like_btn)
                                interacted_count += 1
                                random_delay(0.8, 1.5)
                            else:
                                # 点击其他地方收起菜单
                                click_at_absolute(rect.left + 50, btn_y)
                        
                        elif action_roll < 0.40 and ai_service: # AI 神评
                            logger.info(f"[养号刷圈] 💬 选中AI评论好友 {publisher} 的动态")
                            click_at_absolute(btn_x, btn_y)
                            random_delay(0.3, 0.6)
                            
                            comment_btn = moment_window.ButtonControl(Name="评论")
                            if comment_btn.Exists(0.5):
                                try_click(comment_btn)
                                random_delay(0.5, 1.0)
                                
                                edit = moment_window.EditControl(ClassName=WxClass.COMMENT_EDIT)
                                if edit and exists_with_timeout(edit, 1.0):
                                    # 请求 AI 评论
                                    reply_text = "太赞了！"
                                    try:
                                        import asyncio
                                        try:
                                            loop = asyncio.get_event_loop()
                                        except RuntimeError:
                                            loop = asyncio.new_event_loop()
                                            asyncio.set_event_loop(loop)
                                        
                                        if loop.is_running():
                                            import concurrent.futures
                                            with concurrent.futures.ThreadPoolExecutor() as pool:
                                                def _run_ai():
                                                    new_loop = asyncio.new_event_loop()
                                                    asyncio.set_event_loop(new_loop)
                                                    return new_loop.run_until_complete(
                                                        ai_service.generate_comment(content=content_text, user_name=publisher)
                                                    )
                                                ai_res = pool.submit(_run_ai).result()
                                        else:
                                            ai_res = loop.run_until_complete(
                                                ai_service.generate_comment(content=content_text, user_name=publisher)
                                            )

                                        if ai_res and ai_res.get("success") and ai_res.get("content"):
                                            reply_text = ai_res.get("content")
                                    except Exception:
                                        pass
                                    
                                    pyperclip.copy(reply_text)
                                    uia.SendKeys('{Ctrl}v')
                                    random_delay(0.3, 0.6)
                                    
                                    # 发送评论
                                    send_btn = None
                                    for ctrl, _ in uia.WalkControl(moment_window, maxDepth=8):
                                        if ctrl.ControlTypeName == 'ButtonControl':
                                            send_btn = ctrl
                                            break
                                    if send_btn:
                                        try_click(send_btn)
                                        interacted_count += 1
                                        random_delay(1.0, 1.8)
                            else:
                                # 点击收起
                                click_at_absolute(rect.left + 50, btn_y)
                        
                        else:
                            # 仅阅读浏览，冷静 1-2 秒
                            logger.info(f"[养号刷圈] 👀 流式浏览了 {publisher} 的动态")
                            random_delay(1.0, 2.0)

                    if result["reached_previous_cursor"]:
                        break

                    # 向下滚动一屏
                    list_view.SendKeys('{PageDown}')
                    random_delay(1.2, 2.0)

                self._close_moments(moment_window)
                self._ensure_chat_page(force=True)
                
                result["interacted"] = interacted_count
                return result
        except Exception as e:
            logger.error(f"[养号刷圈] 浏览失败: {e}")
            try:
                with self._lock:
                    uia.SendKeys('{Esc}')
            except Exception:
                pass
            return result

def random_delay(lo: float = 0.2, hi: float = 0.5):
    time.sleep(random.uniform(lo, hi))
