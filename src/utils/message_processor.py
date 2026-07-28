"""
消息处理器（移植自 xm-bot4 utils/message_processor.py — 110行部分反编译）

原始文件: utils/message_processor.py (PARTIAL(1), 110 lines)
解析混合消息内容（文本、图片、文件、微信支付等）。
"""
import re
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)



@dataclass
class MessageComponent:
    """消息组件"""
    type: str = 'text'
    content: str = ''


@dataclass
class ParsedMessage:
    """解析后的消息"""
    type: str = 'text'
    components: List[MessageComponent] = None

    def __post_init__(self):
        if self.components is None:
            self.components = []


class MessageProcessor:
    """消息处理器（完整移植自 xm-bot4，修复反编译中的 None 引用问题）"""

    def __init__(self):
        self.MESSAGE_TYPES = {
            'TEXT': 'text',
            'IMAGE': 'image',
            'MIXED': 'mixed',
            'FILE': 'file',
            'WXPAY_QR': 'wxpay_qr',
        }

    def parse_message(self, content: str) -> ParsedMessage:
        """解析消息内容"""
        if not content:
            return ParsedMessage(type=self.MESSAGE_TYPES['TEXT'], components=[])

        components = []
        last_index = 0
        text_buffer = ''

        pattern = (
            r'(?:(!\[[^\]]*?\]\((weixin://wxpay/bizpayurl\?pr=[a-zA-Z0-9]+[^)]*)\))'
            r'|(!\[[^\]]*?\]\((?!weixin://wxpay)([^)]+?)\))'
            r'|(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)'
            r'|(https?://[^\s<>"[\]{}|\\^`]+))'
        )

        all_matches = list(re.finditer(pattern, content, re.IGNORECASE))

        for match in all_matches:
            match_index = match.start()
            full_match = match.group(0)

            text_before = content[last_index:match_index]
            if text_before:
                text_buffer += text_before

            groups = match.groups()
            url = None
            component_type = None

            # 微信支付二维码
            if groups[0] and groups[1]:
                url = groups[1]
                component_type = self.MESSAGE_TYPES['WXPAY_QR']
            # Markdown 图片链接
            elif groups[2] and groups[3]:
                url = groups[3]
                component_type = self._determine_url_type(url)
            # Base64 图片
            elif groups[4]:
                url = groups[4]
                component_type = self.MESSAGE_TYPES['IMAGE']
            # 普通 URL
            elif groups[5]:
                url = groups[5]
                component_type = self._determine_url_type(url)

            if url and component_type:
                if component_type == self.MESSAGE_TYPES['TEXT']:
                    text_buffer += full_match
                else:
                    if text_buffer:
                        components.append(MessageComponent(
                            type=self.MESSAGE_TYPES['TEXT'],
                            content=text_buffer))
                        text_buffer = ''
                    components.append(MessageComponent(
                        type=component_type, content=url))

            last_index = match.end()

        remaining_text = content[last_index:]
        if remaining_text:
            text_buffer += remaining_text

        if text_buffer:
            components.append(MessageComponent(
                type=self.MESSAGE_TYPES['TEXT'],
                content=text_buffer))

        # 确定整体消息类型
        if len(components) > 1:
            return ParsedMessage(
                type=self.MESSAGE_TYPES['MIXED'],
                components=components)
        elif components:
            return ParsedMessage(
                type=components[0].type,
                components=components)
        else:
            return ParsedMessage(
                type=self.MESSAGE_TYPES['TEXT'],
                components=[])

    def _determine_url_type(self, url: str) -> str:
        """判断 URL 类型（图片、文件或文本）

        使用多层策略进行 URL 类型识别：
        1. 文件扩展名检查（快速、可靠）
        2. 备选方案处理
        """
        extension_type = self._check_file_extension(url)
        if extension_type != self.MESSAGE_TYPES['TEXT']:
            return extension_type
        return self._fallback_url_detection(url)

    def _check_file_extension(self, url: str) -> str:
        """检查文件扩展名"""
        if re.search(
            r'\.(jpg|jpeg|png|gif|webp|bmp|svg|ico|tiff?)(?:[^)\s]*?)$',
            url, re.IGNORECASE
        ):
            return self.MESSAGE_TYPES['IMAGE']
        if re.search(
            r'\.(pdf|docx?|xlsx?|pptx?|txt|rtf|odt|ods|odp)(?:[^)\s]*?)$',
            url, re.IGNORECASE
        ):
            return self.MESSAGE_TYPES['FILE']
        return self.MESSAGE_TYPES['TEXT']

    def _fallback_url_detection(self, url: str) -> str:
        """备选 URL 类型检测（包含已知域名匹配和 HTTP HEAD 探针）"""
        # 1. 已知图片/文件域名匹配
        image_hosts = ['img.', 'image.', 'pic.', 'photo.', 'cdn.']
        if any(host in url.lower() for host in image_hosts):
            return self.MESSAGE_TYPES['IMAGE']
        
        # 2. HTTP HEAD 探针
        if url.startswith(('http://', 'https://')):
            try:
                import httpx
                # 设定较短的超时，避免长时间挂起
                with httpx.Client(timeout=1.5) as client:
                    resp = client.head(url, follow_redirects=True)
                    content_type = resp.headers.get("Content-Type", "")
                    if content_type:
                        return self._mime_type_to_message_type(content_type)
            except Exception as e:
                logger.debug(f"[MessageProcessor] HEAD 探针探测 {url} 失败: {e}")
                
        return self.MESSAGE_TYPES['TEXT']

    def _mime_type_to_message_type(self, mime_type: str) -> str:
        """MIME 类型转换为消息类型"""
        if not mime_type:
            return self.MESSAGE_TYPES['TEXT']
        mime_lower = mime_type.lower()
        if mime_lower.startswith('image/'):
            return self.MESSAGE_TYPES['IMAGE']
        if mime_lower.startswith('application/pdf') or 'octet-stream' in mime_lower:
            return self.MESSAGE_TYPES['FILE']
        # 一些常见的办公文档格式
        if any(keyword in mime_lower for keyword in ['msword', 'vnd.openxmlformats', 'officedocument', 'excel', 'powerpoint']):
            return self.MESSAGE_TYPES['FILE']
        return self.MESSAGE_TYPES['TEXT']

