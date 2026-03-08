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


class ToolsDecisionMixin:
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
            logger.error("Tool determination error: %s", e)
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
