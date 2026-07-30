#!/usr/bin/env python3
"""End-to-end smoke test against a running API, driven by a real login.

Exercises the paths a user actually walks: log in, browse the repository, upload
a document, wait for it to index, ask a question about it over the chat
websocket, then clean up after itself.

This talks to whatever backend BASE_URL points at, with a real account. Uploads
and chat sessions are created for real and deleted at the end (pass --keep to
leave them). Point it at a throwaway environment if you would rather it not
touch production data.

Credentials are never stored in this file. Supply them by flag, environment, or
interactive prompt:

    export SMOKE_EMAIL=you@example.com SMOKE_PASSWORD='...'
    python scripts/smoke_test.py

    python scripts/smoke_test.py --email you@example.com          # prompts for password
    python scripts/smoke_test.py --base-url https://api.example.com
    python scripts/smoke_test.py --only auth,documents            # skip the chat phases
    python scripts/smoke_test.py --keep                           # leave test artifacts behind
    python scripts/smoke_test.py --org-id c37d3324-...            # abort if it lands elsewhere

Org context comes from the account's profile row, not from a request header, so
an account in several orgs tests whichever one it is currently switched to.
Pass --org-id (or SMOKE_ORG_ID) to make that explicit and fail fast otherwise.

Exit code is 0 only if every check passed.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from getpass import getpass
from typing import Dict, List, Optional

import httpx
import websockets

# How long to wait for an uploaded document to reach a terminal embedding state.
INDEX_TIMEOUT = 180.0
INDEX_POLL_INTERVAL = 3.0
# How long to wait for a chat answer to finish streaming.
CHAT_TIMEOUT = 180.0

# Planted in the uploaded document so the RAG answer can be checked for a fact
# that exists nowhere else — a model answering from general knowledge cannot
# produce these.
CANARY_CODE = "ZX-4417-QQ"
CANARY_AMOUNT = "847,392.55"

FIXTURE = f"""Smoke Test Reference Sheet

This document exists only to verify document ingestion and retrieval.

Contract reference code: {CANARY_CODE}
Total contract value: USD {CANARY_AMOUNT}
Counterparty: Northwind Freight Holdings
Effective date: 14 March 2031
Governing law: Singapore

Payment terms
Payment is due 45 days from invoice date. Late payment accrues interest at
1.25 percent per month. Invoices are settled in USD only.

