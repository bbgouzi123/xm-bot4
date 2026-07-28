"""
朋友圈巡游核心循环 — 忠实对齐竞品 YokoAIBot_v2，遵循单文件 300 行代码规范。
"""
import logging
import random
import time
import uiautomation as uia
from datetime import datetime
from typing import Any, Dict

from src.uia.retry import random_delay, try_click, physical_click
from src.uia.input_guard import UIAInterruptError, uia_lock
from src.utils.safe_uia import safe_exists, safe_get_children

from src.utils.daily_counter import DailyCounter
from src.utils.moment_config import (
    human_delay, generate_moment_fingerprint,
    has_interacted, record_interaction, is_moment_interact_excluded,
)
from src.monitor.moment_utils import (
    parse_moment_item, parse_publish_timestamp,
    is_item_fully_visible, click_interaction_area, dismiss_popup,
)
from src.monitor.moments_scroll_guard import (
    get_moment_list_snapshot, verify_scroll_displacement, handle_scroll_block,
)
from src.monitor.moment_interactor_helpers import (
    _is_btn_visible, _find_toast, _intel_radar, _sync_tags,
    _process_single_moment_item,
)
from src.monitor.moment_patrol_utils import (
    _is_rest_time, _parse_patrol_settings,
    _check_and_reply_interactions_safe,
)

logger = logging.getLogger(__name__)
_daily_counter = DailyCounter()

def _send_notice(title: str, body: str):
    try:
        import asyncio, threading
        from src.utils.alert_notifier import alert_notifier
        threading.Thread(target=lambda: asyncio.run(alert_notifier.send_user_notification(title, body, "moments")), daemon=True).start()
    except Exception as e:
        logger.warning(f"发送系统通知异常: {e}")






def patrol_round_body(manager: Any, settings: dict, account_id: str) -> int:
    """单轮巡游：打开朋友圈 → 先滚动跳过封面 → 遍历+互动 → 同步标签 → 关闭。"""
    # ===== 休息时间检查（对齐竞品 MomentCommentTask.execute 第一步）=====
    if _is_rest_time(settings):
        logger.info("[朋友圈巡游] 当前处于休息时段，跳过本次巡游")
        return 0

    interacted_count = 0
    with uia_lock("正在执行: 朋友圈互动"):
        interacted_count = _patrol_round_inner(manager, settings, account_id)
    return interacted_count


