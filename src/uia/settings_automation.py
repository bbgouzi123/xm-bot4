import logging
import time
import re
import uiautomation as uia
from src.uia.retry import try_click

logger = logging.getLogger(__name__)

def safe_walk_control(control, max_depth=12):
    """安全遍历控件树，自动捕获并忽略 COM 遍历相关的致命异常 (如 COMError)，保障流程稳定性。"""
    if not control:
        return

    def _dfs(curr, current_depth):
        if current_depth > max_depth:
            return

        children = []
        try:
            children = curr.GetChildren()
        except Exception:
            # 容忍任何 COMError 或内部不可访问节点
            return

        for child in children:
            try:
                # 触发属性读取，确保控件尚未失效
                _ = child.Name
                _ = child.ControlTypeName
                yield child, current_depth
            except Exception:
                continue

            yield from _dfs(child, current_depth + 1)

    yield from _dfs(control, 1)

def clean_name(name):
    if not name: return ""
    return re.sub(r'[\u200b\ufeff\xa0\n\r]', '', name).strip()

def physical_click(ctrl):
    """物理点击控件中心（对 Qt 控件最可靠）"""
    rect = ctrl.BoundingRectangle
    if rect:
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        uia.Click(cx, cy)
        return True
    return False

def detect_toggle_state(toggle):
    """
    检测开关当前状态，多策略叠加。
    返回: True=已开启, False=已关闭, None=无法判断
    """
    # 策略1: TogglePattern (标准 UIA)
    tp = toggle.GetTogglePattern()
    if tp:
        return tp.ToggleState == 1

    # 策略2: LegacyIAccessiblePattern (Qt 常用)
    lp = toggle.GetLegacyIAccessiblePattern()
    if lp:
        try:
            # 0x10 = STATE_SYSTEM_CHECKED
            return bool(lp.State & 0x10)
        except:
            pass

    # 策略3: 检查控件 Name 是否包含状态提示
    name = clean_name(toggle.Name)
    if "开" in name or "on" in name.lower():
        return True
    if "关" in name or "off" in name.lower():
        return False

    # 无法判断
    return None

