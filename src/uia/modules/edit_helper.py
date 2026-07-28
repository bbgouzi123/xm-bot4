import logging
import re
from typing import Optional
import uiautomation as uia

from src.uia.session import clean_session_name
from src.uia.modules.edit_helper_verify import verify_by_input_name, verify_chat_by_history_impl

logger = logging.getLogger("WeChatDriver.EditHelper")

def _get_header_title_safely(container) -> str:
    """
    安全且带剪枝地提取顶部标题栏文本。
    在 DFS 遍历中剪掉 mmui::RecyclerListView 等庞大消息历史列表子树，
    保证 100% 提取到真正的顶部会话标题，而绝不会错抓消息记录气泡中的文字。
    """
    # 🚀 优先直接使用 AutomationId 提取，速度极快且不卡顿
    try:
        title_ctrl = container.TextControl(AutomationId="content_view.top_content_view.title_h_view.left_v_view.left_content_v_view.left_ui_.big_title_line_h_view.current_chat_name_label", searchDepth=12)
        if title_ctrl.Exists(0.05):
            t_name = title_ctrl.Name
            if t_name:
                return t_name.strip()
    except Exception:
        pass

    try:
        from src.utils.safe_uia import safe_get_children, safe_get_name, safe_class_name, safe_control_type
        candidates = []
        stack = [(container, 0)]
        while stack:
            curr, depth = stack.pop()
            if depth > 0:
                try:
                    c_cls = safe_class_name(curr)
                    c_type = safe_control_type(curr)
                    
                    # 🚀 黄金剪枝规则：剪掉所有聊天消息记录列表子树，大幅减少节点扫描并绝不抓错气泡文字
                    if "ListView" in c_cls or "RecyclerListView" in c_cls or c_type == "ListControl" or c_cls == "mmui::RecyclerListView":
                        continue
                        
                    if c_type == 'TextControl' or 'XTextView' in c_cls:
                        t_name = safe_get_name(curr).strip()
                        # 排除非标题文本
                        if t_name and not t_name.startswith("微信号") and t_name != "聊天信息":
                            if 'XTextView' in c_cls:
                                return t_name
                            candidates.append(t_name)
                except Exception:
                    continue
            
            # 标题栏通常在非常浅的前 16 层结构中
            if depth < 16:
                children = safe_get_children(curr)
                if children:
                    for child in reversed(children):
                        stack.append((child, depth + 1))
        if candidates:
            return candidates[0]
    except Exception:
        pass
    return ""

