import logging
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from app.state import account_manager, driver
from src.utils.response import ok

router = APIRouter()
_log = logging.getLogger(__name__)

@router.get("/api/wechat/active-chat-names")
async def get_active_chat_names(instance_id: str = None):
    try:
        target_driver = driver

        target_inst = None
        if instance_id:
            if instance_id in account_manager._instances:
                target_inst = account_manager._instances[instance_id]
            elif isinstance(instance_id, str) and instance_id.isdigit() and int(instance_id) in account_manager._instances:
                target_inst = account_manager._instances[int(instance_id)]
            else:
                for inst in account_manager._instances.values():
                    if inst.wxid == instance_id or (inst.driver and getattr(inst.driver, "_wxid", None) == instance_id):
                        target_inst = inst
                        break

        if target_inst and getattr(target_inst, "driver", None):
            target_driver = target_inst.driver

        if not target_driver or not target_driver.is_connected():
            return ok({"contacts": [], "groups": [], "active": None, "all": [], "error": "微信未连接"})

        def _read_sessions():
            sessions = []
            active_session = None

            try:
                from src.uia.session import parse_session_name, SYSTEM_ACCOUNTS
                root = getattr(target_driver, "root", None)
                if not root:
                    return sessions, active_session

                session_list = None
                for list_name in ("会话", "消息", "SessionList", "Chats"):
                    try:
                        candidate = root.ListControl(Name=list_name)
                        if candidate and candidate.Exists(0):
                            session_list = candidate
                            break
                    except Exception:
                        pass

                if not session_list:
                    return sessions, active_session

                children = session_list.GetChildren()
                for child in children[:25]:
                    try:
                        raw_name = (child.Name or "").strip()
                        if not raw_name:
                            continue

                        parsed = parse_session_name(raw_name)
                        if not parsed:
                            continue

                        name = parsed.get("name", "").strip()
                        if not name:
                            continue

                        if name in SYSTEM_ACCOUNTS:
                            continue
                        if parsed.get("isOfficial"):
                            continue

                        entry = {
                            "name": name,
                            "is_group": parsed.get("isGroup", False),
                            "unread": parsed.get("unread", 0),
                            "is_at": parsed.get("isAt", False),
                            "is_pinned": parsed.get("isPinned", False),
                        }

                        if entry not in sessions:
                            sessions.append(entry)

                        try:
                            sel_pattern = child.GetSelectionItemPattern()
                            if sel_pattern and sel_pattern.IsSelected:
                                active_session = entry
                        except Exception:
                            pass

                    except Exception:
                        continue

                if not active_session:
                    try:
                        import win32gui
                        hwnd = getattr(target_driver, "hwnd", None)
                        if hwnd:
                            title = win32gui.GetWindowText(hwnd).strip()
                            if title and " - 微信" in title:
                                name = title.replace(" - 微信", "").strip()
                                if name:
                                    from src.uia.session import session_type_cache
                                    cached = session_type_cache.get_type(name)
                                    is_group = (cached == "group") if cached else (
                                        '群' in name and len(name) > 2
                                    )
                                    active_session = {"name": name, "is_group": is_group, "unread": 0, "is_at": False, "is_pinned": False}
                    except Exception:
                        pass

            except Exception as e:
                _log.debug(f"[active-chat-names] UIA 读取异常: {e}")

            return sessions, active_session

        sessions, active_session = await run_in_threadpool(_read_sessions)

        if active_session:
            sessions = [s for s in sessions if s["name"] != active_session["name"]]
            sessions.insert(0, active_session)

        contacts = [s["name"] for s in sessions if not s["is_group"]]
        groups   = [s["name"] for s in sessions if s["is_group"]]

        return ok({
            "contacts": contacts[:20],
            "groups":   groups[:20],
            "active":   active_session,
            "all":      sessions[:25],
        })

    except Exception as e:
        _log.warning(f"[active-chat-names] 接口异常: {e}")
        return ok({"contacts": [], "groups": [], "active": None, "all": [], "error": str(e)})
