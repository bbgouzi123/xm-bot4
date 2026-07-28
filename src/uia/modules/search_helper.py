import logging
import time as _time_pkg
import uiautomation as uia
import pyperclip
import os
from src.uia.retry.clicks import try_click, physical_click
from src.uia.retry import random_delay
from src.utils.safe_uia import (
    safe_walk_control,
    safe_control_type,
    safe_get_name,
    safe_bounding_rect,
    safe_get_children
)

logger = logging.getLogger(__name__)


def search_and_click_impl(driver, session_name: str, wxid: str = None) -> bool:
    if session_name == "filehelper" or wxid == "filehelper":
        session_name = "文件传输助手"
    try:
        logger.info(f"[UIA] 开始通过外部模块搜索并切换到会话: '{session_name}', wxid: '{wxid}'")
        from src.uia.retry import ensure_wechat_foreground
        ensure_wechat_foreground(driver.hwnd)

        # 🌟 1. 优先使用 Ctrl + F 物理热键定位聚焦微信搜索栏，实现纯键盘聚焦（100% 避免物理鼠标点击搜索框卡死）
        try:
            uia.SendKeys("{Ctrl}f")
            random_delay(0.15, 0.3)
        except Exception as e_ctrl:
            logger.debug(f"[UIA] 发送 Ctrl+F 异常: {e_ctrl}")

        # 2. 从当前焦点控件或直连获取搜索框，无需执行任何物理鼠标点击操作
        search_box = None
        try:
            focused = uia.GetFocusedControl()
            if focused and (focused.ControlTypeName == "EditControl" or "Edit" in (getattr(focused, "ClassName", "") or "")):
                search_box = focused
        except Exception:
            pass

        if not search_box or not search_box.Exists(0.1):
            search_box = driver.root.EditControl(Name="搜索")
            if not search_box or not search_box.Exists(0.1):
                search_box = driver.root.EditControl(ClassName="mmui::XValidatorTextEdit")

        if not search_box or not search_box.Exists(0.1):
            from src.uia.retry.tray import try_recover_wechat_from_whitescreen
            if try_recover_wechat_from_whitescreen():
                focused = uia.GetFocusedControl()
                if focused and (focused.ControlTypeName == "EditControl" or "Edit" in (getattr(focused, "ClassName", "") or "")):
                    search_box = focused
                if not search_box or not search_box.Exists(0.1):
                    search_box = driver.root.EditControl(Name="搜索")
                    if not search_box or not search_box.Exists(0.1):
                        search_box = driver.root.EditControl(ClassName="mmui::XValidatorTextEdit")

        if not search_box or not search_box.Exists(0.1):
            logger.error("[UIA] 微信全局搜索框定位失败")
            return False

        # 3. 输入搜索文本 (安全截断至 32 字节，防止特殊字符或超长名字导致微信崩溃/挂起)
        # 🌟 强制通过微信昵称与备注名（session_name）进行查找，禁用微信号搜索
        search_key = session_name
        session_name_truncated = search_key.encode('utf-8')[:32].decode('utf-8', errors='ignore')
        
        tried_rects = set()
        clipboard_ok = False
        for att in range(3):
            try:
                import pyperclip
                pyperclip.copy(session_name_truncated)
                clipboard_ok = True
                break
            except Exception as clip_ex:
                logger.warning(f"[UIA] 复制到剪贴板失败，重试中: {clip_ex}")
                _time_pkg.sleep(0.05)

        for attempt in range(3):
            logger.info(f"[UIA] 搜索定位会话 '{session_name}' (wxid: {wxid})，尝试第 {attempt+1} 次...")
            
            # 聚焦并清空、填充搜索词
            search_box = driver._find_search_box()
            if not search_box:
                logger.warning("[UIA] 未找到微信搜索框，重试中")
                _time_pkg.sleep(0.2)
                continue
                
            # ⚠️ [修复] 必须先确认搜索框已接焦，再通过「控件级」SendKeys 精准注入
            # 全局 uia.SendKeys 是盲注入，在焦点漂移时会向错误控件（甚至显示为"^a^v"字面字符）发送
            try_click(search_box, max_retries=2, delay=0.1)
            _time_pkg.sleep(0.08)  # 等待系统分配焦点到 search_box

            search_ok = False
            # 优先策略：内存级 ValuePattern.SetValue 直写（不走键盘模拟，最安全）
            try:
                val_pattern = search_box.GetValuePattern()
                if val_pattern:
                    val_pattern.SetValue(session_name_truncated)
                    _time_pkg.sleep(0.05)
                    if (val_pattern.Value or "").strip() == session_name_truncated.strip():
                        search_ok = True
                        logger.info(f"[UIA] 内存直写搜索框成功: '{session_name_truncated}'")
                        # 💡 触发监听器刷新：部分版本的微信通过 ValuePattern 修改后不触发 Qt 的 textChanged 监听，
                        # 需要追加发送一个无伤按键组合（空格并回退）来唤醒搜索面板过滤更新。
                        try:
                            search_box.SendKeys("{Space}{BackSpace}", waitTime=0.05)
                        except Exception:
                            pass
            except Exception as set_val_ex:
                logger.debug(f"[UIA] 内存直写搜索框异常: {set_val_ex}")

            # 兜底策略：通过 Win32 PostMessage 发送物理 Ctrl+A / Ctrl+V
            # 彻底绕过 SendKeys 的 "^" 修饰符（在微信 Qt 控件上会退化为字面字符 ^a^v）
            if not search_ok and clipboard_ok:
                try:
                    import ctypes
                    VK_CONTROL, VK_A, VK_V = 0x11, 0x41, 0x56
                    WM_KEYDOWN, WM_KEYUP, WM_CLEAR = 0x100, 0x101, 0x303
                    hwnd = search_box.NativeWindowHandle if hasattr(search_box, "NativeWindowHandle") else 0
                    if hwnd and hwnd > 0:
                        # 发送 Ctrl+A 全选
                        ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, VK_CONTROL, 0)
                        ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, VK_A, 0)
                        _time_pkg.sleep(0.05)
                        ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, VK_A, 0)
                        ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, VK_CONTROL, 0)
                        _time_pkg.sleep(0.05)
                        # 发送 Ctrl+V 粘贴
                        ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, VK_CONTROL, 0)
                        ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, VK_V, 0)
                        _time_pkg.sleep(0.05)
                        ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, VK_V, 0)
                        ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, VK_CONTROL, 0)
                        search_ok = True
                        logger.info(f"[UIA] 通过 Win32 PostMessage 发送 Ctrl+A/V 成功")
                    else:
                        raise ValueError("无法获取搜索框 hwnd")
                except Exception as pm_ex:
                    logger.warning(f"[UIA] PostMessage 注入失败，降级控件级 SendKeys: {pm_ex}")
                    try:
                        search_box.SetFocus()
                        _time_pkg.sleep(0.05)
                        search_box.SendKeys("^a{Delete}", waitTime=0.1)
                        search_box.SendKeys("^v")
                    except Exception as _sk_ex:
                        logger.warning(f"[UIA] 控件级 SendKeys 也失败: {_sk_ex}")
            elif not search_ok:
                try:
                    search_box.SetFocus()
                    search_box.SendKeys("^a{Delete}", waitTime=0.1)
                except Exception:
                    pass
            random_delay(0.3, 0.5)

            if attempt == 0 and not tried_rects:
                try:
                    focused = uia.GetFocusedControl()
                except Exception:
                    focused = None
                is_chat_input = focused and getattr(focused, "ClassName", "") == "mmui::ChatInputField"
                if not is_chat_input:
                    try: uia.SendKeys("{Enter}")
                    except Exception as e: logger.debug(f"[UIA] 快速发送 Enter 异常: {e}")
                    random_delay(0.4, 0.6)
                if driver._verify_chat_switched(session_name, wxid=wxid):
                    return True
                try:
                    driver._ensure_chat_page(force=True)
                    search_box = driver._find_search_box()
                    if search_box:
                        try_click(search_box, max_retries=2, delay=0.1)
                        _time_pkg.sleep(0.05)
                        _vp = None
                        try: _vp = search_box.GetValuePattern()
                        except Exception: pass
                        if _vp:
                            try: _vp.SetValue(session_name_truncated)
                            except Exception: pass
                        elif clipboard_ok:
                            try: search_box.SendKeys("^a{Delete}", waitTime=0.1); search_box.SendKeys("^v")
                            except Exception: pass
                        random_delay(0.3, 0.5)
                except Exception as reset_ex:
                    logger.warning(f"[UIA] 快速回车复位异常: {reset_ex}")

            from .search_helper_utils import get_popover_helper, match_name_helper, evaluate_candidate_score
            target_alias = ""
            try:
                from src.crm.account_data import get_active_account
                from src.utils.contacts_cache import contacts_cache
                target_alias = next((f.get("alias") or "" for f in (contacts_cache.get_friends(get_active_account()) or []) if f.get("wxid") == wxid), "")
            except Exception as e_alias:
                logger.debug(f"[UIA] 查询联系人别名失败: {e_alias}")
            switched = False
            max_wait_seconds = 3.0
            start_time = _time_pkg.time()
            has_local_group = False
            first_candidate_name = None
            loop_best_candidate = loop_best_candidate_real_name = loop_best_candidate_rect = None
            loop_max_score = -100

            while _time_pkg.time() - start_time < max_wait_seconds:
                pop = get_popover_helper(driver)
                if pop:
                    try:
                        autoids = ([f"search_item_{wxid}", f"search_item_function_{wxid}"] if wxid else []) + [f"search_item_{session_name}", f"search_item_function_{session_name}"]
                        for autoid in autoids:
                            target_ctrl = pop.Control(AutomationId=autoid)
                            if target_ctrl.Exists(0.05):
                                rect = safe_bounding_rect(target_ctrl)
                                rect_tuple = (rect.left, rect.top, rect.right, rect.bottom) if rect else None
                                if rect_tuple and rect_tuple in tried_rects:
                                    continue
                                logger.info(f"[UIA] 快速通过 AutomationId '{autoid}' 精准匹配到目标控件，尝试点击 (wxid: {wxid})")
                                try_click(target_ctrl, max_retries=2, delay=0.1)
                                random_delay(0.3, 0.5)
                                if driver._verify_chat_switched(session_name, real_name=session_name, wxid=wxid):
                                    logger.info(f"[UIA] 通过 AutomationId 精准匹配成功切换会话: {session_name}")
                                    return True
                                else:
                                    if rect_tuple:
                                        tried_rects.add(rect_tuple)
                                    break
                    except Exception as e:
                        logger.debug(f"[UIA] 快速 AutomationId 定位异常: {e}")

                    best_candidate = best_candidate_real_name = best_candidate_rect = None
                    max_score, current_group_name = -100, ""
                    exclude_keywords = ["搜索网络结果", "搜一搜", "最近在搜", "小程序", "公众号", "服务号", "聊天记录", "最常使用的小程序", "查找微信号", "查找"]

                    for ctrl, depth in safe_walk_control(pop, max_depth=6):
                        try:
                            name = safe_get_name(ctrl)
                            if name and "查看全部" in name and current_group_name and any(gk in current_group_name for gk in ["联系人", "群聊", "微信群", "功能", "服务号"]):
                                logger.info(f"[UIA] 发现展开更多按钮 '{name}'，点击以显示所有搜索结果...")
                                try_click(ctrl, max_retries=2, delay=0.15)
                                random_delay(0.3, 0.5)
                                break

                            ctrl_type = safe_control_type(ctrl)
                            CLASSIFY_TITLES = ["联系人", "群聊", "最常使用的小程序", "搜一搜", "搜索网络结果", "最近在搜", "聊天记录", "公众号", "微信群", "企业", "功能", "服务号"]
                            if name and name in CLASSIFY_TITLES:
                                current_group_name = name
                                if name in ["联系人", "群聊", "微信群", "企业", "文件传输助手", "功能"]:
                                    has_local_group = True
                                continue

                            if ctrl_type in ['ButtonControl', 'CustomControl', 'ListItemControl', 'PaneControl']:
                                rect = safe_bounding_rect(ctrl)
                                if not rect or (rect.right - rect.left) <= 5 or (rect.bottom - rect.top) <= 5: continue
                                rect_tuple = (rect.left, rect.top, rect.right, rect.bottom)
                                if rect_tuple in tried_rects: continue

                                if name and any(kw in name for kw in exclude_keywords): continue

                                score, matched_text = evaluate_candidate_score(
                                    ctrl, name, ctrl_type, current_group_name,
                                    session_name, session_name_truncated,
                                    wxid, target_alias, exclude_keywords
                                )
                                if score is None:
                                    continue
                                    
                                if first_candidate_name is None:
                                    first_candidate_name = matched_text

                                if score > max_score:
                                    max_score = score
                                    best_candidate = ctrl
                                    best_candidate_real_name = matched_text
                                    best_candidate_rect = rect_tuple
                        except Exception:
                            continue
                    
                    if best_candidate and max_score > loop_max_score:
                        loop_max_score = max_score
                        loop_best_candidate = best_candidate
                        loop_best_candidate_real_name = best_candidate_real_name
                        loop_best_candidate_rect = best_candidate_rect

                    if best_candidate and max_score >= 80:
                        candidate_name = best_candidate_real_name or safe_get_name(best_candidate).strip()
                        logger.info(f"[UIA] 轮询匹配到高分项: '{candidate_name}'，得分: {max_score}，尝试点击")
                        try_click(best_candidate, max_retries=2, delay=0.1)
                        random_delay(0.3, 0.5)
                        if driver._verify_chat_switched(session_name, real_name=candidate_name, wxid=wxid):
                            logger.info(f"[UIA] 轮询匹配成功切换到会话: {session_name}")
                            switched = True
                            break
                        else:
                            if best_candidate_rect:
                                tried_rects.add(best_candidate_rect)
                            break
                
                _time_pkg.sleep(0.15)

            if switched:
                return True

            if loop_best_candidate and loop_max_score > 0 and loop_best_candidate_rect not in tried_rects:
                candidate_name = loop_best_candidate_real_name or safe_get_name(loop_best_candidate).strip()
                logger.info(f"[UIA] 轮询未匹配到高分项，采用兜底最佳项: '{candidate_name}'，得分: {loop_max_score}")
                try_click(loop_best_candidate, max_retries=2, delay=0.1)
                random_delay(0.5, 0.8)
                if driver._verify_chat_switched(session_name, real_name=candidate_name, wxid=wxid):
                    logger.info(f"[UIA] 兜底匹配切换成功: {session_name}")
                    return True
                else:
                    if loop_best_candidate_rect:
                        tried_rects.add(loop_best_candidate_rect)

        logger.warning(f"[UIA] 无法切换到会话 '{session_name}'，已达到最大尝试次数。")
        try:
            uia.SendKeys("{Escape}")
        except:
            pass
        return False

    except Exception as e:
        logger.error(f"[UIA] 搜索切换会话异常: {e}")
        try:
            uia.SendKeys("{Escape}")
        except:
            pass
        return False