def get_edit_control_impl(self, who: str) -> Optional[uia.EditControl]:
    """获取聊天文本输入框，对输入框的 Name 属性及聊天头部标题进行严格的名字模式校验"""
    if who == "filehelper":
        who = "文件传输助手"
    search_who = clean_session_name(who)
    
    # 自动剥离末尾的省略号
    clean_prefix = re.sub(r'(\.\.\.|…)+$', '', search_who).strip()
    is_truncated = (clean_prefix != search_who)
    
    # 定位聊天容器 以大幅缩小 UIA 搜索范围
    from src.utils.safe_uia import get_chat_container_safely
    chat_container = get_chat_container_safely(self.root)
    if not chat_container:
        return None
    
    # 1. 尝试使用全名及语音后缀在 UIA 中直接查找（若被截断，不适用这种完全匹配）
    if not is_truncated:
        suffix = " 按住 Ctrl + Win  使用语音输入文字"
        for name_val in [f"{search_who}{suffix}", search_who]:
            edit = chat_container.EditControl(Name=name_val, searchDepth=16)
            if edit.Exists(0.1): 
                return edit
 
    # 2. 增强正则匹配：将空格替换为 \s+ 支持连续或不规范的空格，支持普通会话、企业微信后缀、可选的群聊人数括号、以及可选的语音输入提示后缀
    escaped_prefix = re.escape(clean_prefix).replace(r'\ ', r'\s+')
    escaped_who = re.escape(search_who).replace(r'\ ', r'\s+')
    if is_truncated and len(clean_prefix) >= 2:
        pattern = re.compile(r'^' + escaped_prefix + r'.*?(@[^@\s\(\)]+)?(\s*[（(]\d+[）)])?(\s+按住.*)?$')
    else:
        pattern = re.compile(r'^' + escaped_who + r'(@[^@\s\(\)]+)?(\s*[（(]\d+[）)])?(\s+按住.*)?$')
 
    # 辅助函数：提取顶部标题栏的名字，做双重校验
    def _get_header_title(container) -> str:
        return _get_header_title_safely(container)
 
    # 🌟 2. 快速定位：优先查找 chat_container 中 AutomationId="chat_input_field" 或是 ClassName="mmui::ChatInputField" 的输入框
    edit = chat_container.EditControl(AutomationId="chat_input_field", searchDepth=16)
    if not edit.Exists(0.05):
        edit = chat_container.EditControl(ClassName="mmui::ChatInputField", searchDepth=16)
    if edit.Exists(0.15):
        ctrl_name = (edit.Name or "").strip()
        # 正则匹配成功，或者包含目标名称兜底，或者顶部标题栏名字匹配（兼容草稿覆盖输入框 Name 的场景）
        if ctrl_name and (pattern.match(ctrl_name) or search_who in ctrl_name or (is_truncated and clean_prefix in ctrl_name)):
            logger.info(f"[UIA] 快速成功通过 ClassName 和正则匹配到输入框: Name='{ctrl_name}' (目标='{search_who}')")
            return edit
        
        # 读取顶部标题栏进行双重匹配
        header_name = _get_header_title(chat_container)
        if header_name and (pattern.match(header_name) or search_who in header_name or (is_truncated and clean_prefix in header_name)):
            logger.info(f"[UIA] 快速匹配：输入框 Name='{ctrl_name}' 不匹配，但顶部标题 '{header_name}' 正确，判定当前会话已切换成功")
            return edit
 
    # 辅助函数：安全且带剪枝寻找输入框
    def _find_edit_control_safely(container) -> Optional[uia.EditControl]:
        if not container:
            return None
        try:
            from src.utils.safe_uia import safe_get_children, safe_control_type, safe_get_name, safe_class_name
            stack = [(container, 0)]
            while stack:
                ctrl, depth = stack.pop()
                if depth > 0:
                    try:
                        c_cls = safe_class_name(ctrl)
                        c_type = safe_control_type(ctrl)
                        
                        # 🚀 黄金剪枝规则：剪掉所有聊天消息记录列表子树，防止深层遍历卡死并提速数百倍
                        if "ListView" in c_cls or "RecyclerListView" in c_cls or c_type == "ListControl" or c_cls == "mmui::RecyclerListView":
                            continue
                            
                        if c_type == 'EditControl':
                            ctrl_name = safe_get_name(ctrl).strip()
                            if ctrl_name and (pattern.match(ctrl_name) or search_who in ctrl_name or (is_truncated and clean_prefix in ctrl_name)):
                                logger.info(f"[UIA] 通过正则/包含成功匹配到正确的输入框: Name='{ctrl_name}' (目标='{search_who}')")
                                return ctrl
                            
                            # 兼容草稿：只要类名符合输入框类名，且标题匹配
                            if c_cls == "mmui::ChatInputField" or getattr(ctrl, 'AutomationId', '') == "chat_input_field":
                                header_name = _get_header_title(container)
                                if header_name and (pattern.match(header_name) or search_who in header_name or (is_truncated and clean_prefix in header_name)):
                                    logger.info(f"[UIA] 兜底匹配：输入框 Name='{ctrl_name}' 不匹配，但类名和顶部标题 '{header_name}' 正确")
                                    return ctrl
                    except Exception:
                        continue
                
                # 输入框所在的层级很浅，搜索限制在 8 层
                if depth < 8:
                    children = safe_get_children(ctrl)
                    if children:
                        for child in reversed(children):
                            stack.append((child, depth + 1))
        except Exception as e:
            logger.error(f"[UIA] 遍历匹配输入框异常: {e}")
        return None
 
    # 3. 作为最后的兜底，在缩小的 chat_container 范围内寻找输入框
    edit_ctrl = _find_edit_control_safely(chat_container)
    if edit_ctrl:
        return edit_ctrl
 
    # 4. 轻量刷新 UIA 无障碍树并最后尝试一次
    try:
        from src.uia.startup_flow import force_accessibility_refresh
        force_accessibility_refresh(self.hwnd, self.root, escalate=False)
    except Exception as e:
        logger.debug(f"[UIA] 输入框定位轻量刷新异常: {e}")
 
    try:
        # 重新尝试定位容器
        chat_container = get_chat_container_safely(self.root)
        if chat_container:
            edit_ctrl = _find_edit_control_safely(chat_container)
            if edit_ctrl:
                return edit_ctrl
    except Exception:
        pass


    logger.debug(f"[UIA] 当前聊天页面不是 '{search_who}'，未找到对应的输入框（回复已发送时此日志可忽略）")
    return None

def verify_chat_by_history(self, wxid: str) -> bool:
    """利用数据库历史消息比对当前 UIA 窗口内容，确保切换到了正确的同名联系人，防止错发"""
    return verify_chat_by_history_impl(self, wxid, _get_header_title_safely)


