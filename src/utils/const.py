"""
常量定义（移植自 xm-bot4 lib/CONST.py — 13行完整反编译）

原始文件: lib/CONST.py (COMPLETE, 13 lines)
"""


class ActionType:
    """操作类型"""
    TEXT = 'text'
    FILE = 'file'


# 换行符常量
NEWLINE = '\n'


class AIPlatform:
    """AI 平台类型"""
    ALI = 'qianwen'       # 阿里千问
    COZE = 'coze'         # 扣子
    DIFY = 'dify'         # Dify
    DEEPSEEK = 'deepseek' # DeepSeek
    

# 产品身份标识（用于订阅和定价同步）
PRODUCT_KEY = 'xm-bot4'
