"""Tools service for OpenAI function calling / agent framework."""
from typing import List, Dict, Any, Optional
from uuid import UUID
from openai import AsyncOpenAI

from src.services.rag_service import RAGService
from src.services.web_search_service import web_search_service
from src.config import settings


# Tool definitions for OpenAI function calling
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search through internal company documents, files, and knowledge base. "
                "Use this when the user asks about specific documents, company information, "
                "project details, or technical documentation that would be stored in files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant documents"
                    },
                    "focus": {
                        "type": "string",
                        "description": "Optional focus area or document type to narrow the search",
                        "enum": ["technical", "business", "compliance", "general"]
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the internet for current information, news, events, or general knowledge. "
                "Use this when the user asks about current events, external information, "
                "public data, or anything not specific to internal documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query for web search"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


class ToolsService:
    """Service for managing AI tools and function calling."""

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

    @staticmethod
    async def determine_tools_to_use(
        user_message: str,
        chat_history: List[Dict[str, str]],
        rag_enabled: bool = True,
        web_search_enabled: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Use OpenAI function calling to determine which tools should be used.

        Args:
            user_message: The user's message
            chat_history: Previous chat history
            rag_enabled: Whether RAG is enabled for this session
            web_search_enabled: Whether web search is enabled

        Returns:
            List of tool calls to execute (empty if no tools needed)
        """
        # Build available tools based on what's enabled
        available_tools = []
        if rag_enabled:
            available_tools.append(TOOL_DEFINITIONS[0])  # search_documents
        if web_search_enabled and settings.SERPER_API_KEY:
            available_tools.append(TOOL_DEFINITIONS[1])  # search_web

        # If no tools are available, return empty
        if not available_tools:
            return []

        try:
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

            # Build messages for tool selection
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an AI assistant with access to tools. "
                        "Analyze the user's message and determine which tools (if any) you need to use to answer accurately. "
                        "Only use tools when necessary - simple conversational messages don't need tools. "
                        "You can call multiple tools if needed (e.g., both documents and web search)."
                    )
                }
            ]

            # Add recent history for context
            messages.extend(chat_history[-5:])
            messages.append({"role": "user", "content": user_message})

            # Call OpenAI with function calling
            response = await client.chat.completions.create(
                model=settings.OPENAI_CHAT_MODEL,
                messages=messages,
                tools=available_tools,
                tool_choice="auto",  # Let the model decide
                temperature=0.3  # Lower temperature for more deterministic tool selection
            )

            # Extract tool calls
            message = response.choices[0].message
            if message.tool_calls:
                return [
                    {
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments
                    }
                    for tool_call in message.tool_calls
                ]

            return []

        except Exception as e:
            print(f"Tool determination error: {e}")
            # Fallback to simple heuristics
            return await ToolsService._fallback_tool_selection(
                user_message, rag_enabled, web_search_enabled
            )

    @staticmethod
    async def _fallback_tool_selection(
        user_message: str,
        rag_enabled: bool,
        web_search_enabled: bool
    ) -> List[Dict[str, Any]]:
        """
        Fallback tool selection using simple heuristics if OpenAI call fails.
        """
        tools = []
        lowered = user_message.lower()

        # Simple greetings don't need tools
        if len(lowered.split()) < 5 and any(
            w in lowered for w in ["hi", "hello", "hey", "thanks", "bye"]
        ):
            return []

        # Keywords suggesting document search
        doc_keywords = [
            "document", "file", "project", "report", "our", "company",
            "internal", "policy", "procedure", "contract", "agreement"
        ]
        if rag_enabled and any(keyword in lowered for keyword in doc_keywords):
            tools.append({
                "id": "fallback_doc",
                "name": "search_documents",
                "arguments": f'{{"query": "{user_message}"}}'
            })

        # Keywords suggesting web search
        web_keywords = [
            "latest", "current", "news", "today", "recent", "what is",
            "who is", "when did", "2024", "2025", "2026"
        ]
        if web_search_enabled and any(keyword in lowered for keyword in web_keywords):
            tools.append({
                "id": "fallback_web",
                "name": "search_web",
                "arguments": f'{{"query": "{user_message}"}}'
            })

        return tools


tools_service = ToolsService()
