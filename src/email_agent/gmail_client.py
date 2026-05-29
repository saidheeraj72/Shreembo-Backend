"""
Gmail REST API client (users.messages).

Thin async wrapper over the Gmail v1 REST API using httpx. All calls take a
valid OAuth access token (minted/refreshed by accounts.py).
"""
import base64
import logging
from email.message import EmailMessage
from typing import Optional

import httpx

from src.core.exceptions import AppException

logger = logging.getLogger(__name__)

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


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


async def list_messages(
    access_token: str,
    query: Optional[str] = None,
    max_results: int = 20,
    label_ids: Optional[list[str]] = None,
) -> list[dict]:
    """
    List messages (optionally filtered by a Gmail search `query`), then fetch
    each message's metadata/body. Returns parsed message dicts.
    """
    params: dict = {"maxResults": max_results}
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = label_ids

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{API_BASE}/messages", headers=_auth_headers(access_token), params=params
        )
        if resp.status_code != 200:
            logger.error("Gmail list failed: %s", resp.text)
            raise AppException("Failed to list Gmail messages.", status_code=502)

        ids = [m["id"] for m in resp.json().get("messages", [])]

        results = []
        for mid in ids:
            mresp = await client.get(
                f"{API_BASE}/messages/{mid}",
                headers=_auth_headers(access_token),
                params={"format": "full"},
            )
            if mresp.status_code == 200:
                results.append(parse_message(mresp.json()))
    return results


async def get_message(access_token: str, message_id: str) -> dict:
    """Fetch and parse a single message by id."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{API_BASE}/messages/{message_id}",
            headers=_auth_headers(access_token),
            params={"format": "full"},
        )
    if resp.status_code == 404:
        raise AppException("Email not found.", status_code=404)
    if resp.status_code != 200:
        logger.error("Gmail get failed: %s", resp.text)
        raise AppException("Failed to fetch Gmail message.", status_code=502)
    return parse_message(resp.json())


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

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
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
