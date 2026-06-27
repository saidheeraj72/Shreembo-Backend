"""
Gmail REST API client (users.messages).

Thin async wrapper over the Gmail v1 REST API using httpx. All calls take a
valid OAuth access token (minted/refreshed by accounts.py).
"""
import asyncio
import base64
import logging
from email.message import EmailMessage
from typing import Optional

import httpx

from src.core.exceptions import AppException

logger = logging.getLogger(__name__)

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# Tuning knobs for talking to the Gmail API.
_CONCURRENCY = 10          # parallel message fetches per list call
_BATCH_MODIFY_LIMIT = 1000  # Gmail's max ids per batchModify request
_METADATA_HEADERS = ["Subject", "From", "To", "Cc", "Date"]


def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _b64url_decode(data: str) -> bytes:
    """Decode Gmail's base64url payload (handles missing padding)."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _header(headers: list[dict], name: str) -> Optional[str]:
    name_l = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_l:
            return h.get("value")
    return None


def _extract_body(payload: dict) -> str:
    """Walk the MIME tree and return the best text body (prefers text/plain)."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})

    if mime == "text/plain" and body.get("data"):
        return _b64url_decode(body["data"]).decode("utf-8", errors="replace")

    parts = payload.get("parts") or []
    # Prefer text/plain across parts, fall back to text/html.
    plain, html = None, None
    for part in parts:
        result = _extract_body(part)
        if part.get("mimeType") == "text/plain" and result and plain is None:
            plain = result
        elif part.get("mimeType") == "text/html" and result and html is None:
            html = result
        elif result and plain is None and html is None:
            plain = result
    if plain:
        return plain
    if html:
        return html
    if body.get("data"):
        return _b64url_decode(body["data"]).decode("utf-8", errors="replace")
    return ""


def parse_message(msg: dict) -> dict:
    """Normalize a Gmail message resource into a flat dict."""
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "snippet": msg.get("snippet", ""),
        "subject": _header(headers, "Subject"),
        "from": _header(headers, "From"),
        "to": _header(headers, "To"),
        "cc": _header(headers, "Cc"),
        "date": _header(headers, "Date"),
        "label_ids": msg.get("labelIds", []),
        "body": _extract_body(payload),
    }


async def _fetch_metadata(
    client: httpx.AsyncClient,
    access_token: str,
    message_id: str,
    sem: asyncio.Semaphore,
) -> Optional[dict]:
    """Fetch a single message's metadata (headers + snippet, no body)."""
    async with sem:
        resp = await client.get(
            f"{API_BASE}/messages/{message_id}",
            headers=_auth_headers(access_token),
            params={
                "format": "metadata",
                "metadataHeaders": _METADATA_HEADERS,
            },
        )
    if resp.status_code != 200:
        logger.warning("Gmail metadata fetch failed for %s: %s", message_id, resp.text)
        return None
    return parse_message(resp.json())


async def list_messages(
    access_token: str,
    query: Optional[str] = None,
    max_results: int = 25,
    label_ids: Optional[list[str]] = None,
    page_token: Optional[str] = None,
) -> dict:
    """
    List messages (optionally filtered by a Gmail `query`) for a single page.

    Uses `format=metadata` (headers + snippet, no body) and fetches all
    messages on the page concurrently for speed. Returns:
        {"messages": [parsed...], "next_page_token": str|None}
    Order is preserved (most recent first, as Gmail returns).
    """
    params: dict = {"maxResults": max_results}
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = label_ids
    if page_token:
        params["pageToken"] = page_token

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{API_BASE}/messages", headers=_auth_headers(access_token), params=params
        )
        if resp.status_code != 200:
            logger.error("Gmail list failed: %s", resp.text)
            raise AppException("Failed to list Gmail messages.", status_code=502)

        data = resp.json()
        ids = [m["id"] for m in data.get("messages", [])]
        next_page_token = data.get("nextPageToken")

        sem = asyncio.Semaphore(_CONCURRENCY)
        fetched = await asyncio.gather(
            *(_fetch_metadata(client, access_token, mid, sem) for mid in ids)
        )

    messages = [m for m in fetched if m is not None]
    return {"messages": messages, "next_page_token": next_page_token}


