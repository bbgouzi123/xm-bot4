import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def log_token_usage(model: str, prompt_tokens: int, completion_tokens: int, total_tokens: int):
    """持久化记录大模型 API 的 Token 消费情况到本地 JSONL 数据库中"""
    try:
        os.makedirs("data/token_logs", exist_ok=True)
        log_file = "data/token_logs/token_usage.jsonl"
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        logger.debug(f"[TokenLogger] 成功记录 Token 消耗: {model} -> {total_tokens} tokens")
    except Exception as e:
        logger.error(f"[TokenLogger] 写入 Token 消耗日志失败: {e}")
