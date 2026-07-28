# contacts 子包说明

将原 `contacts.py` 拆分为职责清晰的小文件，保留外部导入不变。

## 对外接口

- `from src.uia.contacts import ContactSync`
- `from src.uia.contacts import request_contact_sync_pause`

## 文件职责

- `contact_sync.py`：`ContactSync` 组装入口
- `session.py`：通讯录页面导航、列表会话与上下文回正
- `profile_parser.py`：右侧资料区域解析与稳定轮询
- `sync_contacts.py`：联系人全量同步
- `sync_details.py`：头像与详情同步上传
- `storage.py`：本地 JSON、SQLite、缓存与同步后端清理
- `query_tags.py`：单联系人查询、标签同步
- `constants.py`：共享常量、暂停标志、匹配辅助