class SettingsAutomation:
    """微信设置自动化 - 自动开启语音转文字"""

    @classmethod
    def ensure_voice_transcription(cls, driver) -> dict:
        """开启语音消息自动转文字（智能跳过重复开启）"""
        import os
        import json
        from pathlib import Path
        
        wxid = getattr(driver, "_wxid", None)
        if not wxid and driver._connected:
            # 兜底：如果还没提取到 wxid，尝试从 driver 获取
            try:
                user_info = driver.get_current_user()
                wxid = user_info.get("wxid")
            except: pass
            
        wxid_str = str(wxid) if wxid else "default"
        
        # 获取本地电脑指纹并生成唯一匹配键
        from src.utils.license_validator.machine import MachineMixin
        computer_fingerprint = MachineMixin.get_machine_code()
        unique_key = f"voice_trans_pc_{computer_fingerprint}_wx_{wxid_str}"
        
        # 1. 优先从本地持久化文件读取状态，跳过任何云端或I/O延迟
        local_status_file = Path.home() / ".xm-ai-bot" / "voice_trans_status.json"
        local_enabled = False
        if local_status_file.exists():
            try:
                with open(local_status_file, "r", encoding="utf-8") as f:
                    status_dict = json.load(f)
                if status_dict.get(unique_key):
                    local_enabled = True
            except Exception as e:
                logger.warning(f"[配置] 读取本地语音转文字状态文件失败: {e}")

        if local_enabled:
            logger.info(f"[智能跳过] 检测到本台电脑上账号 {wxid_str} 已配置过语音转文字，跳过 UIA 操作")
            return {"success": True, "skipped": True}

        # 2. 从云端同步内存缓存拉取双重验证
        from src.utils.config_cache import config_cache
        if config_cache.get(unique_key):
            cls._save_local_status(local_status_file, unique_key)
            logger.info(f"[智能跳过] 检测到云端记录中本台电脑上账号 {wxid_str} 已配置过语音转文字，已同步至本地，跳过 UIA 操作")
            return {"success": True, "skipped": True}

        # 在执行前台 UIA 交互前，确保微信已被强力置顶
        if hasattr(driver, "hwnd") and driver.hwnd:
            try:
                from src.uia.retry.window_ops import ensure_wechat_foreground
                ensure_wechat_foreground(driver.hwnd)
            except Exception as e:
                logger.warning(f"[配置] 置顶微信窗口失败: {e}")

        from src.utils.uia_task_runner import run_uia_task
        with run_uia_task("配置微信语音转文字", priority=10):
            res = cls._do_ensure_voice_transcription(driver)
            if res.get("success"):
                # 执行成功后，双写持久化到本地文件及内存/云端缓存
                cls._save_local_status(local_status_file, unique_key)
                config_cache.set(unique_key, True)
            return res

    @classmethod
    def _save_local_status(cls, file_path, unique_key: str):
        """将成功配置状态落盘"""
        import json
        status_dict = {}
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    status_dict = json.load(f)
            except Exception:
                pass
        
        status_dict[unique_key] = True
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(status_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[配置] 写入本地语音转文字状态文件失败: {e}")

    @classmethod
    def _do_ensure_voice_transcription(cls, driver) -> dict:
        try:
            logger.info("准备自动配置微信[语音转文字]服务...")
            desktop = uia.GetRootControl()

            # 实时从窗口句柄获取最稳定且健康的根控件，规避长生命周期缓存 driver.root 的 COM 指针失效问题
            root_ctrl = None
            if hasattr(driver, "hwnd") and driver.hwnd:
                try:
                    root_ctrl = uia.ControlFromHandle(driver.hwnd)
                except Exception:
                    pass
            if not root_ctrl:
                root_ctrl = driver.root

            if not root_ctrl:
                return {"success": False, "reason": "未找到微信主窗口根控件，无法进行自动化配置"}

            # ===== 第1步: 点击"更多" =====
            more_btn = None
            for ctrl, d in safe_walk_control(root_ctrl, max_depth=12):
                if ctrl.ControlTypeName == "ButtonControl" and clean_name(ctrl.Name) in ("更多", "设置及其他"):
                    more_btn = ctrl
                    break

            if more_btn:
                logger.info("物理点击'更多'按钮...")
                physical_click(more_btn)
            else:
                r = root_ctrl.BoundingRectangle
                if not r: return {"success": False, "reason": "无法获取窗口坐标"}
                uia.Click(r.left + 30, r.bottom - 30)

            time.sleep(1.5)

            # ===== 第2步: 点击"设置" =====
            setting_btn = None
            for ctrl, d in safe_walk_control(root_ctrl, max_depth=12):
                cname = clean_name(ctrl.Name)
                if cname == "设置" and ctrl.ControlTypeName in ("ButtonControl", "MenuItemControl", "TextControl"):
                    setting_btn = ctrl
                    break

            if setting_btn:
                logger.info("物理点击'设置'菜单项...")
                physical_click(setting_btn)
            else:
                r = root_ctrl.BoundingRectangle
                uia.Click(r.left + 30, r.bottom - 55)

            time.sleep(2.5)

            # ===== 第3步: 捕获 PreferenceWindow =====
            settings_win = None
            for _ in range(10):
                for w in desktop.GetChildren():
                    if "PreferenceWindow" in (w.ClassName or ""):
                        settings_win = w
                        break
                if settings_win: break
                time.sleep(0.3)

            if not settings_win:
                return {"success": False, "reason": "设置窗口未弹出"}

            settings_win.SetActive()
            logger.info("成功捕获设置窗口")

            # ===== 第4步: 物理点击"通用"按钮 (mmui::XButton) =====
            general_btn = None
            for ctrl, d in safe_walk_control(settings_win, max_depth=6):
                cname = clean_name(ctrl.Name)
                cclass = ctrl.ClassName or ""
                if cname == "通用" and "XButton" in cclass:
                    general_btn = ctrl
                    break

            if general_btn:
                rect = general_btn.BoundingRectangle
                logger.info(f"物理点击'通用'按钮 ({rect.left},{rect.top})-({rect.right},{rect.bottom})")
                physical_click(general_btn)
            else:
                r = settings_win.BoundingRectangle
                logger.info("坐标点击'通用' (侧边栏第二项)")
                uia.Click(r.left + 90, r.top + 115)

            time.sleep(1.5)

            # ===== 第5步: 寻找"语音消息自动转成文字" =====
            target_label = None
            for attempt in range(4):
                for ctrl, d in safe_walk_control(settings_win, max_depth=12):
                    cname = clean_name(ctrl.Name)
                    if "语音消息自动转成文字" in cname:
                        target_label = ctrl
                        break
                if target_label: break
                sr = settings_win.BoundingRectangle
                if sr:
                    cx = sr.left + (sr.right - sr.left) * 3 // 4
                    cy = sr.top + (sr.bottom - sr.top) // 2
                    uia.SetCursorPos(cx, cy)
                    uia.WheelDown(5, waitTime=0.3)
                time.sleep(0.5)

            if not target_label:
                cls._close_settings(settings_win)
                return {"success": False, "reason": "未找到'语音消息自动转成文字'"}

            logger.info("已定位到目标选项")

            # ===== 第6步: 定位开关并安全开启 =====
            toggle = None
            parent = target_label.GetParentControl()
            if parent:
                for child in parent.GetChildren():
                    ct = child.ControlTypeName or ""
                    cc = child.ClassName or ""
                    if ct in ("CheckBoxControl", "ButtonControl") or "Switch" in cc:
                        toggle = child
                        break
            if not toggle:
                toggle = target_label.GetNextSiblingControl()

            if toggle:
                state = detect_toggle_state(toggle)
                if state is True:
                    # 已经是 ON，无需操作
                    logger.info("开关已为 ON，无需操作！直接跳过")
                elif state is False:
                    # 确认是 OFF，安全开启
                    logger.info("开关为 OFF，物理点击开启...")
                    physical_click(toggle)
                    time.sleep(0.3)
                    # 验证：点击后再检测一次
                    verify = detect_toggle_state(toggle)
                    if verify is True:
                        logger.info("验证通过：开关已切换为 ON")
                    else:
                        logger.info(f"点击后状态: {verify}（无法精确验证，但已执行点击操作）")
                else:
                    # state is None - 无法判断当前状态
                    # 安全策略：通过颜色/像素来判断。但无法做到时，宁可不点！
                    # 因为盲点可能把已开启的开关关闭。
                    logger.warning("无法判断开关当前状态，为避免误关闭，跳过点击操作")
                    logger.warning("请手动确认微信设置 -> 通用 -> 聊天中的语音消息自动转成文字 是否已开启")
                    cls._close_settings(settings_win)
                    return {"success": True, "warning": "无法自动检测开关状态，请手动确认"}
            else:
                # 找不到开关控件 - 也不盲点，报告给用户
                logger.warning("未找到开关控件，无法自动操作")
                cls._close_settings(settings_win)
                return {"success": False, "reason": "未找到开关控件，请手动开启"}

            time.sleep(0.5)
            cls._close_settings(settings_win)
            logger.info("=== 自动化配置完成 ===")
            return {"success": True}

        except Exception as e:
            logger.error(f"异常: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "reason": str(e)}

    @classmethod
    def _close_settings(cls, settings_win):
        try:
            settings_win.SetActive()
            settings_win.SendKeys("{Esc}")
            time.sleep(0.3)
            if settings_win.Exists(0.2):
                settings_win.SendKeys("{Alt}{F4}")
        except: pass
