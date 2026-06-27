"""
Email Issues — extract a table of recurring issues/problems from a mailbox.

Companion to ``mind_map`` but tabular instead of graph-shaped. The LLM names each
issue with a stable snake_case slug and flags whether the emails contain a
resolution; the backend then computes the *deterministic* facts — how many emails
reference the issue (its repeat count) and when it was first/last raised — from
the email headers themselves, so those numbers never depend on the model guessing.

This module is read-only and stateless — nothing is persisted.
"""
import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from uuid import UUID

from openai import AsyncOpenAI

from src.config import settings
from src.core.database import db
from src.email_agent import accounts as account_store
from src.email_agent import gmail_client

logger = logging.getLogger(__name__)

# Same turn-based scanning approach as the mind map: small batches, more turns,
# so the model is never overloaded.
_EMAILS_PER_TURN = 20
_MAX_TURNS = 30
# How many already-found issue slugs to remind the model of each turn (for reuse).
_KNOWN_ISSUES_HINT = 60

_PROMPT = """You analyze a batch of emails and extract the distinct ISSUES they raise.

An "issue" is a genuine problem, bug, complaint, blocker, risk, open request or
unmet need someone is raising. Ignore newsletters, receipts, marketing and pure
FYI mail.

Return STRICT JSON of this exact shape (no prose):
{
  "issues": [
    {
      "key": "payment_delays",          // short snake_case slug naming the issue (MUST stay stable across emails)
      "title": "Payment delays",        // short human-readable title
      "summary": "Vendor invoices are going unpaid for over 30 days", // one sentence
      "severity": "high",               // one of: low, medium, high
      "solved": true,                   // true ONLY if an email shows the issue was resolved/answered
      "solution": "Finance cleared the backlog on 12 Jun", // the resolution if solved, else ""
      "source_email_ids": ["<id>", "<id>"]  // ids (from the input) that raise or discuss THIS issue
    }
  ]
}

Rules:
- Reuse the SAME `key` slug when two emails describe the same underlying issue, so they merge into one row.
- Keep slugs concise and semantic (e.g. "late_shipments", not "issue_1").
- Only set "solved": true when an email actually contains a fix, answer or confirmation it was resolved. When unsure, use false.
- You may be given a list of issue keys found in earlier turns — REUSE those exact slugs when relevant.
- If the batch has no real issues, return {"issues": []}."""

_SEVERITIES = {"low", "medium", "high"}
_DEFAULT_SEVERITY = "medium"


def _slugify(key: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (key or "").strip().lower())
    return s.strip("_") or "unknown"


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _email_for_prompt(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "from": m.get("from"),
        "subject": m.get("subject"),
        "date": m.get("date"),
        "snippet": (m.get("snippet") or "")[:500],
    }


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    """Best-effort parse of an email Date header into an aware datetime."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _extract_turn(
    client: AsyncOpenAI,
    emails: list[dict],
    known_keys: list[str],
) -> list[dict]:
    """Run the extractor over one turn's worth of emails; returns a list of issues."""
    payload = {"emails": [_email_for_prompt(m) for m in emails]}
    if known_keys:
        payload["known_issue_keys"] = known_keys
    try:
        resp = await client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
    except Exception:  # noqa: BLE001 - one bad turn shouldn't kill the whole scan
        logger.exception("Issue extraction turn failed")
        return []

    content = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Issue turn returned non-JSON: %s", content[:200])
        return []
    return data.get("issues") or []


