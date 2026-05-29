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

MAX_TOOL_ITERATIONS = 8

# Gmail system label ids used by the label-modifying tools.
LABEL_UNREAD = "UNREAD"
LABEL_INBOX = "INBOX"
LABEL_STARRED = "STARRED"
LABEL_IMPORTANT = "IMPORTANT"
LABEL_TRASH = "TRASH"
LABEL_SPAM = "SPAM"

SYSTEM_PROMPT = """You are an email assistant operating on the user's connected mailbox ({email_address}).

Available tools:
- search_emails: find emails with Gmail search syntax (e.g. "from:boss is:unread newer_than:7d"). Returns up to 50 at a time.
- count_emails: get an exact total for a query (e.g. "is:unread") without listing them. Use this for "how many" questions.
- read_email: read the full body of a specific email by id.
- read_thread: read an entire conversation thread by thread id.
- list_attachments: list a message's attachments (names/types/sizes).
- send_email: send a new email or a reply.
- create_draft: save a draft without sending.
- forward_email: forward an existing email to new recipients.
- mark_read / mark_unread: change read state of one or more emails.
- archive_emails: remove emails from the inbox (keeps them in All Mail).
- trash_emails: move emails to Trash (reversible).
- star_emails / unstar_emails: star or unstar emails.
- mark_important / mark_not_important: change the importance marker.
- mark_spam / unmark_spam: move to/from Spam.
- list_labels: list all labels in the mailbox.
- apply_label / remove_label: add or remove a label by name (apply creates it if missing).

Guidelines:
- The action tools (mark_read, archive_emails, trash_emails, star_emails, labels, etc.) accept EITHER explicit message_ids OR a `query`. To act on an ENTIRE set ("mark ALL unread as read", "archive everything from X"), pass the query (e.g. "is:unread") and it will affect every match — do NOT list them first or cap at a page. Use message_ids only when acting on specific emails the user picked.
- For "how many" questions use count_emails. For browsing, use search_emails (returns up to 50; pass next_page_token to go further).
- Before any action that SENDS or MODIFIES the mailbox (send_email, forward_email, trash_emails, mark_spam, applying/removing labels in bulk, etc.), make sure the user's intent is clear. For drafting a new email, write the draft in your reply and wait for confirmation before calling send_email — prefer create_draft if they want to review in Gmail.
- When replying, read the email first and pass its id as in_reply_to and its thread id as thread_id to keep the thread.
- Be concise. Summarize emails clearly. Never invent email content — only use what the tools return.
- After performing an action, confirm what you did (e.g. "Marked 25 emails as read")."""


def _ids_param(description: str) -> dict:
    """
    Bulk-action schema: target either explicit ids OR a Gmail query.

    Passing `query` acts on EVERY matching message (paginated server-side),
    so it is the right way to do things like 'mark all unread as read' without
    first listing them. Provide exactly one of the two.
    """
    return {
        "type": "object",
        "properties": {
            "message_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": description,
            },
            "query": {
                "type": "string",
                "description": "Gmail query selecting ALL messages to act on (e.g. 'is:unread'). Use instead of message_ids to affect the entire result set.",
            },
        },
    }


