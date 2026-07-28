import time
import logging
import uiautomation as uia

logger = logging.getLogger("RedPacket")

# 缓存已经点击过的红包和转账，避免重复点击
# 格式为: f"{session_id}_{left}_{top}_{right}_{bottom}_{name}"
_clicked_red_packets = set()

def claim_redpacket_impl(driver, session_id: str, is_group: bool) -> bool:
    """
    自动抢红包及转账领取的 UIA 实现逻辑。
    """
    if not driver.is_connected():
        logger.warning("[红包/转账] 微信未连接，无法执行自动领取")
        return False

    # 1. 尝试实时查询数据库获取红包或转账消息
    db_success = False
    db_msg_content = None
    try:
        from src.wechat_4x.wcdb_monitor import get_wcdb_monitor
        monitor = get_wcdb_monitor(driver._account_id)
        if monitor and monitor.is_active():
            recent_msgs = monitor.get_latest_messages(session_id, limit=5)
            if recent_msgs:
                for msg in recent_msgs:
                    msg_content = msg.get("content", "")
                    msg_content_lower = msg_content.lower()
                    if any(x in msg_content or x in msg_content_lower for x in ("[微信红包]", "[红包]", "<wcpayinfo>", "微信红包", "[转账]", "微信转账", "待你收款")):
                        db_success = True
                        db_msg_content = msg_content
                        break
    except Exception as db_ex:
        logger.warning(f"[红包/转账] 实时查询 WCDB 数据库失败，将自动走兜底 UIA 识别: {db_ex}")

    if db_success:
        logger.info(f"[红包/转账] 成功实时查询数据库定位到支付消息，准备执行点击领取。消息摘要: {db_msg_content[:150]}")
    else:
        logger.info(f"[红包/转账] 数据库无法查询（未连接、无匹配消息或解密失败），走兜底 UIA 识别")

    # 2. 确保微信在前台
    try:
        from src.uia.retry.window_ops import ensure_wechat_foreground
        ensure_wechat_foreground(driver.hwnd)
    except Exception as e:
        logger.error(f"[红包/转账] 强行置前窗口异常: {e}")

    # 将 wxid 映射为显示名称
    from src.wechat_4x.wcdb_monitor_helpers import resolve_display_name
    name = resolve_display_name(driver._account_id, session_id, is_group) or session_id

    # 3. 切换到对应的会话窗口
    logger.info(f"[红包/转账] 正在切换到会话: {name} (wxid: {session_id})")
    if not driver.ChatWith(name, wxid=session_id):
        logger.warning(f"[红包/转账] 切换到会话 {name} 失败")
        return False

    # 4. 在聊天消息列表中查找红包或转账气泡
    from src.uia.elements import WxName
    try:
        msg_list = driver._walk_find('ListControl', name=WxName.MESSAGE_LIST, class_name='mmui::RecyclerListView', max_depth=8) or driver._walk_find('ListControl', name=WxName.MESSAGE_LIST, max_depth=8)
        if not msg_list:
            logger.warning("[红包/转账] 未找到聊天消息列表控件")
            return False

        children = list(msg_list.GetChildren())
        if not children:
            logger.warning("[红包/转账] 聊天消息列表为空")
            return False

        # 过滤出红包与待领取的转账气泡
        red_packet_bubbles = []
        for child in children:
            bubble_name = child.Name or ""
            is_valid = False
            if "微信红包" in bubble_name and (child.ClassName == "mmui::ChatBubbleItemView" or child.ControlType == uia.ControlType.ListItemControl):
                is_valid = True
            elif ("微信转账" in bubble_name or "转账" in bubble_name) and "待你收款" in bubble_name and (child.ClassName == "mmui::ChatBubbleItemView" or child.ControlType == uia.ControlType.ListItemControl):
                is_valid = True
                
            if is_valid:
                red_packet_bubbles.append(child)

        if not red_packet_bubbles:
            logger.info("[红包/转账] 当前可见消息中没有找到未领取的微信红包或转账")
            return False

        target_bubbles = red_packet_bubbles[-3:]
        claimed_any = False

        for bubble in target_bubbles:
            rect = bubble.BoundingRectangle
            bubble_name = bubble.Name or ""
            fp = f"{session_id}_{rect.left}_{rect.top}_{rect.right}_{rect.bottom}_{bubble_name}"

            if fp in _clicked_red_packets:
                continue

            _clicked_red_packets.add(fp)
            logger.info(f"[红包/转账] 发现未处理支付气泡: '{bubble_name}'，坐标: {rect}，执行点击")

            # 触发点击/双击以弹出窗口
            is_transfer = "待你收款" in bubble_name
            try:
                clicked_btn = False
                if is_transfer:
                    # 尝试查找“收款”按钮并点击
                    for desc in bubble.GetDescendants():
                        if desc.Name == "收款" and (desc.ClassName == "mmui::XTextView" or desc.ControlType == uia.ControlType.ButtonControl or desc.ControlType == uia.ControlType.TextControl):
                            logger.info(f"[转账] 找到气泡内'收款'按钮，执行点击")
                            desc.Click()
                            clicked_btn = True
                            break
                if not clicked_btn:
                    bubble.DoubleClick(simulateMove=False)
                time.sleep(1.0) # 等待弹窗弹出
            except Exception as d_ex:
                logger.error(f"[红包/转账] 触发卡片弹窗异常: {d_ex}")
                continue

            # 5. 寻找红包或转账弹窗 (由于均是 Qt51514QWindowIcon 类，故进行匹配)
            dialog = None
            start_w = time.time()
            while time.time() - start_w < 2.5:
                for root_child in uia.GetRootControl().GetChildren():
                    if root_child.ClassName == "Qt51514QWindowIcon" and root_child.NativeWindowHandle != driver.hwnd:
                        dialog = root_child
                        break
                if dialog:
                    break
                time.sleep(0.1)

            if not dialog:
                logger.warning("[红包/转账] 未检测到详情弹窗弹出")
                continue

            # 6. 检测弹窗状态与是否已领完
            is_claimed_or_expired = False
            is_transfer_dialog = False
            for desc in dialog.GetDescendants():
                desc_name = desc.Name or ""
                if any(word in desc_name for word in ("手慢了", "红包派完了", "已失效", "已领完", "红包详情", "已拆开", "过期", "你已收款", "已存入零钱", "已收")):
                    logger.info(f"[红包/转账] 该红包或转账已失效或已完成: {desc_name}")
                    is_claimed_or_expired = True
                    break
                if any(word in desc_name for word in ("确认收钱", "确认收款", "转账", "交易金额")):
                    is_transfer_dialog = True

            # 7. 执行领取/点击
            if not is_claimed_or_expired:
                if is_transfer_dialog:
                    # 查找确认收钱按钮
                    confirm_btn = None
                    for desc in dialog.GetDescendants():
                        if desc.Name in ("确认收钱", "确认收款", "收钱", "收款"):
                            confirm_btn = desc
                            break
                    if confirm_btn:
                        logger.info(f"[转账] 找到确认收款按钮: '{confirm_btn.Name}'，执行点击")
                        try:
                            confirm_btn.Click()
                        except Exception as click_err:
                            logger.error(f"[转账] 点击确认收款按钮失败: {click_err}")
                    else:
                        d_rect = dialog.BoundingRectangle
                        cx = d_rect.left + (d_rect.right - d_rect.left) // 2
                        cy = d_rect.top + int((d_rect.bottom - d_rect.top) * 0.7)
                        logger.info(f"[转账] 未能找到收款按钮，执行相对坐标物理点击: ({cx}, {cy})")
                        from src.uia.retry.clicks import physical_click
                        try:
                            physical_click(cx, cy)
                        except Exception as pc_err:
                            logger.error(f"[转账] 物理点击按钮失败: {pc_err}")
                else:
                    # 红包点击“开”
                    open_btn = None
                    for desc in dialog.GetDescendants():
                        if desc.Name == "开":
                            open_btn = desc
                            break
                    if open_btn:
                        logger.info("[红包] 找到 '开' 按钮，执行点击")
                        try:
                            open_btn.Click()
                        except Exception as click_err:
                            logger.error(f"[红包] 点击 '开' 按钮失败: {click_err}")
                    else:
                        cover_el = None
                        for desc in dialog.GetDescendants():
                            if "PayRedEnvelope" in (desc.ClassName or "") or "DetailCover" in (desc.ClassName or ""):
                                cover_el = desc
                                break
                        if cover_el:
                            c_rect = cover_el.BoundingRectangle
                            cx = c_rect.left + (c_rect.right - c_rect.left) // 2
                            cy = c_rect.top + int((c_rect.bottom - c_rect.top) * 0.62)
                        else:
                            d_rect = dialog.BoundingRectangle
                            cx = d_rect.left + (d_rect.right - d_rect.left) // 2
                            cy = d_rect.top + int((d_rect.bottom - d_rect.top) * 0.62)
                        from src.uia.retry.clicks import physical_click
                        try:
                            physical_click(cx, cy)
                        except Exception as pc_err:
                            logger.error(f"[红包] 物理点击按钮失败: {pc_err}")

                time.sleep(1.2) # 等待收款/拆包动画

            # 8. 关闭弹窗 (发送 Esc 配合右上角物理点击关闭)
            try:
                logger.info("[红包/转账] 尝试发送 Esc 关闭弹窗")
                dialog.SetFocus()
                uia.SendKeys('{Esc}')
                time.sleep(0.3)
            except Exception as esc_err:
                logger.error(f"[红包/转账] 发送 Esc 关闭弹窗异常: {esc_err}")

            try:
                d_rect = dialog.BoundingRectangle
                if is_transfer_dialog:
                    # 根据 1.log 中的关闭坐标相对偏移计算：右边界偏移 29，顶边界偏移 26
                    close_x = d_rect.right - 29
                    close_y = d_rect.top + 26
                else:
                    # 红包关闭偏移量：右边界偏移 34，顶边界偏移 22
                    close_x = d_rect.right - 34
                    close_y = d_rect.top + 22
                logger.info(f"[红包/转账] 点击右上角关闭按钮: ({close_x}, {close_y})")
                from src.uia.retry.clicks import physical_click
                physical_click(close_x, close_y)
            except Exception as close_err:
                logger.error(f"[红包/转账] 点击右上角关闭按钮异常: {close_err}")

            claimed_any = True
            time.sleep(0.5)

        return claimed_any

    except Exception as e:
        logger.error(f"[红包/转账] 领取主逻辑异常: {e}", exc_info=True)
        return False