async def list_message_ids(
    access_token: str,
    query: Optional[str] = None,
    max_total: int = 5000,
) -> list[str]:
    """
    Return all message ids matching `query` (paginating, ids only — no body),
    up to `max_total`. Used by bulk actions that operate on an entire result
    set (e.g. mark *all* unread as read).
    """
    ids: list[str] = []
    page_token: Optional[str] = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        while len(ids) < max_total:
            params: dict = {"maxResults": 500}
            if query:
                params["q"] = query
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(
                f"{API_BASE}/messages",
                headers=_auth_headers(access_token),
                params=params,
            )
            if resp.status_code != 200:
                logger.error("Gmail list ids failed: %s", resp.text)
                raise AppException("Failed to list Gmail messages.", status_code=502)
            data = resp.json()
            ids.extend(m["id"] for m in data.get("messages", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return ids[:max_total]


async def count_messages(
    access_token: str,
    query: Optional[str] = None,
    max_pages: int = 20,
) -> dict:
    """
    Count messages matching a Gmail `query` by paging through ids only
    (no body fetch — cheap). Returns {"count", "capped"}; `capped` is True
    if there were more pages than `max_pages` (count is then a lower bound).
    """
    total = 0
    page_token: Optional[str] = None
    pages = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while pages < max_pages:
            params: dict = {"maxResults": 500}
            if query:
                params["q"] = query
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(
                f"{API_BASE}/messages",
                headers=_auth_headers(access_token),
                params=params,
            )
            if resp.status_code != 200:
                logger.error("Gmail count failed: %s", resp.text)
                raise AppException("Failed to count Gmail messages.", status_code=502)
            data = resp.json()
            total += len(data.get("messages", []))
            page_token = data.get("nextPageToken")
            pages += 1
            if not page_token:
                break
    return {"count": total, "capped": bool(page_token)}


async def get_message(access_token: str, message_id: str) -> dict:
    """Fetch and parse a single message by id."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{API_BASE}/messages/{message_id}",
            headers=_auth_headers(access_token),
            params={"format": "full"},
        )
    if resp.status_code in (400, 404):
        # 400 = malformed/invalid id (often a thread id or RFC822 Message-Id was
        # passed instead of the Gmail message id); 404 = no such message.
        raise AppException(
            f"Email '{message_id}' not found or its id is invalid.", status_code=404
        )
    if resp.status_code != 200:
        logger.error("Gmail get failed (%s): %s", resp.status_code, resp.text)
        raise AppException(
            f"Failed to fetch Gmail message ({resp.status_code}): {resp.text[:300]}",
            status_code=502,
        )
    return parse_message(resp.json())


async def get_thread(access_token: str, thread_id: str) -> list[dict]:
    """Fetch all messages in a thread (full bodies), oldest first."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{API_BASE}/threads/{thread_id}",
            headers=_auth_headers(access_token),
            params={"format": "full"},
        )
    if resp.status_code == 404:
        raise AppException("Thread not found.", status_code=404)
    if resp.status_code != 200:
        logger.error("Gmail get thread failed: %s", resp.text)
        raise AppException("Failed to fetch thread.", status_code=502)
    return [parse_message(m) for m in resp.json().get("messages", [])]


def _build_raw_message(
    sender: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    in_reply_to: Optional[str] = None,
) -> str:
    """Build a base64url-encoded RFC822 message for send/draft."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


async def send_message(
    access_token: str,
    sender: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """
    Build a MIME message and send it via Gmail.
    `in_reply_to` should be the RFC822 Message-Id of the email being replied to.
    """
    raw = _build_raw_message(sender, to, subject, body, cc, bcc, in_reply_to)
    payload: dict = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{API_BASE}/messages/send",
            headers=_auth_headers(access_token),
            json=payload,
        )
    if resp.status_code not in (200, 201):
        logger.error("Gmail send failed: %s", resp.text)
        raise AppException("Failed to send email via Gmail.", status_code=502)
    return resp.json()


async def create_draft(
    access_token: str,
    sender: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """Create a draft email (not sent). Returns the created draft resource."""
    raw = _build_raw_message(sender, to, subject, body, cc, bcc, in_reply_to)
    message: dict = {"raw": raw}
    if thread_id:
        message["threadId"] = thread_id
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{API_BASE}/drafts",
            headers=_auth_headers(access_token),
            json={"message": message},
        )
    if resp.status_code not in (200, 201):
        logger.error("Gmail draft create failed: %s", resp.text)
        raise AppException("Failed to create draft.", status_code=502)
    return resp.json()


async def batch_modify(
    access_token: str,
    message_ids: list[str],
    add_label_ids: Optional[list[str]] = None,
    remove_label_ids: Optional[list[str]] = None,
) -> int:
    """
    Add/remove labels on one or more messages (Gmail batchModify).

    Most mailbox actions are label changes: mark read = remove 'UNREAD',
    archive = remove 'INBOX', star = add 'STARRED', trash = add 'TRASH', etc.
    Returns the number of messages affected.
    """
    if not message_ids:
        return 0
    add = add_label_ids or []
    remove = remove_label_ids or []
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Gmail caps batchModify at 1000 ids per request — chunk larger sets.
        for start in range(0, len(message_ids), _BATCH_MODIFY_LIMIT):
            chunk = message_ids[start : start + _BATCH_MODIFY_LIMIT]
            resp = await client.post(
                f"{API_BASE}/messages/batchModify",
                headers=_auth_headers(access_token),
                json={"ids": chunk, "addLabelIds": add, "removeLabelIds": remove},
            )
            # batchModify returns 204 No Content on success.
            if resp.status_code not in (200, 204):
                logger.error("Gmail batchModify failed: %s", resp.text)
                raise AppException("Failed to update messages.", status_code=502)
    return len(message_ids)


async def list_labels(access_token: str) -> list[dict]:
    """List all labels (system + user) as [{id, name, type}]."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{API_BASE}/labels", headers=_auth_headers(access_token)
        )
    if resp.status_code != 200:
        logger.error("Gmail list labels failed: %s", resp.text)
        raise AppException("Failed to list labels.", status_code=502)
    return [
        {"id": lbl.get("id"), "name": lbl.get("name"), "type": lbl.get("type")}
        for lbl in resp.json().get("labels", [])
    ]