Termination
Either party may terminate with 90 days written notice. Early termination by
the counterparty incurs a fee of USD 62,000.
"""

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)


class Report:
    """Collects check results so the run ends with a single verdict."""

    def __init__(self) -> None:
        self.rows: List[tuple] = []

    def record(
        self,
        name: str,
        ok: bool,
        detail: str = "",
        info: str = "",
        skipped: bool = False,
    ) -> bool:
        """Record one check.

        *detail* explains a failure and is shown only when the check fails —
        printing "not in results" beside a PASS is worse than printing nothing.
        *info* is context worth seeing either way (counts, ids, timings).
        """
        self.rows.append((name, ok, detail, skipped))
        if skipped:
            mark, colour = "SKIP", YELLOW
        elif ok:
            mark, colour = "PASS", GREEN
        else:
            mark, colour = "FAIL", RED
        shown = info if (ok or skipped) else " ".join(p for p in (info, detail) if p)
        line = f"  {colour}{mark}{RESET}  {name}"
        if shown:
            line += f"  {DIM}{shown}{RESET}"
        print(line, flush=True)
        return ok

    @property
    def failures(self) -> List[tuple]:
        return [r for r in self.rows if not r[1] and not r[3]]

    def summary(self) -> int:
        passed = sum(1 for r in self.rows if r[1] and not r[3])
        skipped = sum(1 for r in self.rows if r[3])
        failed = len(self.failures)
        print(f"\n{BOLD}{passed} passed, {failed} failed, {skipped} skipped{RESET}")
        if failed:
            print(f"\n{RED}Failed checks:{RESET}")
            for name, _, detail, _ in self.failures:
                print(f"  - {name}: {detail}")
        return 1 if failed else 0


def phase(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")


def describe(response: httpx.Response) -> str:
    """A short, safe description of a failed response."""
    body = response.text or ""
    if len(body) > 300:
        body = body[:300] + "…"
    return f"HTTP {response.status_code} {body}".strip()


class SmokeTest:
    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        keep: bool,
        expect_org: Optional[str] = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.keep = keep
        self.expect_org = expect_org
        self.report = Report()

        self.token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.org_id: Optional[str] = None
        self.unit_id: Optional[str] = None
        self.document_id: Optional[str] = None
        self.folder_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    # -- plumbing ----------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HTTP client is not open — call run()")
        return self._client

    @property
    def auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def get(self, path: str, **kw) -> httpx.Response:
        return await self.client.get(f"{self.base}{path}", headers=self.auth_headers, **kw)

    async def post(self, path: str, **kw) -> httpx.Response:
        return await self.client.post(f"{self.base}{path}", headers=self.auth_headers, **kw)

    async def patch(self, path: str, **kw) -> httpx.Response:
        return await self.client.patch(f"{self.base}{path}", headers=self.auth_headers, **kw)

    async def delete(self, path: str, **kw) -> httpx.Response:
        return await self.client.delete(f"{self.base}{path}", headers=self.auth_headers, **kw)

    # -- phases ------------------------------------------------------------

    async def check_reachable(self) -> bool:
        phase("Connectivity")
        try:
            r = await self.client.get(f"{self.base}/health", timeout=10)
        except Exception as e:
            return self.report.record(
                "GET /health", False, f"{type(e).__name__}: {e} — is the server running at {self.base}?"
            )
        return self.report.record("GET /health", r.status_code == 200, detail=describe(r))

    async def run_auth(self) -> bool:
        phase("Auth")
        r = await self.post("/api/v1/auth/login", json={"email": self.email, "password": self.password})
        if r.status_code != 200:
            return self.report.record("POST /auth/login", False, describe(r))
        data = r.json()
        self.token = data.get("access_token")
        user = data.get("user") or {}
        self.user_id = user.get("id")
        self.report.record(
            "POST /auth/login", bool(self.token and self.user_id),
            info=f"user={user.get('email')}",
        )
        if not self.token:
            return False

        r = await self.get("/api/v1/auth/me")
        if r.status_code != 200:
            return self.report.record("GET /auth/me", False, describe(r))
        me = r.json()
        self.org_id = me.get("org_id")
        self.report.record("GET /auth/me", True, info=f"org_id={self.org_id}")

        r = await self.get("/api/v1/auth/organizations")
        # Responds with {"organizations": [...], "count": n} — not a bare list.
        # Counting the envelope's keys would report "2 orgs" for any account.
        payload = r.json() if r.status_code == 200 else {}
        orgs = payload.get("organizations") or [] if isinstance(payload, dict) else []
        self.report.record(
            "GET /auth/organizations", r.status_code == 200 and isinstance(orgs, list),
            detail=describe(r),
            info=f"{len(orgs)} org(s)" if r.status_code == 200 else "",
        )
        for org in orgs:
            if not isinstance(org, dict):
                continue
            here = "  <-- active" if org.get("id") == self.org_id else ""
            print(f"    {DIM}{org.get('id')}  {org.get('name')} "
                  f"(role={org.get('title') or org.get('role') or '?'}){here}{RESET}")

        # Org context is derived from the profile row, not from a request header
        # — X-Org-ID is allowed through CORS but never read. So an account in
        # more than one org silently tests whichever one the profile points at.
        # When an org is named, refuse to run anywhere else rather than write
        # test documents into the wrong tenant.
        if self.expect_org:
            self.report.record(
                "authenticated context is the expected org",
                self.org_id == self.expect_org,
                detail=f"expected {self.expect_org}, got {self.org_id} — "
                       f"switch orgs in the app, or drop --org-id",
                info=f"{self.expect_org}",
            )
            if self.org_id != self.expect_org:
                return False
            self.report.record(
                "expected org is in the membership list",
                any(isinstance(o, dict) and o.get("id") == self.expect_org for o in orgs),
                detail="not present in /auth/organizations",
            )
        elif len(orgs) > 1:
            print(f"    {YELLOW}note{RESET} {DIM}account has {len(orgs)} orgs; "
                  f"testing in {self.org_id}. Pass --org-id to pin it.{RESET}")
        # An expired or malformed token must be refused, not silently accepted.
        r = await self.client.get(
            f"{self.base}/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        self.report.record(
            "GET /auth/me rejects a bad token", r.status_code in (401, 403),
            f"got HTTP {r.status_code}, expected 401/403",
        )
        return True

    async def run_documents(self) -> bool:
        phase("Documents")
        r = await self.get("/api/v1/documents/folders")
        if r.status_code != 200:
            return self.report.record("GET /documents/folders (root)", False, describe(r))
        root = r.json()
        units = [f for f in root.get("folders", []) if f.get("node_type") == "branch"]
        self.report.record(
            "GET /documents/folders (root)", True,
            info=f"{len(units)} unit(s), {len(root.get('documents', []))} file(s)",
        )
        if not units:
            return self.report.record(
                "a unit exists to upload into", False,
                "no branch/unit found — create one in the admin UI first",
            )
        self.unit_id = units[0]["id"]

        r = await self.get(f"/api/v1/documents/folders/{self.unit_id}?type=branch")
        self.report.record(
            f"GET /documents/folders/{{unit}} ({units[0].get('name')})",
            r.status_code == 200, detail=describe(r),
        )

        stamp = time.strftime("%Y%m%d-%H%M%S")
        r = await self.post(
            "/api/v1/documents/folders",
            json={"name": f"smoke-test-{stamp}", "branch_id": self.unit_id,
                  "description": "Created by scripts/smoke_test.py"},
        )
        if r.status_code in (200, 201):
            self.folder_id = r.json().get("id")
        self.report.record("POST /documents/folders", r.status_code in (200, 201),
                           detail=describe(r))

        # Upload into the folder we just made, so cleanup is unambiguous.
        filename = f"smoke-test-{stamp}.txt"
        files = {"file": (filename, FIXTURE.encode(), "text/plain")}
        form = {"branch_id": self.unit_id}
        if self.folder_id:
            form["parent_id"] = self.folder_id
        r = await self.post("/api/v1/documents/upload/direct", files=files, data=form, timeout=120)
        if r.status_code not in (200, 201):
            return self.report.record("POST /documents/upload/direct", False, describe(r))
        doc = r.json()
        self.document_id = doc.get("id")
        self.report.record("POST /documents/upload/direct", bool(self.document_id),
                           info=f"id={self.document_id}")
        if not self.document_id:
            return False

        # The point of this wait: a document must always reach a terminal state.
        # A run that hangs here means background embedding died without
        # recording it — the failure mode the UI used to poll on forever.
        deadline = time.time() + INDEX_TIMEOUT
        status, seen = None, []
        while time.time() < deadline:
            r = await self.get(f"/api/v1/documents/documents/{self.document_id}")
            if r.status_code != 200:
                return self.report.record("GET /documents/{id}", False, describe(r))
            status = r.json().get("embedding_status")
            if status not in seen:
                seen.append(status)
            if status in ("completed", "failed", "skipped"):
                break
            await asyncio.sleep(INDEX_POLL_INTERVAL)

        waited = int(INDEX_TIMEOUT - max(0, deadline - time.time()))
        self.report.record(
            "document reaches a terminal embedding state",
            status in ("completed", "failed", "skipped"),
            detail="still not terminal — the background embedding task died "
                   "without recording a result",
            info=f"{' -> '.join(str(s) for s in seen)} after ~{waited}s",
        )
        self.report.record(
            "document indexed successfully", status == "completed",
            detail=f"embedding_status={status}",
        )

        r = await self.get(f"/api/v1/documents/search?q=smoke-test-{stamp}")
        found = r.status_code == 200 and any(
            (item.get("document") or {}).get("id") == self.document_id for item in r.json()
        )
        self.report.record(
            "GET /documents/search finds it", found,
            detail=describe(r) if r.status_code != 200 else "uploaded document not in results",
        )

        r = await self.get(f"/api/v1/documents/documents/{self.document_id}/view")
        self.report.record(
            "GET /documents/{id}/view", r.status_code == 200 and bool(r.json().get("view_url")),
            detail=describe(r) if r.status_code != 200 else "no view_url in response",
        )

        r = await self.get(f"/api/v1/documents/documents/{self.document_id}/download")
        self.report.record(
            "GET /documents/{id}/download", r.status_code == 200 and bool(r.json().get("download_url")),
            detail=describe(r) if r.status_code != 200 else "no download_url in response",
        )

        r = await self.patch_document(description="Updated by smoke test")
        self.report.record("PUT /documents/{id}", r.status_code == 200,
                           detail=describe(r))
        return True

    async def patch_document(self, **fields) -> httpx.Response:
        return await self.client.put(
            f"{self.base}/api/v1/documents/documents/{self.document_id}",
            headers=self.auth_headers, json=fields,
        )

    async def run_chat_rest(self) -> bool:
        phase("Chat (REST)")
        r = await self.post(
            "/api/v1/chat/sessions",
            json={"title": "Smoke test session", "rag_enabled": True, "web_search_enabled": False},
        )
        if r.status_code not in (200, 201):
            return self.report.record("POST /chat/sessions", False, describe(r))
        session = r.json()
        self.session_id = session.get("id")
        self.report.record("POST /chat/sessions", bool(self.session_id),
                           info=f"id={self.session_id}")
        if not self.session_id:
            return False

        # The UI reads is_rag_enabled; the API has historically returned
        # rag_enabled. Check the flag survives the round trip under some name,
        # because a silently-absent flag reads as "RAG off" in the client.
        flag = session.get("rag_enabled", session.get("is_rag_enabled"))
        self.report.record(
            "session reports its RAG flag", flag is True,
            detail=f"rag_enabled={session.get('rag_enabled')!r} "
                   f"is_rag_enabled={session.get('is_rag_enabled')!r} (asked for True)",
        )

        r = await self.get("/api/v1/chat/sessions")
        self.report.record(
            "GET /chat/sessions", r.status_code == 200, detail=describe(r),
            info=f"{len(r.json())} session(s)" if r.status_code == 200 else "",
        )

        r = await self.get(f"/api/v1/chat/sessions/{self.session_id}")
        self.report.record(f"GET /chat/sessions/{{id}}", r.status_code == 200,
                           detail=describe(r))

        r = await self.patch(f"/api/v1/chat/sessions/{self.session_id}",
                             json={"title": "Smoke test session (renamed)"})
        self.report.record("PATCH /chat/sessions/{id}", r.status_code == 200,
                           detail=describe(r))

        r = await self.get("/api/v1/chat/usage")
        self.report.record("GET /chat/usage", r.status_code == 200,
                           detail=describe(r))
        return True

    async def run_chat_ws(self) -> bool:
        phase("Chat (websocket streaming + RAG)")
        if not self.session_id:
            return self.report.record("chat websocket", False, "no session to send into")

        ws_base = self.base.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{ws_base}/api/v1/chat/ws?token={self.token}"
        question = (
            "What is the contract reference code and the total contract value "
            "on the smoke test reference sheet?"
        )

        events: List[str] = []
        chunks: List[str] = []
        sources: List[dict] = []
        verdict: Optional[str] = None
        issues: List[dict] = []
        answer = ""
        error: Optional[str] = None

        try:
            async with websockets.connect(url, max_size=None) as ws:
                self.report.record("connect /api/v1/chat/ws", True, info="token accepted")
                await ws.send(json.dumps({
                    "type": "send_message",
                    "session_id": self.session_id,
                    "content": question,
                    "rag_enabled": True,
                    "web_search_enabled": False,
                }))

                deadline = time.time() + CHAT_TIMEOUT
                while time.time() < deadline:
                    remaining = deadline - time.time()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    if kind not in events:
                        events.append(kind)
                    if kind == "stream_chunk":
                        chunks.append(msg.get("content", ""))
                    elif kind == "rag_context":
                        sources = msg.get("data") or []
                    elif kind == "stream_error":
                        error = msg.get("error")
                        break
                    elif kind == "stream_end":
                        answer = msg.get("content") or "".join(chunks)
                        sources = msg.get("sources") or sources
                        verdict = msg.get("verdict")
                        issues = msg.get("issues") or []
                        break
        except Exception as e:
            return self.report.record("connect /api/v1/chat/ws", False, f"{type(e).__name__}: {e}")

        if error:
            return self.report.record("streamed answer", False, f"stream_error: {error}")

        self.report.record("received stream_start", "stream_start" in events)
        self.report.record(
            "answer streamed incrementally", len(chunks) > 1,
            detail="arrived as one blob — not actually streaming",
            info=f"{len(chunks)} chunk(s)",
        )
        self.report.record("received stream_end", bool(answer),
                           detail="no stream_end before timeout")
        print(f"    {DIM}events: {', '.join(e for e in events if e)}{RESET}")
        if answer:
            preview = answer.strip().replace("\n", " ")
            print(f"    {DIM}answer: {preview[:220]}{'…' if len(preview) > 220 else ''}{RESET}")

        self.report.record("retrieval returned sources", bool(sources),
                           detail="no sources — retrieval returned nothing",
                           info=f"{len(sources)} source(s)")
        cited_doc = any(s.get("document_id") == self.document_id for s in sources)
        self.report.record(
            "sources include the uploaded document", cited_doc,
            detail=f"document {self.document_id} not among {len(sources)} source(s)",
        )
        # The real test of grounding: these strings exist only in the fixture.
        self.report.record(
            f"answer contains the planted code {CANARY_CODE}", CANARY_CODE in answer,
            detail="the model did not retrieve the document's contents",
        )
        self.report.record(
            f"answer contains the planted amount {CANARY_AMOUNT}", CANARY_AMOUNT in answer,
            detail="figure missing or reformatted — the prompt requires exact figures",
        )
        self.report.record(
            "hallucination check reported a verdict", verdict is not None,
            detail="no verdict — judge disabled, timed out, or errored (it fails open)",
        )
        if verdict:
            print(f"    {DIM}verdict: {verdict}, {len(issues)} issue(s){RESET}")
            for issue in issues[:3]:
                print(f"    {DIM}  - [{issue.get('status')}] {str(issue.get('claim'))[:120]}{RESET}")
        self.report.record(
            "answer is fully grounded", verdict in (None, "supported"),
            detail=f"verdict={verdict} with {len(issues)} unsupported claim(s)",
        )

        r = await self.get(f"/api/v1/chat/sessions/{self.session_id}/messages")
        self.report.record(
            "GET /chat/sessions/{id}/messages reloads history",
            r.status_code == 200 and len(r.json()) >= 2,
            detail=describe(r) if r.status_code != 200
                   else f"only {len(r.json())} message(s), expected >= 2",
            info=f"{len(r.json())} message(s)" if r.status_code == 200 else "",
        )
        if r.status_code == 200:
            saved = [m for m in r.json() if m.get("role") == "assistant"]
            self.report.record(
                "persisted answer keeps its sources", bool(saved and saved[-1].get("sources")),
                detail="assistant message came back without sources — metadata dropped on reload",
            )
        return True

    async def run_cleanup(self) -> None:
        phase("Cleanup")
        if self.keep:
            self.report.record("cleanup", True, info="skipped (--keep)", skipped=True)
            print(f"    {DIM}document={self.document_id} folder={self.folder_id} "
                  f"session={self.session_id}{RESET}")
            return
        if self.session_id:
            r = await self.delete(f"/api/v1/chat/sessions/{self.session_id}")
            self.report.record("DELETE /chat/sessions/{id}", r.status_code in (200, 204),
                               detail=describe(r))
        if self.document_id:
            r = await self.delete(f"/api/v1/documents/documents/{self.document_id}")
            self.report.record("DELETE /documents/{id}", r.status_code in (200, 204),
                               detail=describe(r))
        if self.folder_id:
            r = await self.delete(f"/api/v1/documents/folders/{self.folder_id}")
            self.report.record("DELETE /documents/folders/{id}", r.status_code in (200, 204),
                               detail=describe(r))

    async def run(self, only: Optional[set]) -> int:
        async with httpx.AsyncClient(timeout=60) as client:
            self._client = client
            if not await self.check_reachable():
                return self.report.summary()
            if not await self.run_auth():
                return self.report.summary()
            try:
                if not only or "documents" in only:
                    await self.run_documents()
                if not only or "chat" in only:
                    if await self.run_chat_rest():
                        await self.run_chat_ws()
            finally:
                await self.run_cleanup()
        return self.report.summary()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="End-to-end smoke test for the documents and chat APIs.",
    )
    parser.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--email", default=os.environ.get("SMOKE_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("SMOKE_PASSWORD"))
    parser.add_argument(
        "--org-id", default=os.environ.get("SMOKE_ORG_ID"),
        help="refuse to run unless the login lands in this org (recommended for "
             "accounts belonging to more than one)",
    )
    parser.add_argument("--only", help="comma-separated phases to run: documents,chat")
    parser.add_argument("--keep", action="store_true", help="do not delete what the run created")
    args = parser.parse_args()

    # Prompting only works on a terminal. In CI or a piped run, say what is
    # missing instead of dying on EOFError from input().
    try:
        email = args.email or input("Email: ").strip()
        password = args.password or getpass("Password: ")
    except (EOFError, KeyboardInterrupt):
        print(
            "\nNo credentials supplied and no terminal to prompt on. "
            "Set SMOKE_EMAIL and SMOKE_PASSWORD, or pass --email/--password.",
            file=sys.stderr,
        )
        return 2
    if not email or not password:
        print("Email and password are required.", file=sys.stderr)
        return 2

    only = {p.strip() for p in args.only.split(",")} if args.only else None

    print(f"{BOLD}Smoke test{RESET}  {DIM}{args.base_url}  as {email}"
          f"{'  org ' + args.org_id if args.org_id else ''}{RESET}")
    test = SmokeTest(args.base_url, email, password, args.keep, args.org_id)
    try:
        return asyncio.run(test.run(only))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
