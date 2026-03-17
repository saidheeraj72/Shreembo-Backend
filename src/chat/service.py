"""Composed chat service."""

from .sessions import ChatSessionsMixin
from .messages import ChatMessagesMixin


class ChatService(ChatSessionsMixin, ChatMessagesMixin):
    """Service for chat sessions and messages."""


from . import sessions as _sessions
from . import messages as _messages

_sessions.ChatService = ChatService
_messages.ChatService = ChatService

# Singleton instance
chat_service = ChatService()