def _merge_turn_into(issues: dict[str, dict], batch: list[dict]) -> None:
    """Merge one turn's issues into the running accumulator (keyed by slug)."""
    for raw in batch:
        key = raw.get("key")
        if not key:
            continue
        slug = _slugify(key)
        src_ids = [s for s in (raw.get("source_email_ids") or []) if s]
        severity = raw.get("severity")
        if severity not in _SEVERITIES:
            severity = _DEFAULT_SEVERITY
        solved = bool(raw.get("solved"))
        solution = (raw.get("solution") or "").strip()

        if slug in issues:
            existing = issues[slug]
            existing["source_email_ids"] = sorted(
                set(existing["source_email_ids"]) | set(src_ids)
            )
            # A later turn finding a solution should "win" — once solved, stays solved.
            if solved and not existing["solved"]:
                existing["solved"] = True
                existing["solution"] = solution
            elif solved and not existing["solution"] and solution:
                existing["solution"] = solution
        else:
            issues[slug] = {
                "key": slug,
                "title": raw.get("title") or key,
                "summary": raw.get("summary") or "",
                "severity": severity,
                "solved": solved,
                "solution": solution if solved else "",
                "source_email_ids": sorted(set(src_ids)),
            }


def _finalize(
    issues: dict[str, dict],
    email_dates: dict[str, datetime],
) -> list[dict]:
    """
    Compute deterministic facts (repeat count, first/last raised) from the email
    headers, then return rows sorted by how often each issue recurs.
    """
    rows: list[dict] = []
    for issue in issues.values():
        ids = issue["source_email_ids"]
        dates = [email_dates[i] for i in ids if i in email_dates]
        first = min(dates) if dates else None
        last = max(dates) if dates else None
        rows.append({
            **issue,
            "occurrences": len(ids),
            "first_raised": first.isoformat() if first else None,
            "last_raised": last.isoformat() if last else None,
        })
    # Most-repeated first; ties broken by most-recently raised.
    rows.sort(
        key=lambda r: (r["occurrences"], r["last_raised"] or ""),
        reverse=True,
    )
    return rows


async def build_issue_table(
    account: dict,
    query: Optional[str] = None,
    max_emails: int = 200,
) -> dict:
    """
    Read recent emails turn-by-turn and return a table of recurring issues.

    Returns: {issues, email_count, generated_at}
    """
    access_token = await account_store.get_valid_access_token(account)

    gmail_query = query if query is not None else "newer_than:3m (in:inbox OR in:sent)"

    client = _client()
    issues: dict[str, dict] = {}
    email_dates: dict[str, datetime] = {}

    page_token: Optional[str] = None
    scanned = 0
    turns = 0

    while turns < _MAX_TURNS and scanned < max_emails:
        turn_size = min(_EMAILS_PER_TURN, max_emails - scanned)
        page = await gmail_client.list_messages(
            access_token,
            query=gmail_query or None,
            max_results=turn_size,
            page_token=page_token,
        )
        emails = page["messages"]
        page_token = page.get("next_page_token")

        if emails:
            for m in emails:
                dt = _parse_date(m.get("date"))
                if dt and m.get("id"):
                    email_dates[m["id"]] = dt
            known = list(issues.keys())[-_KNOWN_ISSUES_HINT:]
            batch = await _extract_turn(client, emails, known)
            _merge_turn_into(issues, batch)
            scanned += len(emails)
            turns += 1

        if not page_token:
            break

    return {
        "issues": _finalize(issues, email_dates),
        "email_count": scanned,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_scan(
    account_id: UUID,
    user_id: UUID,
    result: dict,
    query: Optional[str] = None,
) -> None:
    """Cache a scan result as the latest snapshot for an account (upsert)."""
    row = {
        "account_id": str(account_id),
        "user_id": str(user_id),
        "issues": result["issues"],
        "email_count": result["email_count"],
        "query": query,
        "generated_at": result["generated_at"],
    }
    db.admin.table("email_issue_scans").upsert(
        row, on_conflict="account_id"
    ).execute()


def get_latest_scan(account_id: UUID, user_id: UUID) -> Optional[dict]:
    """Return the cached snapshot for an account, or None if never scanned."""
    result = (
        db.admin.table("email_issue_scans")
        .select("issues, email_count, generated_at")
        .eq("account_id", str(account_id))
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        return None
    return result.data
