from typing import Optional, Union, List, Dict, Any
from pydantic import BaseModel

class ImportRequest(BaseModel):
    """确认导入请求"""
    file_id: str  # upload 返回的临时文件 ID
    field_mapping: Optional[dict] = None  # 用户可能修改的字段映射
    original_filename: str = ""  # 用户上传的原始文件名
    tags: list[str] = []  # 用户自定义标签（支持多个）
    auto_tag_filename: bool = True  # 是否自动用文件名做第一个标签


class RemapRequest(BaseModel):
    """手动列映射重新解析请求"""
    file_id: str
    mapping: dict  # 列索引 -> 字段名的映射关系


class StartTaskRequest(BaseModel):
    """启动加好友任务"""
    # 性能参数
    max_friends_per_day: int = 15
    interval_minutes: Union[float, List[float]] = 3.0
    batch_size: int = 3
    active_warmup: bool = False  # 是否开启一键托管智能养号模式
    # 验证消息
    verify_mode: str = "ai"  # "ai" 或 "fixed"
    verify_message: str = ""  # fixed 模式下的固定验证消息
    remark_mode: str = "default"  # "default" 或 "custom"
    remark_template: str = ""  # 自定义拼接模板，如 "{姓名}-{公司}"
    auto_greeting: bool = True  # 添加成功后是否自动发送破冰消息
    tags: str = ""  # 微信内部标签（UIA 自动打）
    # 任务范围筛选（留空=全部）
    industry_profile_id: str = ""  # 只执行指定行业
    tag_filter: str = ""  # 只执行指定标签
    import_batch_id: str = ""  # 只执行指定批次
    # 重启策略
    retry_failed: bool = False  # 重试之前失败的
    retry_unknown: bool = False  # 重试未知状态的
    skip_processing: bool = True  # 跳过已发出请求的（避免重复打扰）


class QueueQueryRequest(BaseModel):
    """队列查询（支持多维筛选）"""
    status: Optional[str] = None
    page: int = 1
    page_size: int = 50
    keyword: str = ""
    tag: Optional[str] = None  # 按标签筛选
    industry_profile_id: Optional[str] = None  # 按行业筛选
    import_batch_id: Optional[str] = None  # 按导入批次筛选


class BatchDeleteRequest(BaseModel):
    """批量删除"""
    ids: list[int] = []


class BatchTagRequest(BaseModel):
    """批量标签操作"""
    ids: list[int] = []
    tags: list[str] = []


class RecycleRequest(BaseModel):
    """号码回收复用请求"""
    new_industry_id: str  # 新行业 ID
    new_industry_name: str  # 新行业名称
    recycle_mode: str = "same_account"  # "same_account" 或 "new_account"
    source_industry_id: str = ""  # 从哪个行业回收（留空=全部）
    source_batch_id: str = ""  # 从哪个批次回收
    source_tag: str = ""  # 从哪个标签回收
    add_tags: list[str] = []  # 追加的新标签

class EnterpriseSearchRequest(BaseModel):
    q: str = ""
    region: str = ""
    industry: str = ""
    reg_status: str = ""
    page: int = 1
    page_size: int = 20
    hide_purchased: bool = False


class EnterprisePrepareRequest(BaseModel):
    ids: List[str]
    user_id: str = ""


class EnterpriseImportPaidRequest(BaseModel):
    session_id: str
    user_id: str
    consumable_order_id: int
    tags: List[str] = []
    original_filename: str = "码上查企导入"


class StartGroupTaskRequest(BaseModel):
    """启动批量加群好友任务"""
    group_name: str
    max_add_count: int = 15
    remark_prefix: Optional[str] = None
    tags: Optional[str] = None
    verify_message: Optional[str] = None
    interval_range: List[float] = [10.0, 20.0]


class ManualImportRequest(BaseModel):
    """手动录入导入请求"""
    contacts: List[Dict[str, Any]]
    tags: List[str] = []

