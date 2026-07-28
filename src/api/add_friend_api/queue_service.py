from typing import Optional
from fastapi import APIRouter

from src.friend import friend_queue
from src.utils.response import ok, ok_msg
from .models import QueueQueryRequest, BatchDeleteRequest, BatchTagRequest, RecycleRequest

router = APIRouter()

@router.post("/queue")
async def get_queue(req: QueueQueryRequest):
    """获取加好友队列（分页+多维筛选）"""
    return friend_queue.get_queue_list(
        status=req.status,
        page=req.page,
        page_size=req.page_size,
        keyword=req.keyword,
        tag=req.tag,
        industry_profile_id=req.industry_profile_id,
        import_batch_id=req.import_batch_id,
    )

@router.get("/queue/stats")
async def get_queue_stats():
    """获取队列统计"""
    stats = friend_queue.get_queue_stats()
    today_count = friend_queue.get_today_count()
    return ok({**stats, "today_added": today_count})

@router.delete("/queue/{queue_id}")
async def delete_queue_item(queue_id: int):
    """删除单条记录"""
    friend_queue.delete_item(queue_id)
    return ok_msg("操作成功")

@router.post("/queue/batch-delete")
async def batch_delete_items(req: BatchDeleteRequest):
    """批量删除"""
    friend_queue.batch_delete(req.ids)
    return ok({"deleted": len(req.ids)})

@router.post("/queue/clear")
async def clear_queue(status: Optional[str] = None):
    """清空队列"""
    friend_queue.clear_queue(status)
    return ok_msg("操作成功")

@router.get("/tags")
async def get_all_tags():
    """获取所有已使用的标签"""
    tags = friend_queue.get_all_tags()
    return ok({"tags": tags})

@router.post("/tags/batch-set")
async def batch_set_tags(req: BatchTagRequest):
    """批量给选中号码添加标签"""
    friend_queue.batch_set_tags(req.ids, req.tags)
    return ok({"affected": len(req.ids)})

@router.post("/tags/batch-remove")
async def batch_remove_tags(req: BatchTagRequest):
    """批量移除选中号码的标签"""
    friend_queue.batch_remove_tags(req.ids, req.tags)
    return ok({"affected": len(req.ids)})

@router.get("/queue/stats-by-industry")
async def get_stats_by_industry():
    """按行业统计导入数量和状态"""
    data = friend_queue.get_stats_by_industry()
    return ok({"data": data})

@router.get("/queue/stats-by-tag")
async def get_stats_by_tag():
    """按标签统计"""
    data = friend_queue.get_stats_by_tag()
    return ok({"data": data})

@router.get("/queue/batches")
async def get_import_batches():
    """获取所有导入批次"""
    batches = friend_queue.get_import_batches()
    return ok({"batches": batches})

@router.post("/queue/recycle")
async def recycle_queue(req: RecycleRequest):
    """号码回收复用"""
    result = friend_queue.recycle_to_industry(
        new_industry_id=req.new_industry_id,
        new_industry_name=req.new_industry_name,
        recycle_mode=req.recycle_mode,
        source_industry_id=req.source_industry_id,
        source_batch_id=req.source_batch_id,
        source_tag=req.source_tag,
        add_tags=req.add_tags if req.add_tags else None,
    )
    return result


@router.post("/queue/reset-batch")
async def reset_batch_status(batch_id: str):
    """按导入批次 ID 重置所有记录状态"""
    count = friend_queue.batch_reset_by_import_id(batch_id)
    return ok({"reset_count": count})


@router.post("/queue/delete-batch")
async def delete_batch_by_id(batch_id: str):
    """按导入批次 ID 物理清空本地队列所有记录（防止删除已同步批次后重新获取列表时死灰复燃）"""
    count = friend_queue.delete_batch_by_import_id(batch_id)
    return ok({"deleted_count": count})


@router.post("/queue/sync-from-cloud")
async def sync_queue_from_cloud():
    """从同步后端重拉队列数据"""
    try:
        friend_queue.reload_from_cloud_for_active_bot()
        return ok_msg("操作成功")
    except Exception as e:
        return err(50000, f"同步失败: {str(e)}")

