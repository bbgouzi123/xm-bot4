import os
from typing import List

from .messager_reader import get_all_messages_impl
from .messager_writer import send_message_impl, send_files_impl


class WeChatMessagerMixin:
    def get_all_messages(self, parse_file: bool = False,
                        context_count: int = 20,
                        session_name: str = "",
                        scroll_to_bottom: bool = False) -> List:
        """获取当前聊天窗口的所有可见消息"""
        return get_all_messages_impl(self, parse_file, context_count, session_name, scroll_to_bottom)

    def send_message(self, who: str, message: str, wxid: str = None) -> bool:
        """发送文本消息"""
        return send_message_impl(self, who, message, wxid)

    def SendMsg(self, message: str, who: str = "", quote_msg_id: str = "") -> bool:
        return self.send_message(who, message)

    def SendFiles(self, who: str, file_path: str, wxid: str = None) -> bool:
        """发送文件"""
        return send_files_impl(self, who, file_path, wxid)

    def send_voice_by_favorite(self, who: str, favorite_name: str, wxid: str = None) -> bool:
        """通过收藏夹转发语音 - 委托给 messager_voice 模块"""
        from src.uia.modules.messager_voice import WeChatVoiceMixin
        return WeChatVoiceMixin.send_voice_by_favorite(self, who, favorite_name, wxid=wxid)

    def send_voice_by_tts_clone(self, who: str, text: str, voice_id: str, wxid: str = None) -> bool:
        """TTS 克隆音色内录发送 - 委托给 messager_voice 模块"""
        from src.uia.modules.messager_voice import WeChatVoiceMixin
        return WeChatVoiceMixin.send_voice_by_tts_clone(self, who, text, voice_id, wxid=wxid)

    def invite_friend_to_group(self, group_name: str, friend_name: str) -> bool:
        """邀请好友加入指定群聊"""
        from src.uia.group_helper import invite_friend_to_group
        return invite_friend_to_group(self, group_name, friend_name)
