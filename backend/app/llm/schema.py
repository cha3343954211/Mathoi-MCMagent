"""LLM 通用消息与工具协议（OpenAI 风格，作为内部统一表示）。

多模态支持
----------
content 可以是：
  - str  : 纯文本（向下兼容）
  - list : 内容块列表，每块为 {"type":"text","text":"..."} 或
           {"type":"image_url","image_url":{"url":"data:image/...;base64,...","detail":"auto"}}
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

# 单个内容块
ContentPart = dict[str, Any]
# content 的联合类型
MessageContent = Union[str, list[ContentPart]]


def _image_part(url: str, detail: str = "auto") -> ContentPart:
    """构造 image_url 内容块（url 可以是 http/https URL 或 data:... Base64 串）。"""
    return {"type": "image_url", "image_url": {"url": url, "detail": detail}}


def image_part_from_bytes(data: bytes, mime: str = "image/png", detail: str = "auto") -> ContentPart:
    b64 = base64.b64encode(data).decode()
    return _image_part(f"data:{mime};base64,{b64}", detail)


def image_part_from_file(path: Path, detail: str = "auto") -> ContentPart:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return image_part_from_bytes(path.read_bytes(), mime, detail)


def text_part(text: str) -> ContentPart:
    return {"type": "text", "text": text}


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: MessageContent = ""
    name: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    def to_openai(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d

    @staticmethod
    def user(text: str, images: Optional[list[ContentPart]] = None) -> "ChatMessage":
        """便捷工厂：纯文本或图文混合的 user 消息。"""
        if not images:
            return ChatMessage(role="user", content=text)
        parts: list[ContentPart] = [text_part(text)] + images
        return ChatMessage(role="user", content=parts)


class ToolSpec(BaseModel):
    """OpenAI function-calling 风格工具描述。"""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