def verify_chat_switched_impl(self, session_name: str, real_name: Optional[str] = None, wxid: Optional[str] = None) -> bool:
    """验证是否成功切换到目标聊天（带短暂渲染延迟容错重试）"""
    import time
    import re
    if session_name == "filehelper" or wxid == "filehelper":
        session_name = "文件传输助手"
    search_who = clean_session_name(session_name)
    
    def normalize_spaces(s: str) -> str:
        return re.sub(r'\s+', ' ', s).strip()
        
    norm_search = normalize_spaces(search_who)
    norm_real = normalize_spaces(clean_session_name(real_name)) if real_name else None
    
    # 微信 4.x 切换页面后无障碍树更新可能存在 100~300ms 延迟，
    # 采用最大 5 次重试（每次间隔 0.15s）确保高延迟及前台渲染不完整时的强健性
    for attempt in range(5):
        # 🚀 1. 黄金预检防线：先用极快极轻量级的“标题对齐与任意输入框存在”进行预检，
        # 绝不首先调用可能触发繁重无障碍刷新的 _get_edit_control！
        try:
            from src.utils.safe_uia import get_chat_container_safely
            chat_container = get_chat_container_safely(self.root)
            if chat_container and chat_container.Exists(0.05):
                header_title = _get_header_title_safely(chat_container)
                if header_title:
                    clean_header = clean_session_name(header_title)
                    clean_header_pure = re.sub(r'[（(]\d+[）)]$', '', clean_header).strip()
                    norm_header = normalize_spaces(clean_header_pure)
                    
                    if (norm_search in norm_header or norm_header in norm_search) or (norm_real and (norm_real in norm_header or norm_header in norm_real)):
                        any_edit = chat_container.EditControl(AutomationId="chat_input_field", searchDepth=16)
                        if not any_edit.Exists(0.05):
                            any_edit = chat_container.EditControl(ClassName="mmui::ChatInputField", searchDepth=16)
                        if any_edit.Exists(0.1):
                            # 开启历史消息精准校验
                            if wxid:
                                if verify_chat_by_history(self, wxid):
                                    logger.info(f"[UIA] verify_chat_switched 快速预检通过且历史匹配成功：顶部标题为 '{header_title}' 且输入框已显现，判定切换成功")
                                    return True
                                else:
                                    logger.debug(f"[UIA] verify_chat_switched 标题对齐但历史比对暂未成功，继续等待微信窗口渲染... attempt={attempt}")
                            else:
                                logger.info(f"[UIA] verify_chat_switched 快速预检通过：顶部标题为 '{header_title}' 且输入框已显现，判定切换成功")
                                return True
        except Exception as check_ex:
            logger.debug(f"[UIA] verify_chat_switched 快速预检标题异常: {check_ex}")

        # 🚀 1.5 输入框 Name 直接比对（WeChat 4.1.7 inspect 确认：chat_input_field.Name = 好友昵称）
        # 当标题栏 AutomationId 读取失败（版本差异）时，此路径作为强健兜底快速校验
        try:
            _result = verify_by_input_name(
                self.root, norm_search, norm_real, search_who, wxid, attempt,
                lambda w: verify_chat_by_history(self, w)
            )
            if _result is True:
                return True
            # _result is None → 继续下一次重试
        except Exception as _ev_ex:
            logger.debug(f"[UIA] verify_chat_switched 输入框Name比对模块异常: {_ev_ex}")


        # 🚀 2. 降级重量级匹配：只有在最后一次尝试时才调用完整匹配，防止因频繁触发无障碍树刷新导致 COM 锁死
        if attempt == 4:
            logger.debug("[UIA] verify_chat_switched 达到最后一次尝试，进行重度输入框及无障碍树刷新匹配")
            edit = self._get_edit_control(search_who)
            # 🔧 [Bug 1 Fix] Exists() 超时从 0.1s 降至 0.05s，减少 WalkControl 在微信 render 异常时的阻塞窗口
            if edit and edit.Exists(0.05):
                if wxid and not verify_chat_by_history(self, wxid):
                    return False
                return True
            if real_name:
                real_search_who = clean_session_name(real_name)
                if real_search_who != search_who:
                    edit = self._get_edit_control(real_search_who)
                    if edit and edit.Exists(0.05):
                        if wxid and not verify_chat_by_history(self, wxid):
                            return False
                        return True
 
        if attempt < 4:
            time.sleep(0.15)
 
    real_name_str = f" (真实可能名称: '{real_name}')" if real_name else ""
    try:
        from src.utils.safe_uia import get_chat_container_safely
        chat_container = get_chat_container_safely(self.root)
        if chat_container:
            actual_title = _get_header_title_safely(chat_container) or "未找到聊天容器/标题"
            actual_edit_name = "未找到输入框"
            # 🔧 [Bug 1 Fix] 失败日志路径 searchDepth 16→8，防止全树遍历触发 COM 挂起
            any_edit = chat_container.EditControl(AutomationId="chat_input_field", searchDepth=8)
            if not any_edit.Exists(0.05):
                any_edit = chat_container.EditControl(ClassName="mmui::ChatInputField", searchDepth=8)
            if any_edit.Exists(0.05):
                actual_edit_name = any_edit.Name or ""
            logger.warning(f"[UIA] 切换会话 '{search_who}'{real_name_str} 校验未通过，输入框未显现或名字不匹配。当前实际顶部标题: '{actual_title}'，当前输入框Name: '{actual_edit_name}'")
        else:
            logger.warning(f"[UIA] 切换会话 '{search_who}'{real_name_str} 校验未通过，聊天容器不存在。")
    except Exception as log_ex:
        logger.warning(f"[UIA] 切换会话 '{search_who}'{real_name_str} 校验未通过，输入框未显现或名字不匹配。异常: {log_ex}")
    return False

