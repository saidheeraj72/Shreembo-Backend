"""Composed tools service."""

from .tools_exec import AVAILABLE_TOOLS, ToolsExecutionMixin
from .tools_decision import ToolsDecisionMixin


class ToolsService(ToolsExecutionMixin, ToolsDecisionMixin):
    """Service for tool selection and execution in chat/RAG."""


from . import tools_exec as _tools_exec
from . import tools_decision as _tools_decision

_tools_exec.ToolsService = ToolsService
_tools_decision.ToolsService = ToolsService

tools_service = ToolsService()
