import logging
from src.utils.contacts_cache import contacts_cache

logger = logging.getLogger(__name__)

class EvaluatorContactMixin:
    """联系人库映射、公众号屏蔽与迎新通知判定"""

    def _check_friend_acceptance(self, last_msg: str, last_time: str) -> bool:
        is_friend_accept_notify = (
            ("现在可以开始聊天了" in last_msg and ("你已添加了" in last_msg or "已同意你的好友" in last_msg)) 
            or "我通过了你的朋友验证请求" in last_msg
        ) if last_msg else False

        if is_friend_accept_notify and last_time:
            last_time_strip = str(last_time).strip().lower()
            old_keywords = ["昨天", "前天", "星期", "周", "月", "日", "年", "-", "/", "yesterday"]
            en_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            is_old = any(kw in last_time_strip for kw in old_keywords) or any(d in last_time_strip for d in en_days)
            if is_old:
                is_friend_accept_notify = False
        return is_friend_accept_notify

    def _check_known_contact(self, name: str, account_id: str, fp: str) -> bool:
        friends_list = contacts_cache.get_friends(account_id)
        groups_list = contacts_cache.get_groups(account_id)
        if friends_list and len(friends_list) > 0:
            is_known = False
            name_stripped = name.strip()
            for f in friends_list:
                f_name = (f.get("name") or "").strip()
                f_remark = (f.get("remark") or "").strip()
                if f_name == name_stripped or f_remark == name_stripped:
                    is_known = True
                    break
            if not is_known:
                for g in groups_list:
                    g_name = (g.get("name") or "").strip()
                    if g_name == name_stripped:
                        is_known = True
                        break
                        
            if not is_known:
                from src.uia.session import session_type_cache
                is_official = (
                    session_type_cache.get_type(name) == "official_account"
                    or any(kw in name_stripped for kw in ("公众号", "服务号", "订阅号", "腾讯", "微信", "支付", "通知", "系统"))
                )
                if is_official:
                    print(f"[扫描拦截] '{name}' 判定为系统/公众号，跳过并加指纹")
                    self._fingerprints.setdefault(name, set()).add(fp)
                    return False
        else:
            if account_id not in getattr(self, "_preheated_accounts", set()):
                return False
        return True

    def _is_official_account(self, name: str) -> bool:
        from src.uia.session import session_type_cache
        name_stripped = name.strip()
        is_official = (
            session_type_cache.get_type(name) == "official_account"
            or any(kw in name_stripped for kw in ("公众号", "服务号", "订阅号", "腾讯", "微信", "支付", "通知", "系统"))
        )
        return is_official
