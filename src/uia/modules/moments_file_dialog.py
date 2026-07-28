"""
朋友圈发图文件选择对话框辅助函数 — 从 moments.py 拆分

功能：在 Windows 系统文件对话框（#32770）中填入图片路径并点击打开。
"""
import os
import logging
import uiautomation as uia

from src.uia.retry import try_click, random_delay

logger = logging.getLogger("WeChatDriver")


def select_files_via_dialog(image_paths: list, close_moments_fn, moment_window) -> bool:
    """在系统文件选择对话框中填入图片路径并点击打开。

    Args:
        image_paths: 待填入的图片路径列表
        close_moments_fn: 关闭朋友圈窗口的回调（用于失败时清理）
        moment_window: 朋友圈窗口控件（用于清理时关闭）

    Returns:
        True 表示成功填入并点击打开，False 表示失败。
    """
    file_dialog = uia.WindowControl(Name='打开', ClassName='#32770')
    if not file_dialog.Exists(8, 1):
        file_dialog2 = uia.WindowControl(ClassName='#32770')
        if file_dialog2.Exists(3, 1):
            file_dialog = file_dialog2
            logger.info("[朋友圈] 文件对话框通过 ClassName 兜底匹配成功")
        else:
            logger.error(
                "[朋友圈] ⚠️ 文件选择对话框未弹出（等待 11 秒超时）。"
                "可能原因：①微信版本变更了发图交互，②发表按钮点击未触发文件选择。"
            )
            close_moments_fn(moment_window)
            return False

    valid_paths = [p for p in image_paths if os.path.exists(p)]
    if not valid_paths:
        logger.error(f"[朋友圈] ⚠️ 全部图片路径均无效（共 {len(image_paths)} 张）")
        uia.SendKeys('{Esc}')
        close_moments_fn(moment_window)
        return False

    file_str = " ".join([f'"{os.path.abspath(p)}"' for p in valid_paths])
    logger.info(f"[朋友圈] 准备填入文件路径，共 {len(valid_paths)} 张图片")

    edit_ctrl = file_dialog.EditControl(Name='文件名(N):')
    if not edit_ctrl.Exists(3):
        edit_ctrl = file_dialog.EditControl()
        if not edit_ctrl.Exists(1):
            logger.error("[朋友圈] ⚠️ 文件对话框中未找到文件名输入框，中止发布")
            uia.SendKeys('{Esc}')
            close_moments_fn(moment_window)
            return False
    edit_ctrl.SendKeys(file_str, waitTime=0.1)
    random_delay(0.5, 0.8)

    open_btn = file_dialog.ButtonControl(Name='打开(O)')
    if not open_btn.Exists(2):
        for btn_name_try in ['打开', 'Open', 'Open(O)', '确定']:
            open_btn = file_dialog.ButtonControl(Name=btn_name_try)
            if open_btn.Exists(1):
                logger.info(f"[朋友圈] 通过备选名称 '{btn_name_try}' 匹配到打开按钮")
                break
        else:
            logger.error("[朋友圈] ⚠️ 文件对话框中未找到「打开」按钮，中止发布")
            uia.SendKeys('{Esc}')
            close_moments_fn(moment_window)
            return False

    try_click(open_btn)
    return True