def _patrol_round_inner(manager: Any, settings: dict, account_id: str) -> int:
    """实际朋友圈巡游处理。"""
    interacted_count = 0
    exit_reason = "扫描未启动"
    
    # 朋友圈打开在右侧，通知 HUD 控制中心最小化吸附，防止遮挡
    try:
        from src.uia.uia_ws_notify import control_hud
        control_hud("minimize_for_moments")
    except Exception:
        pass

    import win32gui
    import ctypes
    main_hwnd = getattr(manager.driver, 'hwnd', None)
    was_minimized = False
    if main_hwnd and win32gui.IsWindow(main_hwnd):
        was_minimized = win32gui.IsIconic(main_hwnd) or not win32gui.IsWindowVisible(main_hwnd)

    sns_window = None
    try:
        while True:
            uia_lock.update_status("⏳ 正在打开朋友圈...")
            sns_window = manager._open_moments()
            if not sns_window:
                logger.warning("朋友圈窗口打开失败，本轮巡游退出")
                exit_reason = "无法打开朋友圈窗口"
                break
            try:
                sns_window.SetActive()
                sns_window.SetFocus()
            except Exception:
                pass
            time.sleep(2.0)

            uia_lock.update_status("🔍 正在定位朋友圈列表...")
            logger.info("开始刷朋友圈...")
            list_ctrl = sns_window.ListControl(Name='朋友圈')
            if not safe_exists(list_ctrl, 1):
                list_ctrl = sns_window.ListControl(ClassName='mmui::TimeLineListView')
            if not safe_exists(list_ctrl, 1):
                list_ctrl = sns_window.ListControl()
            if not safe_exists(list_ctrl, 2):
                logger.error("找不到朋友圈列表控件")
                exit_reason = "找不到朋友圈列表控件"
                break

            bot_nickname = getattr(manager.driver, '_nickname', '')
            processed_moments = set()
            user_comment_counts: Dict[str, int] = {}
            comment_count = 0
            like_count = 0

            logger.info(f"[调试·朋友圈原始配置] 传入 settings={settings}")
            p = _parse_patrol_settings(settings, account_id)
            should_like         = p["should_like"]
            should_comment      = p["should_comment"]
            like_prob           = p["like_prob"]
            comment_prob        = p["comment_prob"]
            daily_like_limit    = p["daily_like_limit"]
            daily_comment_limit = p["daily_comment_limit"]
            per_friend_limit    = p["per_friend_limit"]
            comment_limit       = p["comment_limit"]
            max_screens         = p["max_screens"]
            skip_self           = p["skip_self"]
            skip_ads            = p["skip_ads"]

            logger.info(
                f"[朋友圈设置] 点赞={should_like}(概率={like_prob:.0%}) "
                f"评论={should_comment}(概率={comment_prob:.0%}) "
                f"日赞上限={daily_like_limit} 日评上限={daily_comment_limit} "
                f"本轮评论上限={comment_limit} 最大屏数={max_screens}"
            )
            uia_lock.update_status(
                f"📋 任务开始 | 今日已赞 {like_count}/{daily_like_limit}  已评 {comment_count}/{daily_comment_limit}"
            )

            uia_lock.update_status("⬇️ 跳过封面，准备开始扫描动态...")
            logger.info("[朋友圈] 先滚动跳过封面区域...")
            try:
                sns_window.SetActive()
                sns_window.SetFocus()
            except Exception:
                pass
            list_ctrl.WheelDown(4)
            time.sleep(1.5)

            screen_idx = 0
            logger.warning(f"[调试·大循环前] screen_idx={screen_idx}, max_screens={max_screens}, manager._running={manager._running}")
            while screen_idx < max_screens and manager._running:
                logger.warning(f"[调试·大循环内] 开始迭代 screen_idx={screen_idx}")
                is_like_done = (not should_like) or (like_count >= daily_like_limit)
                is_comment_done = (not should_comment) or (comment_count >= daily_comment_limit)
                if is_like_done and is_comment_done:
                    logger.info("[朋友圈] 赞评功能未启用或已达今日上限")
                    exit_reason = "已达今日点赞和评论上限"
                    break

                if should_comment and comment_limit > 0 and comment_count >= comment_limit:
                    logger.info(f"[朋友圈] 本轮评论已达上限({comment_count}/{comment_limit})")
                    exit_reason = f"已达单轮评论上限({comment_count}条)"
                    break

                decay = max(0.4, 1.0 - screen_idx * 0.1)
                cur_like_prob    = like_prob    * decay
                cur_comment_prob = comment_prob * decay

                try:
                    sns_window.SetActive()
                    sns_window.SetFocus()
                except Exception:
                    pass

                items = []
                for _get_retry in range(3):
                    try:
                        items = safe_get_children(list_ctrl)
                    except Exception as _ce:
                        logger.debug(f"[朋友圈] GetChildren 失败: {_ce}")
                    
                    if not items:
                        _lc = sns_window.ListControl(ClassName='mmui::TimeLineListView')
                        if safe_exists(_lc, 0.5):
                            list_ctrl = _lc
                            try:
                                items = safe_get_children(list_ctrl)
                            except Exception:
                                pass
                    
                    if items:
                        break
                    time.sleep(1.0 if screen_idx == 0 else 0.4)

                if not items:
                    logger.warning("[调试·大循环内退出] 触发 not items 导致 break")
                    exit_reason = "未检测到更多朋友圈动态（已滑到底部或超时）"
                    break

                logger.info(f"[朋友圈·第{screen_idx+1}屏] 获取到 {len(items)} 个列表项")
                uia_lock.update_status(
                    f"📜 第 {screen_idx+1} 屏，扫描中... | 今日已赞 {like_count} 已评 {comment_count}"
                )

                stop_this_round = False
                for item_idx, item in enumerate(items):
                    if not manager._running:
                        break
                    is_like_done = (not should_like) or (like_count >= daily_like_limit)
                    is_comment_done = (not should_comment) or (comment_count >= daily_comment_limit)
                    if is_like_done and is_comment_done:
                        break
                    if should_comment and comment_limit > 0 and comment_count >= comment_limit:
                        stop_this_round = True
                        break

                    try:
                        stop_rnd, inter, liked, commented = _process_single_moment_item(
                            item=item, item_idx=item_idx, manager=manager,
                            sns_window=sns_window, list_ctrl=list_ctrl,
                            settings=settings, account_id=account_id, bot_nickname=bot_nickname,
                            processed_moments=processed_moments, user_comment_counts=user_comment_counts,
                            per_friend_limit=per_friend_limit,
                            like_count=like_count, comment_count=comment_count,
                            daily_like_limit=daily_like_limit, daily_comment_limit=daily_comment_limit,
                            should_like=should_like, should_comment=should_comment,
                            skip_self=skip_self, skip_ads=skip_ads,
                            cur_like_prob=cur_like_prob, cur_comment_prob=cur_comment_prob,
                            screen_idx=screen_idx,
                        )
                        interacted_count += inter
                        like_count    += liked
                        comment_count += commented
                        if inter:
                            uia_lock.update_status(f"✅ 互动完成 | 本轮已赞 {like_count} 已评 {comment_count}")
                        if stop_rnd:
                            stop_this_round = True
                            break
                    except Exception as item_e:
                        continue

                if stop_this_round:
                    logger.warning("[调试·大循环内退出] 触发 stop_this_round 导致 break")
                    exit_reason = "检测到已互动过的旧动态（或2天前旧动态）"
                    break

                # 记录滑动前的可见项快照以做位移监控
                snapshot_before = get_moment_list_snapshot(list_ctrl)

                try:
                    list_ctrl.WheelDown(wheelTimes=3)
                except Exception:
                    _lc = sns_window.ListControl(ClassName='mmui::TimeLineListView')
                    if safe_exists(_lc, 1):
                        list_ctrl = _lc
                        list_ctrl.WheelDown(wheelTimes=3)
                
                random_delay(0.8, 1.2)  # 等待加载
                
                # 滑动有效性校验
                snapshot_after = get_moment_list_snapshot(list_ctrl)
                if not verify_scroll_displacement(snapshot_before, snapshot_after):
                    # 无位移，可能卡死或有微信弹窗。发送 ESC 尝试清除阻塞
                    is_ended, is_recovered = handle_scroll_block(sns_window)
                    if is_ended:
                        exit_reason = "已滑动至朋友圈最底部（到底熔断）"
                        break
                    elif not is_recovered:
                        exit_reason = "滑动卡死阻塞且ESC未能恢复"
                        break
                    else:
                        # ESC 清除了阻碍弹窗，重新定位朋友圈列表
                        _lc = sns_window.ListControl(ClassName='mmui::TimeLineListView')
                        if safe_exists(_lc, 1.5):
                            list_ctrl = _lc
                
                screen_idx += 1

            if screen_idx >= max_screens:
                exit_reason = f"已扫描完设定的 {max_screens} 屏动态"
            elif not manager._running:
                exit_reason = "用户手动停止了巡游"

            msg = f"🏁 巡游结束 | 原因: {exit_reason} | 本轮共互动 {interacted_count} 条"
            uia_lock.update_status(msg)
            _send_notice(
                "✨ 朋友圈点赞评论任务结束",
                f"结束原因：{exit_reason}。\n📊 数据统计：本轮巡游扫描了 {screen_idx} 屏，互动了 {interacted_count} 条动态（点赞 {like_count} 次，评论 {comment_count} 次）。"
            )
            time.sleep(5.0)

            _sync_tags(manager, sns_window, settings)
            _check_and_reply_interactions_safe(manager, sns_window, settings, account_id)
            break
    except UIAInterruptError:
        logger.info("[朋友圈巡游] 用户 ESC 中断，优雅退出")
    except OSError as e:
        # COM RPC 断连异常 (0x80010108 / 0x8001010d) 被 Python 捕获时表现为 OSError
        logger.warning(f"[朋友圈巡游] COM/UIA 连接异常，本轮安全退出: {e}")
    except Exception as e:
        logger.error(f"单轮朋友圈巡游异常: {e}")
        uia_lock.update_status(f"❌ 巡游异常: {str(e)[:30]}")
        raise e
    finally:
        if sns_window:
            try:
                manager.driver._close_moments(sns_window)
            except Exception as ce:
                logger.warning(f"[朋友圈巡游] 关闭朋友圈窗口异常: {ce}")
        try:
            manager.driver._ensure_chat_page(force=True)
        except Exception:
            pass

        if was_minimized and main_hwnd and win32gui.IsWindow(main_hwnd):
            logger.info(f"[静默回收] 任务结束，自动将微信主窗口(hwnd={main_hwnd})收回至最小化/后台状态...")
            try:
                ctypes.windll.user32.ShowWindow(main_hwnd, 6) # SW_MINIMIZE
            except Exception as e:
                logger.debug(f"[静默回收] 最小化窗口失败: {e}")

        # 任务结束，恢复 HUD 控制中心吸附前的展开状态
        try:
            from src.uia.uia_ws_notify import control_hud
            control_hud("restore_for_moments")
        except Exception:
            pass
    return interacted_count
