import time
import logging
import threading
from typing import List

logger = logging.getLogger(__name__)

class TagSyncerLogic:
    """CRM 标签同步逻辑 Mixin"""
    
    def sync_tags_impl(self, session_name: str, customer_id: str, tags: List[str]) -> bool:
        """同步打标与电话核心逻辑（通常在 UIA 物理排他锁保护下执行，供外部同步或 run_uia_with_timeout 调度）"""
        from src.utils.uia_task_runner import run_uia_task
        from src.utils.uia_lock import UIATaskPriority
        
        # 🌟 使用 UIA 物理排他锁保护，防止在打标期间微信会话被其它新消息插队切走
        with run_uia_task(f"同步标签和资料: {session_name}", priority=UIATaskPriority.NORMAL, use_physical_lock=True):
            try:
                switched = self.driver.ChatWith(session_name)
                if not switched:
                    print(f"[资料同步] 无法切换到 {session_name} 的聊天窗口，跳过同步")
                    return False
                time.sleep(0.5)
            except Exception as e:
                print(f"[资料同步] 切换会话异常: {e}")
                return False

            # 获取并判定是否有需要在微信侧同步填写的电话号码
            phone_val = None
            try:
                profile = self._profile_manager.get_profile(customer_id)
                if profile:
                    # 遍历查找 CRM 画像中的手机号标签并剥离（防止手机号被当作普通的微信标签干扰搜索）
                    phone_val = next((t.value for t in profile.tags if t.subcategory == "phone"), None)
                    # 清理 tags 列表，剔除纯数字电话标签（防止它作为普通字词打进标签输入框导致匹配失败）
                    tags = [t for t in tags if not (t.isdigit() and len(t) == 11)]
            except Exception as profile_err:
                logger.debug(f"[资料同步] 提取待同步电话异常: {profile_err}")

            if phone_val:
                # 🌟 如果有电话号码，我们必须走三点菜单弹窗流程，以便定位并填写“电话”栏
                print(f"[资料同步] 检测到需要为 '{session_name}' 填写电话: '{phone_val}'，发起弹窗同步事务")
                success = self._tag_sync.apply_remark_and_tags_from_chat(
                    friend_name=session_name, 
                    remark=None, 
                    tags=tags, 
                    phone=phone_val
                )
            else:
                # 🌟 否则，走原来的无弹窗极速打标签直连优化
                success = self._tag_sync.apply_tags_from_chat(session_name, tags)

            if success:
                # 打标和电话同步成功后，将其从待同步队列或计数器中剔除
                self._profile_manager.mark_tags_synced(customer_id, tags)
                if phone_val:
                    # 将 phone_val 标记为已同步以防止重复弹窗
                    try:
                        profile = self._profile_manager.get_profile(customer_id)
                        if phone_val not in profile.wx_synced_tags:
                            profile.wx_synced_tags.append(phone_val)
                        self._profile_manager.save_profile(profile)
                    except:
                        pass
                print(f"[CRM] ✅ 标签与资料同步成功: {session_name}")
                self._stats["tags_synced"] = self._stats.get("tags_synced", 0) + len(tags)
                return True
            else:
                print(f"[CRM] ⚠️ 标签与资料同步失败: {session_name}")
                return False
    def _trigger_tag_sync(self, session_name: str, customer_id: str, tags: List[str]):
        if getattr(self, "_tag_syncing", False):
            print(f"[CRM] 标签同步中，跳过: {session_name}")
            return

        print(f"[CRM] 准备同步标签到微信: {session_name} ← {tags}")

        import asyncio
        async def _do_sync_async():
            self._tag_syncing = True
            try:
                was_paused = self._paused
                self._paused = True
                await asyncio.sleep(3)
                
                # 局部导入以避免循环依赖，并通过协程排他锁防止与自动回复任务发生物理焦点抢占
                from src.monitor.chat_monitor.reply_engine import _workflow_lock
                async with _workflow_lock:
                    from src.utils.uia_task_runner import run_uia_with_timeout
                    await run_uia_with_timeout(self.sync_tags_impl, 60.0, session_name, customer_id, tags)
                    
                if not was_paused: 
                    self._paused = False
            except Exception as e:
                logger.error(f"[CRM] 标签同步异常: {e}")
                self._paused = False
            finally:
                self._tag_syncing = False

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(_do_sync_async())
                return
        except RuntimeError:
            pass

        # 异步环境不可用时的多线程保底
        def _do_sync():
            self._tag_syncing = True
            try:
                was_paused = self._paused
                self._paused = True
                time.sleep(3)
                self.sync_tags_impl(session_name, customer_id, tags)
                if not was_paused: self._paused = False
            except Exception as e:
                logger.error(f"[CRM] 标签同步异常: {e}")
                self._paused = False
            finally:
                self._tag_syncing = False

        threading.Thread(target=_do_sync, daemon=True, name="tag_sync").start()