def _fn(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


TOOLS = [
    _fn(
        "search_emails",
        "Search the mailbox using Gmail search syntax. Returns matching emails with id, sender, subject, date and a snippet. Use count_emails if you only need a total.",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query, e.g. 'from:alice is:unread', 'subject:invoice', 'newer_than:3d'. Empty string returns recent emails.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of emails to return (1-50).",
                },
                "page_token": {
                    "type": "string",
                    "description": "Token from a previous search's next_page_token to fetch the next page.",
                },
            },
            "required": ["query"],
        },
    ),
    _fn(
        "count_emails",
        "Count how many emails match a Gmail query (e.g. 'is:unread') without reading bodies. Use this for 'how many' questions instead of search_emails.",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query, e.g. 'is:unread', 'from:boss'. Empty string counts all mail.",
                },
            },
            "required": ["query"],
        },
    ),
    _fn(
        "read_email",
        "Read the full content of a single email by its id.",
        {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "The email id."},
            },
            "required": ["message_id"],
        },
    ),
    _fn(
        "read_thread",
        "Read every message in a conversation thread (full bodies, oldest first).",
        {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "The Gmail thread id."},
            },
            "required": ["thread_id"],
        },
    ),
    _fn(
        "list_attachments",
        "List the attachments (filename, type, size) of a single email by id.",
        {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "The email id."},
            },
            "required": ["message_id"],
        },
    ),
    _fn(
        "send_email",
        "Send an email or reply. Only call after the user has confirmed.",
        {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text email body."},
                "cc": {"type": "string", "description": "Optional Cc recipients."},
                "bcc": {"type": "string", "description": "Optional Bcc recipients."},
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
    ),
    _fn(
        "create_draft",
        "Create a draft email (saved, not sent) for the user to review in Gmail.",
        {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text email body."},
                "cc": {"type": "string"},
                "bcc": {"type": "string"},
                "in_reply_to": {"type": "string"},
                "thread_id": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    ),
    _fn(
        "forward_email",
        "Forward an existing email (by id) to new recipients, with an optional note.",
        {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Id of the email to forward."},
                "to": {"type": "string", "description": "Recipient email address."},
                "note": {
                    "type": "string",
                    "description": "Optional message to add above the forwarded content.",
                },
            },
            "required": ["message_id", "to"],
        },
    ),
    _fn("mark_read", "Mark one or more emails as read.", _ids_param("Ids of emails to mark read.")),
    _fn("mark_unread", "Mark one or more emails as unread.", _ids_param("Ids of emails to mark unread.")),
    _fn("archive_emails", "Archive emails (remove from inbox).", _ids_param("Ids of emails to archive.")),
    _fn("trash_emails", "Move emails to Trash (reversible).", _ids_param("Ids of emails to trash.")),
    _fn("star_emails", "Star one or more emails.", _ids_param("Ids of emails to star.")),
    _fn("unstar_emails", "Remove the star from one or more emails.", _ids_param("Ids of emails to unstar.")),
    _fn("mark_important", "Mark emails as important.", _ids_param("Ids of emails to mark important.")),
    _fn("mark_not_important", "Remove the important marker from emails.", _ids_param("Ids of emails.")),
    _fn("mark_spam", "Move emails to Spam.", _ids_param("Ids of emails to mark as spam.")),
    _fn("unmark_spam", "Move emails out of Spam back to the inbox.", _ids_param("Ids of emails.")),
    _fn(
        "list_labels",
        "List all labels (system and user) in the mailbox.",
        {"type": "object", "properties": {}},
    ),
    _fn(
        "apply_label",
        "Apply a label (by name) to emails; creates the label if it does not exist. Target by message_ids or a query.",
        {
            "type": "object",
            "properties": {
                "message_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ids of emails to label.",
                },
                "query": {
                    "type": "string",
                    "description": "Gmail query selecting ALL emails to label (use instead of message_ids).",
                },
                "label_name": {"type": "string", "description": "The label name."},
            },
            "required": ["label_name"],
        },
    ),
    _fn(
        "remove_label",
        "Remove a label (by name) from emails. Target by message_ids or a query.",
        {
            "type": "object",
            "properties": {
                "message_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ids of emails.",
                },
                "query": {
                    "type": "string",
                    "description": "Gmail query selecting ALL emails (use instead of message_ids).",
                },
                "label_name": {"type": "string", "description": "The label name to remove."},
            },
            "required": ["label_name"],
        },
    ),
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
            max_results = min(int(args.get("max_results", 10) or 10), 50)
            result = await gmail_client.list_messages(
                access_token,
                query=args.get("query") or None,
                max_results=max_results,
                page_token=args.get("page_token") or None,
            )
            return {
                "emails": [
                    {
                        "id": r["id"],
                        "from": r.get("from"),
                        "subject": r.get("subject"),
                        "date": r.get("date"),
                        "snippet": r.get("snippet"),
                        "unread": "UNREAD" in (r.get("label_ids") or []),
                    }
                    for r in result["messages"]
                ],
                "next_page_token": result["next_page_token"],
            }

        if name == "count_emails":
            return await gmail_client.count_messages(
                access_token, query=args.get("query") or None
            )

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

        if name == "read_thread":
            msgs = await gmail_client.get_thread(access_token, args["thread_id"])
            return {
                "thread_id": args["thread_id"],
                "messages": [
                    {
                        "id": m["id"],
                        "from": m.get("from"),
                        "to": m.get("to"),
                        "date": m.get("date"),
                        "subject": m.get("subject"),
                        "body": (m.get("body") or "")[:4000],
                    }
                    for m in msgs
                ],
            }

        if name == "list_attachments":
            attachments = await gmail_client.list_attachments(
                access_token, args["message_id"]
            )
            return {"attachments": attachments}

        if name == "send_email":
            result = await gmail_client.send_message(
                access_token,
                sender=account["email_address"],
                to=args["to"],
                subject=args["subject"],
                body=args["body"],
                cc=args.get("cc"),
                bcc=args.get("bcc"),
                in_reply_to=args.get("in_reply_to"),
                thread_id=args.get("thread_id"),
            )
            return {"sent": True, "id": result.get("id"), "to": args["to"]}

        if name == "create_draft":
            result = await gmail_client.create_draft(
                access_token,
                sender=account["email_address"],
                to=args["to"],
                subject=args["subject"],
                body=args["body"],
                cc=args.get("cc"),
                bcc=args.get("bcc"),
                in_reply_to=args.get("in_reply_to"),
                thread_id=args.get("thread_id"),
            )
            return {"draft_created": True, "id": result.get("id"), "to": args["to"]}

        if name == "forward_email":
            original = await gmail_client.get_message(access_token, args["message_id"])
            note = args.get("note") or ""
            subject = original.get("subject") or ""
            if not subject.lower().startswith("fwd:"):
                subject = f"Fwd: {subject}"
            forwarded_body = (
                f"{note}\n\n" if note else ""
            ) + (
                "---------- Forwarded message ----------\n"
                f"From: {original.get('from')}\n"
                f"Date: {original.get('date')}\n"
                f"Subject: {original.get('subject')}\n"
                f"To: {original.get('to')}\n\n"
                f"{original.get('body') or ''}"
            )
            result = await gmail_client.send_message(
                access_token,
                sender=account["email_address"],
                to=args["to"],
                subject=subject,
                body=forwarded_body,
            )
            return {"forwarded": True, "id": result.get("id"), "to": args["to"]}

        # --- Label / state changes (all via batchModify) ---
        label_actions = {
            "mark_read": ([], [LABEL_UNREAD]),
            "mark_unread": ([LABEL_UNREAD], []),
            "archive_emails": ([], [LABEL_INBOX]),
            "trash_emails": ([LABEL_TRASH], []),
            "star_emails": ([LABEL_STARRED], []),
            "unstar_emails": ([], [LABEL_STARRED]),
            "mark_important": ([LABEL_IMPORTANT], []),
            "mark_not_important": ([], [LABEL_IMPORTANT]),
            "mark_spam": ([LABEL_SPAM], [LABEL_INBOX]),
            "unmark_spam": ([LABEL_INBOX], [LABEL_SPAM]),
        }
        if name in label_actions:
            ids = await self._resolve_target_ids(access_token, args)
            if ids is None:
                return {"error": "Provide message_ids or a query to act on."}
            add, remove = label_actions[name]
            count = await gmail_client.batch_modify(
                access_token, message_ids=ids, add_label_ids=add, remove_label_ids=remove
            )
            return {"action": name, "count": count}

        if name == "list_labels":
            return {"labels": await gmail_client.list_labels(access_token)}

        if name in ("apply_label", "remove_label"):
            ids = await self._resolve_target_ids(access_token, args)
            if ids is None:
                return {"error": "Provide message_ids or a query to act on."}
            label_id = await self._resolve_label_id(
                access_token, args["label_name"], create=(name == "apply_label")
            )
            if not label_id:
                return {"error": f"Label '{args['label_name']}' not found."}
            count = await gmail_client.batch_modify(
                access_token,
                message_ids=ids,
                add_label_ids=[label_id] if name == "apply_label" else [],
                remove_label_ids=[label_id] if name == "remove_label" else [],
            )
            return {"action": name, "label": args["label_name"], "count": count}

        return {"error": f"Unknown tool: {name}"}

    async def _resolve_target_ids(
        self, access_token: str, args: dict
    ) -> Optional[list[str]]:
        """
        Resolve the set of message ids a bulk action targets.

        Prefers explicit `message_ids`; otherwise expands a `query` to every
        matching id (paginated). Returns None if neither was supplied.
        """
        ids = args.get("message_ids")
        if ids:
            return list(ids)
        query = args.get("query")
        if query is not None:
            return await gmail_client.list_message_ids(access_token, query=query or None)
        return None

    async def _resolve_label_id(
        self, access_token: str, label_name: str, create: bool
    ) -> Optional[str]:
        """Find a label id by name (case-insensitive); optionally create it."""
        labels = await gmail_client.list_labels(access_token)
        for lbl in labels:
            if (lbl.get("name") or "").lower() == label_name.lower():
                return lbl.get("id")
        if create:
            created = await gmail_client.create_label(access_token, label_name)
            return created.get("id")
        return None

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
