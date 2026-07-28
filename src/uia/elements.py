"""
微信 4.1.x UIA 控件映射
整合自 xm-bot4 反编译源码 (Uielements.py)
"""


class WxClass:
    """微信 4.1.x 控件 ClassName 常量（实测）"""

    # 主窗口
    MAIN_WINDOW = "mmui::MainWindow"
    WIN32_CLASS = "Qt51514QWindowIcon"

    # 导航
    TAB_BAR = "mmui::MainTabBar"
    TAB_ITEM = "mmui::XTabBarItem"

    # 布局
    STACKED_WIDGET = "mmui::XStackedWidget"
    SPLITTER = "mmui::XSplitterView"
    VIEW = "mmui::XView"

    # 聊天
    CHAT_DETAIL = "mmui::ChatDetailView"
    CHAT_MASTER = "mmui::ChatMasterView"
    CHAT_INPUT = "mmui::ChatInputField"
    SEARCH_BOX = "mmui::XValidatorTextEdit"
    CHAT_MEMBER_CELL = "mmui::ChatMemberCell"  # 来自 xm-bot4

    # 消息类型
    MSG_ITEM = "mmui::ChatItemView"
    MSG_TEXT = "mmui::ChatTextItemView"
    MSG_BUBBLE = "mmui::ChatBubbleItemView"
    MSG_VOICE = "mmui::ChatVoiceItemView"
    MSG_REFER = "mmui::ChatBubbleReferItemView"
    MSG_CARD = "mmui::ChatPersonalCardItemView"

    # 朋友圈
    SNS_WINDOW = "mmui::SNSWindow"
    TIMELINE_LIST = "mmui::TimeLineListView"
    TIMELINE_CONTENT = "mmui::TimelineContentCell"
    TIMELINE_GRID = "mmui::TimelineGridImageCell"
    TIMELINE_COMMENT = "mmui::TimelineCommentCell"
    TIMELINE_CELL = "mmui::TimelineCell"
    TIMELINE_AD = "mmui::TimelineAdCell"
    TIMELINE_NOTIFY = "TimelineNotifyCell"
    COMMENT_EDIT = "mmui::XValidatorTextEdit"
    SNS_LIKE_TOAST = "mmui::TimelineFloatMenu"  # 新版微信（实测）WindowControl
    SNS_LIKE_TOAST_LEGACY = "SnsLikeToastWnd"    # 旧版微信（竞品环境）PaneControl
    PUBLISH_PANEL = "mmui::SnsPublishPanel"
    OUTLINE_BUTTON = "mmui::XOutlineButton"

    # 发现页
    DISCOVER_CELL = "mmui::ExtensionDiscoverContentCell"

    # 联系人管理
    CONTACTS_DETAIL = "mmui::ContactsManagerDetailView"
    CONTACTS_CONTROL = "mmui::ContactsManagerControlView"
    CONTACT_HEAD = "mmui::ContactHeadView"
    SESSION_CELL = "mmui::ChatSessionCell"
    STICKY_LIST = "mmui::StickyHeaderRecyclerListView"  # 来自 xm-bot4

    # 搜索
    SEARCH_POPOVER = "mmui::SearchContentPopover"
    SESSION_PICKER = "mmui::SessionPickerWindow"

    # 独立窗口
    PREFERENCE_WIN = "mmui::PreferenceWindow"
    FILE_MANAGER = "mmui::FileManagerWindow"

    # 联系人
    CONTACTS_MANAGER = "mmui::ContactsManagerWindow"
    PROFILE_POP = "mmui::ProfileUniquePop"
    CONTACT_GROUP = "mmui::ContactsCellGroupView"
    CONTACT_CELL = "mmui::ContactsCellFriendView"  # 猜测，用于辅助校验

    # 来自 xm-bot4 反编译
    PREVIEW_IMAGE = "mmui::PreviewImage"  # 图片预览
    SEARCH_MSG_WINDOW = "mmui::SearchMsgUniqueChatWindow"  # 搜索聊天记录
    LOGIN_WINDOW = "mmui::LoginWindow"  # 登录窗口


