"""Auto-split tools service part."""
from typing import Dict, Any, List, Optional
from uuid import UUID
import json
import logging

from src.llm.rag import RAGService
from src.llm.web_search import web_search_service
from src.core.openai_client import openai_client

logger = logging.getLogger(__name__)


AVAILABLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "Search through user's documents for relevant information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "top_k": {"type": "integer", "description": "Number of results", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for current information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Web search query"},
                    "limit": {"type": "integer", "description": "Number of results", "default": 3},
                },
                "required": ["query"],
            },
        },
    },
]


class ToolsExecutionMixin:
    @staticmethod
    async def execute_tool(
        tool_name: str,
        tool_arguments: Dict[str, Any],
        user_id: UUID,
        org_id: Optional[UUID],
        session_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """
        Execute a tool function based on the tool name.

        Args:
            tool_name: Name of the tool to execute
            tool_arguments: Arguments for the tool
            user_id: User executing the tool
            org_id: Organization context
            session_id: Optional session context

        Returns:
            Dictionary with tool execution results
        """
        if tool_name == "search_documents":
            return await ToolsService._execute_search_documents(
                tool_arguments, user_id, org_id, session_id
            )
        elif tool_name == "search_web":
            return await ToolsService._execute_search_web(tool_arguments)
        else:
            return {
                "error": f"Unknown tool: {tool_name}",
                "results": []
            }

    @staticmethod
    async def _execute_search_documents(
        arguments: Dict[str, Any],
        user_id: UUID,
        org_id: Optional[UUID],
        session_id: Optional[UUID]
    ) -> Dict[str, Any]:
        """Execute document search tool."""
        query = arguments.get("query", "")
        if not query:
            return {"error": "Query is required", "results": []}

        try:
            results = await RAGService.search_documents(
                query=query,
                user_id=user_id,
                org_id=org_id,
                session_id=session_id
            )

            return {
                "tool": "search_documents",
                "query": query,
                "results": results,
                "count": len(results)
            }
        except Exception as e:
            return {
                "error": f"Document search failed: {str(e)}",
                "results": []
            }

    @staticmethod
    async def _execute_search_web(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute web search tool."""
        query = arguments.get("query", "")
        if not query:
            return {"error": "Query is required", "results": []}

        if not settings.SERPER_API_KEY:
            return {
                "error": "Web search is not configured",
                "results": []
            }

        try:
            results = await web_search_service.search(query)
            return {
                "tool": "search_web",
                "query": query,
                "results": results,
                "count": len(results)
            }
        except Exception as e:
            return {
                "error": f"Web search failed: {str(e)}",
                "results": []
            }

