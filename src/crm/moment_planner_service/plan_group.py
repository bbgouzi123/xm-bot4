import os
import json
import uuid
import random
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from . import state
from .bootstrap import _persist_schedules_after_mutation

logger = logging.getLogger(__name__)

class PlanGroupMixin:
    """朋友圈计划组管理 Mixin，被 MomentPlannerService 继承"""

    def _load_plan_groups(self) -> List[dict]:
        file_path = state.get_local_plan_groups_file()
        if not file_path.exists():
            return []
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
            return raw.get("plan_groups", [])
        except Exception as e:
            logger.warning(f"[日历引擎] 加载计划组快照失败: {e}")
            return []

    def _save_plan_groups(self, groups: List[dict]):
        file_path = state.get_local_plan_groups_file()
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "plan_groups": groups,
                "saved_at": datetime.now().isoformat()
            }
            file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[日历引擎] 已保存计划组快照 ({len(groups)} 个计划)")
        except Exception as e:
            logger.error(f"[日历引擎] 保存计划组快照失败: {e}")

    def get_plan_groups(self) -> List[dict]:
        """获取所有计划组"""
        return self._load_plan_groups()

    def save_plan_group(self, data: dict) -> dict:
        """创建或更新计划组，并同步生成对应的朋友圈排期"""
        pg_id = data.get("id")
        is_new = not bool(pg_id)
        if is_new:
            pg_id = f"pg_{uuid.uuid4().hex}"
            data["id"] = pg_id
            data["created_at"] = datetime.now().isoformat()
            data["status"] = data.get("status") or "active"

        # 1. 加载并更新计划组配置
        groups = self._load_plan_groups()
        if is_new:
            groups.append(data)
        else:
            for i, g in enumerate(groups):
                if g.get("id") == pg_id:
                    # 保留原创建时间
                    data["created_at"] = g.get("created_at")
                    groups[i] = data
                    break

        # 2. 清理该计划组之前生成的所有待发送(pending)朋友圈排期，防止修改计划组时重复或脏数据残留
        with state._schedule_lock:
            state._schedules[:] = [
                s for s in state._schedules
                if not (s.get("compose_batch_id") == pg_id and s.get("status") == "pending")
            ]

        # 3. 只有当状态为 active 时，才重新生成排期时间槽
        if data.get("status") == "active":
            # 运行发圈时间槽生成逻辑
            start_date = data.get("start_date")
            end_date = data.get("end_date")
            exec_mode = data.get("exec_mode", "fixed")
            cycle = data.get("cycle", "daily")
            week_days = data.get("week_days", [])
            fixed_times = data.get("fixed_times", [])
            range_start = data.get("range_start", "09:00")
            range_end = data.get("range_end", "18:00")
            post_count = int(data.get("post_count", 1))
            materials = data.get("materials", [])
            industry_tag = data.get("industry_tag") or data.get("industry_id") or "计划组发布"

            if start_date and end_date and materials:
                try:
                    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                    
                    # 限制最大排期天数，防止过载
                    if (end_dt - start_dt).days > 90:
                        end_dt = start_dt + timedelta(days=90)
                        logger.warning("[日历引擎] 计划组日期区间超过90天，已自动缩减至90天")

                    slots = []
                    current_dt = start_dt
                    while current_dt <= end_dt:
                        current_date_str = current_dt.strftime("%Y-%m-%d")
                        # 0代表周日，1-6代表周一到周六
                        weekday = (current_dt.weekday() + 1) % 7
                        
                        if cycle == "weekly" and weekday not in week_days:
                            current_dt += timedelta(days=1)
                            continue

                        # 生成当天的槽位
                        if exec_mode == "fixed":
                            for t in fixed_times:
                                slots.append(f"{current_date_str} {t}:00")
                        else:  # random mode
                            try:
                                sh, sm = map(int, range_start.split(":"))
                                eh, em = map(int, range_end.split(":"))
                                start_mins = sh * 60 + sm
                                end_mins = eh * 60 + em
                                if end_mins <= start_mins:
                                    end_mins = start_mins + 60
                                
                                total_mins = end_mins - start_mins
                                effective_post_count = max(1, post_count)
                                interval_size = total_mins / effective_post_count
                                
                                for i in range(effective_post_count):
                                    sub_start = start_mins + int(i * interval_size)
                                    sub_end = start_mins + int((i + 1) * interval_size)
                                    if sub_end - sub_start > 1:
                                        chosen_min = random.randint(sub_start, sub_end - 1)
                                    else:
                                        chosen_min = sub_start
                                    
                                    h = chosen_min // 60
                                    m = chosen_min % 60
                                    slots.append(f"{current_date_str} {h:02d}:{m:02d}:00")
                            except Exception as e:
                                logger.error(f"[日历引擎] 计划组随机时间生成失败: {e}")
                                # 保底生成一个固定时间
                                slots.append(f"{current_date_str} 12:00:00")

                        current_dt += timedelta(days=1)

                    # 按时间排序
                    slots.sort()

                    # 轮询匹配素材，并写入内存排期
                    with state._schedule_lock:
                        for idx, slot_time in enumerate(slots):
                            material = materials[idx % len(materials)]
                            new_id = state._next_id
                            state._next_id += 1
                            state._schedules.append({
                                "id": new_id,
                                "scheduled_time": slot_time,
                                "content_text": material.get("content_text", ""),
                                "media_urls": material.get("media_urls", []),
                                "status": "pending",
                                "industry_tag": industry_tag,
                                "created_at": datetime.now().isoformat(),
                                "compose_batch_id": pg_id,
                                "source": "plan_group"
                            })
                except Exception as e:
                    logger.error(f"[日历引擎] 计划组排期生成异常: {e}")

        # 4. 保存计划组列表并同步到云/本地排期
        self._save_plan_groups(groups)
        _persist_schedules_after_mutation()
        return data

    def delete_plan_group(self, plan_group_id: str) -> bool:
        """删除计划组，并同步清理该计划组关联的所有未发布(pending)排期"""
        groups = self._load_plan_groups()
        before_len = len(groups)
        groups = [g for g in groups if g.get("id") != plan_group_id]
        if len(groups) == before_len:
            return False

        # 1. 移除配置
        self._save_plan_groups(groups)

        # 2. 清理关联的 pending 排期
        with state._schedule_lock:
            state._schedules[:] = [
                s for s in state._schedules
                if not (s.get("compose_batch_id") == plan_group_id and s.get("status") == "pending")
            ]

        # 3. 触发持久化和云同步
        _persist_schedules_after_mutation()
        return True