async def create_label(access_token: str, name: str) -> dict:
    """Create a user label; returns {id, name}."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{API_BASE}/labels",
            headers=_auth_headers(access_token),
            json={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
    if resp.status_code not in (200, 201):
        logger.error("Gmail create label failed: %s", resp.text)
        raise AppException("Failed to create label.", status_code=502)
    data = resp.json()
    return {"id": data.get("id"), "name": data.get("name")}


def _collect_attachments(payload: dict) -> list[dict]:
    """Recursively collect attachment metadata from a message payload."""
    found: list[dict] = []
    body = payload.get("body", {})
    filename = payload.get("filename")
    if filename and body.get("attachmentId"):
        found.append(
            {
                "filename": filename,
                "mime_type": payload.get("mimeType"),
                "size": body.get("size"),
                "attachment_id": body.get("attachmentId"),
            }
        )
    for part in payload.get("parts") or []:
        found.extend(_collect_attachments(part))
    return found


async def list_attachments(access_token: str, message_id: str) -> list[dict]:
    """Return metadata for a message's attachments (no file contents)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{API_BASE}/messages/{message_id}",
            headers=_auth_headers(access_token),
            params={"format": "full"},
        )
    if resp.status_code == 404:
        raise AppException("Email not found.", status_code=404)
    if resp.status_code != 200:
        logger.error("Gmail get (attachments) failed: %s", resp.text)
        raise AppException("Failed to fetch message.", status_code=502)
    return _collect_attachments(resp.json().get("payload", {}))
