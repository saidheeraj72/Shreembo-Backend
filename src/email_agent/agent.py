"""
Email Agent — conversational tool-calling layer.

Runs an OpenAI chat-completions loop where the model can call tools that
read/search the connected mailbox and send emails on the user's behalf.
"""
import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from src.config import settings
from src.email_agent import accounts as account_store
from src.email_agent import gmail_client

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = """You are an email assistant operating on the user's connected mailbox ({email_address}).

You can:
- search_emails: find emails using Gmail search syntax (e.g. "from:boss is:unread newer_than:7d").
- read_email: read the full body of a specific email by id.
- send_email: send a new email or a reply.

Guidelines:
- When the user asks about their inbox, use search_emails first, then read_email for details.
- When asked to draft an email, write the draft in your reply and ask the user to confirm before sending. Do NOT call send_email until the user clearly confirms.
- When replying to an email, read it first to get context, and pass its message id as in_reply_to and its thread id as thread_id to keep the thread.
- Be concise. Summarize emails clearly. Never invent email content — only use what the tools return.
- After sending, confirm to the user what was sent and to whom."""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": "Search the mailbox using Gmail search syntax. Returns a list of matching emails with id, sender, subject, date and a snippet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query, e.g. 'from:alice is:unread', 'subject:invoice', 'newer_than:3d'. Empty string returns recent emails.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (1-25).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": "Read the full content of a single email by its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "The email id."},
                },
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email. Only call after the user has confirmed the draft.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string"},
                    "body": {"type": "string", "description": "Plain-text email body."},
                    "in_reply_to": {
                        "type": "string",
                        "description": "Optional id of the email being replied to.",
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "Optional Gmail thread id to keep the reply in-thread.",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
]


class EmailAgent:
    """Conversational agent bound to a single connected mailbox."""

    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return self._client

    async def _execute_tool(self, account: dict, name: str, args: dict) -> dict:
        """Run a single tool call against the mailbox; returns a JSON-able result."""
        access_token = await account_store.get_valid_access_token(account)

        if name == "search_emails":
            max_results = min(int(args.get("max_results", 10) or 10), 25)
            results = await gmail_client.list_messages(
                access_token, query=args.get("query") or None, max_results=max_results
            )
            return {
                "emails": [
                    {
                        "id": r["id"],
                        "from": r.get("from"),
                        "subject": r.get("subject"),
                        "date": r.get("date"),
                        "snippet": r.get("snippet"),
                    }
                    for r in results
                ]
            }

        if name == "read_email":
            msg = await gmail_client.get_message(access_token, args["message_id"])
            return {
                "id": msg["id"],
                "thread_id": msg.get("thread_id"),
                "from": msg.get("from"),
                "to": msg.get("to"),
                "subject": msg.get("subject"),
                "date": msg.get("date"),
                "body": (msg.get("body") or "")[:8000],
            }

        if name == "send_email":
            result = await gmail_client.send_message(
                access_token,
                sender=account["email_address"],
                to=args["to"],
                subject=args["subject"],
                body=args["body"],
                in_reply_to=args.get("in_reply_to"),
                thread_id=args.get("thread_id"),
            )
            return {"sent": True, "id": result.get("id"), "to": args["to"]}

        return {"error": f"Unknown tool: {name}"}

    async def run(self, account: dict, history: list[dict]) -> dict:
        """
        Run the tool-calling loop.

        Args:
            account: the email_accounts row (with email_address).
            history: list of {role, content} messages (user/assistant).

        Returns:
            {"reply": str, "actions": [tool names that were executed]}
        """
        messages: list[dict] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(email_address=account["email_address"]),
            }
        ]
        for m in history:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                messages.append({"role": m["role"], "content": m["content"]})

        actions: list[str] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            response = await self.client.chat.completions.create(
                model=settings.OPENAI_CHAT_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
            msg = response.choices[0].message

            if not msg.tool_calls:
                return {"reply": msg.content or "", "actions": actions}

            # Record the assistant's tool-call turn.
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                actions.append(name)
                try:
                    result = await self._execute_tool(account, name, args)
                except Exception as e:  # noqa: BLE001 - surface tool errors to the model
                    logger.exception("Email agent tool '%s' failed", name)
                    result = {"error": str(e)}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    }
                )

        # Tool budget exhausted — ask the model for a final answer without tools.
        final = await self.client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=messages,
        )
        return {"reply": final.choices[0].message.content or "", "actions": actions}


# Module-level singleton
email_agent = EmailAgent()
