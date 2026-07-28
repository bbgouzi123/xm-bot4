import json
import logging
from typing import List, Dict
from . import state

logger = logging.getLogger(__name__)

class CalendarMixin:
    """日历查询相关的 Mixin"""

    def get_calendar_events(self, start_date: str, end_date: str, industry_id: str = "") -> List[Dict]:
        try:
            return self._get_calendar_events_impl(start_date, end_date, industry_id)
        except Exception as e:
            logger.exception("[日历引擎] get_calendar_events 异常，返回空列表: %s", e)
            return []

    def _get_calendar_events_impl(self, start_date: str, end_date: str, industry_id: str = "") -> List[Dict]:
        start_full = f"{start_date} 00:00:00"
        end_full = f"{end_date} 23:59:59"
        events = []
        with state._schedule_lock:
            for s in state._schedules:
                st = state._schedule_time_str(s)
                if not st or st < start_full or st > end_full:
                    continue
                if industry_id and s.get("industry_tag") != industry_id:
                    continue
                media = s.get("media_urls", [])
                if isinstance(media, str):
                    try:
                        media = json.loads(media)
                    except Exception:
                        media = []
                if isinstance(media, dict):
                    media = state._json_plain([media])
                elif not isinstance(media, list):
                    media = []
                else:
                    media = state._json_plain(media)
                ct = s.get("content_text", "")
                if isinstance(ct, (dict, list)):
                    try:
                        ct = json.dumps(ct, ensure_ascii=False)
                    except Exception:
                        ct = str(ct)
                else:
                    ct = str(ct or "")
                events.append({
                    "id": s.get("id"),
                    "scheduled_time": st,
                    "content_text": ct,
                    "media_urls": media,
                    "status": s.get("status", "pending"),
                    "industry_tag": s.get("industry_tag", ""),
                    "source": s.get("source") or (
                        "direct_bg" if any("direct_bg_moments" in str(url) for url in media) else (
                            "manual_compose" if (
                                s.get("compose_settings") or
                                s.get("compose_batch_id") or
                                any("manual_compose_" in str(url) for url in media) or
                                (not ct and media and any("/oss/" in str(url) for url in media))
                            ) else None
                        )
                    ),
                    "compose_batch_id": s.get("compose_batch_id"),
                    "compose_settings": s.get("compose_settings"),
                    "split_index": s.get("split_index"),
                })
        events.sort(key=lambda x: str(x.get("scheduled_time") or ""))

        def _slot_key(ev):
            m = ev.get("media_urls")
            try:
                if isinstance(m, list):
                    mk = json.dumps(m, sort_keys=True, ensure_ascii=False)
                else:
                    mk = str(m)
            except (TypeError, ValueError):
                mk = repr(m)
            txt = ev.get("content_text")
            if isinstance(txt, (dict, list)):
                try:
                    txt = json.dumps(txt, ensure_ascii=False)
                except Exception:
                    txt = repr(txt)
            else:
                txt = str(txt or "")
            return (str(ev.get("scheduled_time") or ""), txt, mk)

        def _status_rank(st) -> int:
            if st == "published":
                return 3
            if st == "failed":
                return 2
            return 1

        seen_nids = set()
        id_pass = []
        for ev in events:
            raw_id = ev.get("id")
            nid = state._coerce_schedule_id(raw_id)
            if raw_id is not None and nid > 0:
                if nid in seen_nids:
                    continue
                seen_nids.add(nid)
            id_pass.append(ev)

        groups = {}
        for ev in id_pass:
            groups.setdefault(_slot_key(ev), []).append(ev)

        deduped = []
        for evs in groups.values():
            if not evs:
                continue
            best = max(
                evs,
                key=lambda e: (_status_rank(e.get("status")), state._coerce_schedule_id(e.get("id"))),
            )
            deduped.append(best)
        deduped.sort(key=lambda x: str(x.get("scheduled_time") or ""))
        return state._json_plain(deduped)