class WxName:
    """微信控件 Name 属性常量"""
    # 微信 4.x 导航按钮名称
    CHAT_NAV = "微信"
    CONTACTS_NAV = "通讯录"
    FAVORITES_NAV = "收藏"
    MOMENTS_NAV = "朋友圈"
    DISCOVERY_NAV = "发现"
    CHANNELS_NAV = "视频号"
    MINI_PROGRAM_NAV = "小程序"
    SEARCH = "搜一搜"
    FILE_TRANSFER = "文件传输助手"
    NAV_BAR = "导航"
    SETTINGS = "设置"
    MORE = "更多"

    # 朋友圈 UI 名称
    SNS_WINDOW_TITLE = "朋友圈"
    SEND_BUTTON = "发送(S)"
    LIKE_BUTTON = "赞"
    COMMENT_BUTTON = "评论"
    SESSION_LIST = "会话"
    MESSAGE_LIST = "消息"

    # 来自 xm-bot4 反编译
    CHAT_INFO = "聊天信息"  # 聊天信息按钮
    OFFICIAL_PAGE = "公众号主页"  # 判断公众号
    ADD_FRIEND = "添加朋友"
    ADD_TO_CONTACTS = "添加到通讯录"
    SEND_MSG = "发消息"
    QUICK_ACTION = "快捷操作"
    NEW_FRIEND = "新的朋友"
    VERIFY = "前往验证"
    CONFIRM = "确定"
    CANCEL = "取消"
    ENTER_WECHAT = "进入微信"  # 登录
    MODIFY_TAG = "修改标签"
    MODIFY_REMARK = "修改备注"


# 分辨率参数
RESOLUTION_PARAMS = {
    "1080p": {
        "screen_height": 1080,
        "sys_text_height": 32,
        "time_text_height": 28,
        "recall_text_height": 25,
        "chat_text_height": 38,
    },
    "2k": {
        "screen_height": 1440,
        "sys_text_height": 42,
        "time_text_height": 37,
        "recall_text_height": 33,
        "chat_text_height": 50,
    },
    "4k": {
        "screen_height": 2160,
        "sys_text_height": 64,
        "time_text_height": 56,
        "recall_text_height": 50,
        "chat_text_height": 76,
    },
}


# ====================================================================
# 微信 PC 客户端主界面 UIA 选择器常量（侧边栏 / 主窗 / 按钮 / 列表）
# 用于高级功能：好友管理、联系人同步、朋友圈操作等
# ====================================================================

class WeChatSideBar:
    """主界面侧边栏"""
    Chats = {'title': '微信', 'control_type': 'Button'}
    Contacts = {'title': '通讯录', 'control_type': 'Button'}
    Collections = {'title': '收藏', 'control_type': 'Button', 'class_name': 'mmui::XTabBarItem'}
    Moments = {'title': '朋友圈', 'control_type': 'Button', 'class_name': 'mmui::XTabBarItem'}
    Search = {'title': '搜一搜', 'control_type': 'Button', 'class_name': 'mmui::XTabBarItem'}
    Discovery = {'title': '发现', 'control_type': 'Button'}
    More = {'title': '更多', 'control_type': 'Button', 'found_index': 0}


class WeChatMainWindow:
    """主界面控件定义"""
    MainWindow = {'title': '微信', 'class_name': 'mmui::MainWindow', 'framework_id': 'Qt'}
    Toolbar = {'title': '导航', 'control_type': 'ToolBar'}
    ConversationList = {'title': '会话', 'control_type': 'List', 'framework_id': 'Qt'}
    Search = {'title': '搜索', 'control_type': 'Edit', 'class_name': 'mmui::XValidatorTextEdit'}
    EditArea = {'control_type': 'Edit', 'class_name': 'mmui::ChatInputField'}
    FriendChatList = {'title': '消息', 'control_type': 'List'}
    ContactsList = {'control_type': 'List', 'class_name': 'mmui::StickyHeaderRecyclerListView'}
    ProfileWindow = {'class_name': 'ContactProfileWnd', 'control_type': 'Pane', 'framework_id': 'Win32'}


class WeChatButtons:
    """常用按钮"""
    SendButton = {'control_type': 'Button', 'title': '发送(S)'}
    ChatMessageButton = {'title': '聊天信息', 'control_type': 'Button'}
    ConfirmButton = {'control_type': 'Button', 'title': '确定'}
    CancelButton = {'control_type': 'Button', 'title': '取消'}
    TagEditButton = {'control_type': 'Button', 'title': '点击编辑标签'}
    AddNewFriendButton = {'title': '添加朋友', 'control_type': 'Button'}
    AcceptButton = {'control_type': 'Button', 'title': '接受'}
    SettingsButton = {'control_type': 'Button', 'title': '设置', 'found_index': 0}
    MoreButton = {'title': '更多', 'control_type': 'Button'}
    MomentsButton = {'title': '朋友圈', 'control_type': 'Button',
                     'class_name': 'mmui::ExtensionDiscoverContentCell', 'found_index': 0}


class WeChatLists:
    """列表控件"""
    ConversationList = {'title': '会话', 'control_type': 'List'}
    ContactsList = {'title': '通讯录', 'control_type': 'List'}
    FriendChatList = {'title': '消息', 'control_type': 'List'}

