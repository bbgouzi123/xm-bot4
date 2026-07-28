import re
import json
import uuid
import random
import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import quote

from . import state
from .state import sanitize_newlines_in_json
from .json_repair import robust_json_parse
from .screenshot import try_composite_screenshot
from .bootstrap import _persist_schedules_after_mutation

logger = logging.getLogger(__name__)

class GeneratorMixin:
    """排期自动生成与解析的 Mixin"""


    async def generate_monthly_plan(self, industry_tag: str, days: int = 7, image_model_id: str = "", custom_prompt: str = "", start_date: str = "", industry_profile=None, daily_count: int = 1):
        logger.info(f"[{self.account_id}] 启动发圈工厂，正在为行业 '{industry_tag}' 生成 {days} 天计划 (每日 {daily_count} 条)...")

        if not self.ai_service:
            logger.warning("[日历引擎] AI 服务未挂载，无法执行自动生成。")
            raise RuntimeError("AI 服务未配置，请先在系统设置中配置大模型")

        if start_date:
            base_dt = datetime.strptime(start_date, "%Y-%m-%d")
        else:
            base_dt = datetime.now()

        _range_start = base_dt.strftime("%Y-%m-%d 00:00:00")
        _range_end = (base_dt + timedelta(days=max(days - 1, 0))).strftime("%Y-%m-%d 23:59:59")
        db_industry_id = industry_profile.id if industry_profile else industry_tag

        with state._schedule_lock:
            before = len(state._schedules)
            state._schedules[:] = [
                s for s in state._schedules
                if not (s.get("status") == "pending"
                        and s.get("industry_tag") == db_industry_id
                        and _range_start <= state._schedule_time_str(s) <= _range_end)
            ]
            deleted = before - len(state._schedules)
            if deleted:
                logger.info(f"[日历引擎] 已清理 {_range_start[:10]}~{_range_end[:10]} 范围内 {deleted} 条旧 pending 排期")

        batch_size = 1  
        remaining_days = days
        current_offset = 0
        generated_ids = []

        from src.crm.prompt_builder import PromptBuilder

        while remaining_days > 0:
            current_batch_days = min(batch_size, remaining_days)
            batch_start_dt = base_dt + timedelta(days=current_offset)
            batch_start_date_str = batch_start_dt.strftime("%Y-%m-%d")

            logger.info(f"[日历引擎] 正在生成批次：起始日期={batch_start_date_str}, 天数={current_batch_days} (剩余={remaining_days - current_batch_days})")
            batch_seed = f"batch_seed_{uuid.uuid4().hex[:8]}"

            target_industry_name = ""
            is_xm_bot4 = (
                industry_tag == "xm-bot4系统" or
                (industry_profile and (industry_profile.name == "xm-bot4系统" or getattr(industry_profile, "id", "") == "sys_001"))
            )
            if is_xm_bot4:
                from .helpers import resolve_target_industry
                target_industry_name = resolve_target_industry(self.account_id, batch_seed, current_offset)

            if industry_profile:
                prompt = PromptBuilder.build_moment_prompt(
                    industry_profile, 
                    days=current_batch_days, 
                    seed=batch_seed,
                    target_industry_name=target_industry_name,
                    daily_count=daily_count,
                    wxid=self.account_id
                )
            else:
                prompt = f"请为【{industry_tag}】行业生成未来 {current_batch_days} 天的全托管朋友圈排期计划，且每天生成 {daily_count} 条不同的朋友圈。随机特征码：{batch_seed}"

            prompt += "\n\n请自动规划并画图，生成全托管朋友圈排期计划."
            prompt += f"\n排期起始日期：{batch_start_date_str}"

            if custom_prompt:
                prompt += f"\n\n【重要附加指令/知识库约束】：\n{custom_prompt}"

            logger.info(f"[日历引擎] [批次] 最终 Prompt 长度: {len(prompt)} 字符")

            try:
                logger.info("  >> 正在调度 Coze 内容工厂智能体，进行思考与画图...")
                response = await self.ai_service.start_chat(
                    agent_id=image_model_id,
                    message=prompt,
                    session_id=f"planner_generator_{uuid.uuid4().hex[:8]}",
                    cache_session=False
                )

                if not response.get("success"):
                    err_msg = response.get('error') or "未知错误"
                    logger.error(f"[日历引擎] AI 生成失败: {err_msg}")
                    raise RuntimeError(f"AI 生成失败: {err_msg}")

                script_result_str = response.get("content", "").strip()

                script_result_str = script_result_str.replace('，', ',').replace('：', ':').replace('“', '"').replace('”', '"')
                script_result_str = re.sub(r'```json\s*', '', script_result_str)
                script_result_str = re.sub(r'```\s*', '', script_result_str)

                print(f"[日历引擎] AI原始返回（清洗后前500字）: {script_result_str[:500]}")
                print(f"[日历引擎] AI返回总长度: {len(script_result_str)} 字符")

                arr_start = script_result_str.find('[')
                if arr_start >= 0:
                    script_result_str = script_result_str[arr_start:]

                # 关键修复：清理 JSON 字符串值内部的裸换行符（Coze 有时会在 URL / image_prompt 中直接换行）
                script_result_str = sanitize_newlines_in_json(script_result_str)

                plan_data = robust_json_parse(script_result_str)
                # ─── 优雅降级与错误恢复机制 ───
                if not plan_data or not isinstance(plan_data, list) or len(plan_data) == 0:
                    logger.warning("[日历引擎] AI JSON 解析失败或为空，启动备用纯文本句式分析恢复排期...")
                    plan_data = []
                    
                    # 寻找包含可能文案的行
                    lines = [line.strip() for line in script_result_str.split('\n') if line.strip()]
                    valid_texts = []
                    for line in lines:
                        # 排除 Markdown 标题、JSON 语法噪音及过短行
                        if line.startswith(('#', '-', '*', '[', ']', '{', '}', '"', 'day_offset', 'text', 'image_prompt')):
                            continue
                        if len(line) > 10:
                            # 清理行首的数字点标号，如 "1. " 或 "第一天："
                            cleaned_line = re.sub(r'^\d+[\.、\s]+|第[一二三四五六七八九十]+天[:：\s]*', '', line).strip()
                            if len(cleaned_line) > 10:
                                valid_texts.append(cleaned_line)
                    
                    # 如果找到了文案，按顺序分配到天数中
                    if valid_texts:
                        logger.info(f"[日历引擎] 句式分析成功提取 {len(valid_texts)} 条备用排期文案")
                        for idx, text in enumerate(valid_texts):
                            day_num = (idx // daily_count) + 1
                            plan_data.append({
                                "day_offset": day_num,
                                "text": text,
                                "image_prompt": f"适合以下文案意境的商务/生活插图：{text[:30]}",
                                "media_type": "image",
                                "media_urls": []
                            })
                    else:
                        # 兜底生成默认排期，避免整个日历服务崩溃
                        logger.warning("[日历引擎] 句式分析未能提取到任何文案，使用模板生成默认兜底排期")
                        for d in range(1, current_batch_days + 1):
                            for c in range(daily_count):
                                if is_xm_bot4:
                                    # 针对 xm-bot4 系统的黄金兜底方案，突出高含金量 B2B 算账与私域增长痛点
                                    fallback_options = [
                                        (
                                            "私域流量难获客？招销售成本高？xm-bot4 AI数字员工系统，7x24小时智能聊天成交，自动加人跟单，一部手机搞定全流程营销！ 🚀",
                                            "A clean neat Chinese office desk with a laptop displaying a modern software dashboard with green metrics, warm ambient light, high resolution"
                                        ),
                                        (
                                            "夜间来的线索没人回？xm-bot4 AI数字员工帮你深夜秒级响应、解答报价，不错过任何高意向客户，让私域运营24小时不打烊！ 🌙",
                                            "A warm cozy Chinese office room at night, street lights glowing outside window, a glowing computer screen displaying automation logs, realistic photo style"
                                        ),
                                        (
                                            "担心微信风控被限制？xm-bot4独创真实无障碍模拟点击打字，非协议挂底层合规技术，像真人销售一样稳定操作，安全有保障。 🔒",
                                            "A close-up shot of a modern keyboard with a phone next to it, showing a secure and professional workspace in China, soft focused background, high quality"
                                        ),
                                        (
                                            "老板每天忙于琐碎的加人发圈，哪有时间谈大单？xm-bot4系统智能托管30天发圈与自动加人，解放双手，把时间留给核心业务！ ☕",
                                            "A professional Chinese business owner holding a cup of coffee, smiling slightly, looking out from a glass window of a modern office in Shanghai, realistic"
                                        )
                                    ]
                                    local_random = random.Random(f"{batch_seed}_{d}_{c}")
                                    text, img_p = local_random.choice(fallback_options)
                                    
                                    # 如果有具体的 target_industry_name，则进一步将兜底文案本地化融入该行业
                                    if target_industry_name:
                                        text = text.replace("全流程营销", f"【{target_industry_name}】全流程营销").replace("私域运营", f"【{target_industry_name}】私域运营").replace("核心业务", f"【{target_industry_name}】业务增长")
                                        img_p = img_p + f", related to {target_industry_name} business context"
                                else:
                                    # 其它行业的精细化兜底
                                    text = f"新的一天，祝大家工作顺利！继续关注我们在【{industry_tag}】领域的最新动态与分享。"
                                    country = getattr(industry_profile, "moment_image_country", "CN") if industry_profile else "CN"
                                    scene_type = getattr(industry_profile, "moment_image_scene_type", "real_scene") if industry_profile else "real_scene"
                                    country_suffix = " Chinese style, Asian people, realistic" if country == "CN" else " international style"
                                    scene_suffix = " realistic photo, iPhone shot" if scene_type == "real_scene" else " comic illustration style"
                                    img_p = f"a beautiful modern business workspace related to {industry_tag}{country_suffix}{scene_suffix}, high resolution"
                                    
                                plan_data.append({
                                    "day_offset": d,
                                    "text": text,
                                    "image_prompt": img_p,
                                    "media_type": "image",
                                    "media_urls": []
                                })
                else:
                    # 如果解析成功，确保其中的数据格式是正确的
                    sanitized_plan = []
                    for item in plan_data:
                        if isinstance(item, dict):
                            # 确保基本字段存在
                            item.setdefault("day_offset", 1)
                            item.setdefault("text", "今日分享")
                            item.setdefault("image_prompt", "")
                            item.setdefault("media_type", "image")
                            item.setdefault("media_urls", [])
                            sanitized_plan.append(item)
                    plan_data = sanitized_plan

                print(f"[日历引擎] ✅ 成功就绪了 {len(plan_data)} 条排期数据")

                async def _generate_media_task(item_idx: int, prompt_text: str, media_type: str):
                    if not prompt_text:
                        return item_idx, []
                    try:
                        if media_type == "video" and hasattr(self.ai_service, "generate_video"):
                            logger.info(f"[日历生视频] 正在为第 {item_idx + 1} 天生成 AI 短视频...")
                            video_url = await self.ai_service.generate_video(prompt_text)
                            if video_url:
                                logger.info(f"[日历生视频] 第 {item_idx + 1} 天视频生成成功: {video_url}")
                                return item_idx, [video_url]
                        else:
                            logger.info(f"[日历生图] 正在为第 {item_idx + 1} 天生成配图...")
                            img_url = await self.ai_service.generate_image(prompt_text)
                            if img_url:
                                logger.info(f"[日历生图] 第 {item_idx + 1} 天配图生成成功: {img_url}")
                                return item_idx, [img_url]
                    except Exception as ex:
                        logger.error(f"[日历生成] 第 {item_idx + 1} 天媒体 ({media_type}) 生成异常: {ex}")
                    return item_idx, []

                media_tasks = []
                for idx, item in enumerate(plan_data):
                    media_paths = item.get("media_urls", [])
                    image_prompt = item.get("image_prompt", "")
                    media_type = item.get("media_type", "image")

                    if media_type == "image":
                        composite_url = try_composite_screenshot(self.account_id, current_offset + idx, industry_tag, industry_profile)
                        if composite_url:
                            item["media_urls"] = [composite_url]
                            logger.info(f"[日历生成] 第 {idx + 1} 天已成功应用本地真实截图合成配图: {composite_url}")
                            continue

                    needs_draw = False
                    if not media_paths:
                        needs_draw = True
                    else:
                        is_fake = all(
                            any(fake in str(url) for fake in ["example.com", "example-coze.com", "placeholder"])
                            for url in media_paths
                        )
                        if is_fake:
                            needs_draw = True

                    if needs_draw and image_prompt:
                        media_tasks.append(_generate_media_task(idx, image_prompt, media_type))

                if media_tasks:
                    logger.info(f"[日历引擎] 检测到非 Coze 平台且配图/视频缺失，启动并行渲染任务，共计 {len(media_tasks)} 个...")
                    draw_results = await asyncio.gather(*media_tasks)
                    for item_idx, paths in draw_results:
                        if paths:
                            plan_data[item_idx]["media_urls"] = paths

                date_counters = {}
                for item in plan_data:
                    raw_offset = int(item.get("day_offset", 1))
                    offset_days = max(0, raw_offset - 1)
                    text = item.get("text", "")
                    media_paths = item.get("media_urls", [])

                    target_date = batch_start_dt + timedelta(days=offset_days)
                    date_str = target_date.strftime("%Y-%m-%d")
                    count_for_day = date_counters.get(date_str, 0)
                    date_counters[date_str] = count_for_day + 1

                    golden_slots = [
                        (7, 30, 9, 0),
                        (11, 30, 13, 0),
                        (17, 0, 18, 30),
                        (20, 0, 22, 0),
                        (9, 30, 11, 0),
                        (14, 0, 16, 30),
                        (19, 0, 19, 50),
                        (22, 10, 23, 30),
                    ]
                    slot = golden_slots[count_for_day % len(golden_slots)]
                    slot_start_mins = slot[0] * 60 + slot[1]
                    slot_end_mins = slot[2] * 60 + slot[3]
                    rand_mins = random.randint(slot_start_mins, slot_end_mins)

                    scheduled_time = target_date.replace(
                        hour=rand_mins // 60,
                        minute=rand_mins % 60,
                        second=0
                    ).strftime("%Y-%m-%d %H:%M:%S")

                    with state._schedule_lock:
                        new_id = state._next_id
                        state._next_id += 1
                        state._schedules.append({
                            "id": new_id,
                            "scheduled_time": scheduled_time,
                            "content_text": text,
                            "media_urls": json.dumps(media_paths),
                            "media_type": item.get("media_type", "image"),
                            "status": "pending",
                            "industry_tag": db_industry_id,
                            "created_at": datetime.now().isoformat(),
                        })
                        generated_ids.append(new_id)

                    logger.info(f"  √ [已生成]：{scheduled_time} | 文案概要: {text[:15]}...")

                current_offset += current_batch_days
                remaining_days -= current_batch_days
                self._sync_schedules_to_cloud()

            except Exception as e:
                logger.error(f"[日历引擎] 生成当前批次时发生错误: {e}")
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"生成批次时发生错误: {e}")

        self._sync_schedules_to_cloud()
        return generated_ids
