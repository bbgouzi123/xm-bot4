import time
import logging

logger = logging.getLogger("GroupMentionDecay")

class GroupMentionDecayManager:
    """
    群聊艾特热度滑动衰减（暂态放免@）管理器。
    符合大厂工业级 Session 会话管理规范。
    
    核心机制：
    1. 当用户A在群里真实 @ 了机器人，记录并初始化其活跃热度；
    2. 超时时效（冷却期）：规定为 300 秒（5分钟）。如果在 5 分钟内没有任何消息交互，热度超时断开；
    3. 滑动更新（支持长时间连续对话）：在 5 分钟内，只要用户A发送免 @ 消息追问，
       机器人在允许放行的同时，会自动将该会话的 last_active_time 重置刷新为当前时间，支持连续无 @ 聊一小时或更久；
    4. 主动退场（Opt-out）：当检测到“谢谢”、“拜拜”、“再见”等明确的对话结束词时，
       放行最后一次礼貌回复，并在内存中立即清空热度，优雅退场。
    """
    def __init__(self):
        # 结构：{(group_key, user_name): {"last_active_time": float}}
        self._heats = {}

    def record_at(self, group_key: str, user_name: str):
        """
        记录一次真实的 @ 触发，初始化热度
        """
        if not group_key or not user_name:
            return
        key = (group_key, user_name)
        self._heats[key] = {
            "last_active_time": time.time()
        }
        logger.info(f"[热度监控] 群聊 '{group_key}' 用户 '{user_name}' 真实 @ 唤醒，已刷新其连续免 @ 监控热度。")

    def check_and_update_heat(self, group_key: str, user_name: str, message: str) -> bool:
        """
        检查好友在该群聊中是否仍在热度有效期内。
        若仍在有效期，则根据是否匹配结束语决定是否重置或清除热度，并返回 True；否则返回 False。
        """
        if not group_key or not user_name:
            return False
            
        key = (group_key, user_name)
        if key not in self._heats:
            return False
            
        heat = self._heats[key]
        now = time.time()
        
        # 滑动时间窗口超时值：300.0 秒（5分钟）
        session_timeout = 300.0
        
        # 1. 检查是否超时
        if now - heat["last_active_time"] > session_timeout:
            self._heats.pop(key, None)
            logger.info(f"[热度监控] 群聊 '{group_key}' 用户 '{user_name}' 超过 {session_timeout} 秒未发言，热度已超时失效。")
            return False
            
        # 2. 检查是否为主动结束语（Opt-out）
        opt_out_words = {"谢谢", "拜拜", "再见", "不用了", "退退退", "3q", "thanks", "byebye"}
        msg_clean = (message or "").strip().lower()
        if msg_clean in opt_out_words or any(w in msg_clean for w in ["谢谢你", "多谢", "不用帮忙了"]):
            self._heats.pop(key, None)
            logger.info(f"[热度监控] 检测到用户发送了退出词 '{message}'，主动结束连续对话并重置热度。")
            # 返回 True 允许机器人进行最后一次礼貌的告别回复
            return True
            
        # 3. 仍处于有效期，滑动更新活跃时间，重新计算超时，允许无限期对话
        heat["last_active_time"] = now
        logger.info(f"[热度监控] 群聊 '{group_key}' 用户 '{user_name}' 滑动刷新热度，允许继续免 @ 追问。")
        return True

    def record_at_dual(self, group_key: str, user_name: str, sender_wxid: str = None):
        """
        双保险记录一次真实的 @ 触发，同时记录姓名和 WXID 以防脑裂
        """
        if not group_key:
            return
        for k_name in {user_name, sender_wxid} - {None, ""}:
            key = (group_key, k_name)
            self._heats[key] = {
                "last_active_time": time.time()
            }
            logger.info(f"[热度监控] 群聊 '{group_key}' 用户 '{k_name}' 真实 @ 唤醒，已刷新其免 @ 活跃热度。")

    def check_and_update_heat_dual(self, group_key: str, user_name: str, sender_wxid: str, message: str) -> bool:
        """
        双保险检查好友在群聊中是否仍在热度有效期内（支持以 user_name 或 sender_wxid 匹配）
        """
        if not group_key:
            return False
            
        keys_to_check = []
        if user_name:
            keys_to_check.append((group_key, user_name))
        if sender_wxid:
            keys_to_check.append((group_key, sender_wxid))
            
        found_key = None
        for key in keys_to_check:
            if key in self._heats:
                found_key = key
                break
                
        if not found_key:
            return False
            
        heat = self._heats[found_key]
        now = time.time()
        session_timeout = 300.0
        
        if now - heat["last_active_time"] > session_timeout:
            for key in keys_to_check:
                self._heats.pop(key, None)
            logger.info(f"[热度监控] 群聊 '{group_key}' 用户 '{user_name}' ({sender_wxid}) 超时 {session_timeout}s，清除热度。")
            return False
            
        opt_out_words = {"谢谢", "拜拜", "再见", "不用了", "退退退", "3q", "thanks", "byebye"}
        msg_clean = (message or "").strip().lower()
        has_opt_out = msg_clean in opt_out_words or any(w in msg_clean for w in ["谢谢你", "多谢", "不用帮忙了"])
        
        if has_opt_out:
            for key in keys_to_check:
                self._heats.pop(key, None)
            logger.info(f"[热度监控] 检测到群聊退出词 '{message}'，主动结束连续对话。")
            return True
            
        # 刷新所有可能相关的 heats，以确保以后用任意一个 key 校验都能获取到最新的滑动时间
        for key in keys_to_check:
            self._heats[key] = {"last_active_time": now}
        logger.info(f"[热度监控] 群聊 '{group_key}' 用户 '{user_name}' ({sender_wxid}) 滑动刷新热度，允许连续对话。")
        return True

    def clear_heat(self, group_key: str, user_name: str):
        """
        手动清除热度
        """
        key = (group_key, user_name)
        self._heats.pop(key, None)

    def clear_heat_dual(self, group_key: str, user_name: str, sender_wxid: str = None):
        """
        双保险手动清除热度
        """
        for k_name in {user_name, sender_wxid} - {None, ""}:
            key = (group_key, k_name)
            self._heats.pop(key, None)


# 单例实例
group_mention_decay_mgr = GroupMentionDecayManager()
