"""
Email Mind Map — extract a graph of problems/issues from a mailbox.

The LLM emits a *normalized graph* (concepts + relations), never coordinates.
The backend then assigns deterministic, stable node ids by hashing each
concept slug, so the frontend can reconcile graphs across re-scans (add new
ids, grey missing ids, update changed labels in place) and keep manual edits.

This module is read-only and stateless — nothing is persisted.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI

from src.config import settings
from src.email_agent import accounts as account_store
from src.email_agent import gmail_client

logger = logging.getLogger(__name__)

# How many emails the LLM digests in a single turn. Kept small so the model
# never gets overloaded / runs out of context — we do MORE turns instead.
_EMAILS_PER_TURN = 20
# Safety cap on the number of turns (so a huge mailbox can't loop forever).
_MAX_TURNS = 30
# How many existing concept slugs to remind the model of each turn (for reuse).
_KNOWN_CONCEPTS_HINT = 60

ROOT_CONCEPT = "root"
ROOT_ID = "n_root"

# Allowed node categories — keeps colours/legend stable on the frontend.
_NODE_TYPES = {
    "problem", "risk", "blocker", "request", "deadline", "topic", "person", "project",
}
_DEFAULT_TYPE = "topic"

# Selectable mind-map types. Each one focuses the extractor on something
# different; the JSON shape and merge logic stay identical.
MAP_TYPES: dict[str, dict] = {
    "problems": {
        "root_label": "Inbox issues",
        "node_types": "problem, risk, blocker, request, deadline, topic",
        "focus": (
            "Surface the PROBLEMS and ISSUES the emails raise. Only extract genuine "
            "problems, risks, blockers, open requests or deadlines. Ignore newsletters, "
            "receipts and pure FYI mail."
        ),
    },
    "actions": {
        "root_label": "Action items",
        "node_types": "request, deadline, blocker, topic",
        "focus": (
            "Surface the ACTION ITEMS, open REQUESTS and COMMITMENTS in the emails — "
            "things someone is expected to do, with their deadlines and blockers. Ignore "
            "purely informational mail."
        ),
    },
    "topics": {
        "root_label": "Topics",
        "node_types": "topic",
        "focus": (
            "Surface the main TOPICS and THEMES discussed across the emails (use type "
            "'topic' for all). Group related discussions under shared concept slugs."
        ),
    },
    "people": {
        "root_label": "People",
        "node_types": "person, topic",
        "focus": (
            "Surface the PEOPLE/senders involved (type 'person') and the key topics or "
            "issues each is associated with (type 'topic'). Connect a person to the "
            "topics they raise (relation 'raises')."
        ),
    },
}
DEFAULT_MAP_TYPE = "problems"

_PROMPT_TEMPLATE = """You analyze a batch of emails and build a graph. {focus}

Return STRICT JSON of this exact shape (no prose):
{{
  "nodes": [
    {{
      "concept": "payment_delays",            // short snake_case slug naming the concept (stable across emails)
      "label": "Payment delays",              // human-readable label
      "type": "problem",                      // one of: {node_types}
      "summary": "Vendor invoices unpaid >30d", // one short sentence
      "source_email_ids": ["<id>", "<id>"]    // ids (from the input) that evidence this concept
    }}
  ],
  "edges": [
    {{ "source_concept": "root", "target_concept": "payment_delays", "relation": "surfaces" }},
    {{ "source_concept": "payment_delays", "target_concept": "vendor_churn", "relation": "causes" }}
  ]
}}

Rules:
- Reuse the SAME `concept` slug when two emails describe the same underlying thing, so they merge into one node.
- Every node must connect back to "root" (relation "surfaces"). Add node-to-node edges only when there is a real relationship (relation: causes, blocks, relates_to, part_of, raises).
- Keep slugs concise and semantic (e.g. "late_shipments", not "item_1").
- You may be given a list of concepts already discovered in earlier turns — REUSE those exact slugs when relevant, so the graph stays connected across turns.
- If the batch has nothing relevant, return {{"nodes": [], "edges": []}}."""


def _build_prompt(map_type: str) -> str:
    cfg = MAP_TYPES.get(map_type, MAP_TYPES[DEFAULT_MAP_TYPE])
    return _PROMPT_TEMPLATE.format(focus=cfg["focus"], node_types=cfg["node_types"])


def _slugify(concept: str) -> str:
    """Normalize a concept into a canonical slug used for hashing."""
    s = re.sub(r"[^a-z0-9]+", "_", (concept or "").strip().lower())
    return s.strip("_") or "unknown"


def _concept_id(concept: str) -> str:
    """Deterministic, stable node id derived from the concept slug."""
    slug = _slugify(concept)
    if slug == ROOT_CONCEPT:
        return ROOT_ID
    h = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:10]
    return f"n_{h}"


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _email_for_prompt(m: dict) -> dict:
    """Trim an email to the fields the extractor needs."""
    return {
        "id": m.get("id"),
        "from": m.get("from"),
        "subject": m.get("subject"),
        "date": m.get("date"),
        "snippet": (m.get("snippet") or "")[:500],
    }


async def _extract_turn(
    client: AsyncOpenAI,
    prompt: str,
    emails: list[dict],
    known_concepts: list[str],
) -> dict:
    """
    Run the extractor over one turn's worth of emails; returns {nodes, edges}.

    `known_concepts` are slugs already discovered in earlier turns; passing them
    lets the model reuse slugs so the graph stays connected across turns.
    """
    payload = {"emails": [_email_for_prompt(m) for m in emails]}
    if known_concepts:
        payload["known_concepts"] = known_concepts
    try:
        resp = await client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
    except Exception:  # noqa: BLE001 - one bad turn shouldn't kill the whole scan
        logger.exception("Mind-map extraction turn failed")
        return {"nodes": [], "edges": []}

    content = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Mind-map turn returned non-JSON: %s", content[:200])
        return {"nodes": [], "edges": []}
    return {
        "nodes": data.get("nodes") or [],
        "edges": data.get("edges") or [],
    }


def _merge_turn_into(
    nodes: dict[str, dict],
    edges: dict[str, dict],
    batch: dict,
) -> None:
    """
    Merge one turn's node/edge lists into the running accumulators (in place).

    Nodes are keyed by deterministic concept id; source_email_ids are unioned.
    Edges are deduped by (source, target, relation).
    """
    for raw in batch["nodes"]:
        concept = raw.get("concept")
        if not concept:
            continue
        nid = _concept_id(concept)
        node_type = raw.get("type")
        if node_type not in _NODE_TYPES:
            node_type = _DEFAULT_TYPE
        src_ids = [s for s in (raw.get("source_email_ids") or []) if s]

        if nid in nodes:
            existing = nodes[nid]
            existing["source_email_ids"] = sorted(
                set(existing["source_email_ids"]) | set(src_ids)
            )
        else:
            nodes[nid] = {
                "id": nid,
                "concept": _slugify(concept),
                "label": raw.get("label") or concept,
                "type": node_type,
                "summary": raw.get("summary") or "",
                "source_email_ids": sorted(set(src_ids)),
            }

    for raw in batch["edges"]:
        src_c = raw.get("source_concept")
        tgt_c = raw.get("target_concept")
        if not src_c or not tgt_c:
            continue
        source = _concept_id(src_c)
        target = _concept_id(tgt_c)
        if source == target:
            continue
        relation = raw.get("relation") or "relates_to"
        eid = f"e_{source}_{target}_{relation}"
        edges[eid] = {
            "id": eid,
            "source": source,
            "target": target,
            "relation": relation,
        }


def _finalize(nodes: dict[str, dict], edges: dict[str, dict]) -> dict:
    """Attach orphans to root, drop dangling edges, return graph lists."""
    for nid in nodes:
        if nid == ROOT_ID:
            continue
        if not any(e["target"] == nid for e in edges.values()):
            eid = f"e_{ROOT_ID}_{nid}_surfaces"
            edges[eid] = {
                "id": eid,
                "source": ROOT_ID,
                "target": nid,
                "relation": "surfaces",
            }

    valid = set(nodes)
    clean_edges = [
        e for e in edges.values() if e["source"] in valid and e["target"] in valid
    ]
    return {"nodes": list(nodes.values()), "edges": clean_edges}


def _root_node(label: str) -> dict:
    return {
        "id": ROOT_ID,
        "concept": ROOT_CONCEPT,
        "label": label,
        "type": "root",
        "summary": "Surfaced from your mailbox",
        "source_email_ids": [],
    }


async def build_mind_map(
    account: dict,
    query: Optional[str] = None,
    max_emails: int = 200,
    map_type: str = DEFAULT_MAP_TYPE,
) -> dict:
    """
    Read recent emails turn-by-turn and return a graph for the chosen map type.

    Emails are processed in small sequential turns (`_EMAILS_PER_TURN`), merging
    into one accumulating graph, until the mailbox page is exhausted, `max_emails`
    is reached, or `_MAX_TURNS` is hit. Keeping each turn small avoids exhausting
    the LLM — we simply do more turns instead.

    Returns: {nodes, edges, email_count, generated_at}
    """
    access_token = await account_store.get_valid_access_token(account)

    # Default: everything sent or received in the last ~3 months.
    gmail_query = query if query is not None else "newer_than:3m (in:inbox OR in:sent)"

    cfg = MAP_TYPES.get(map_type, MAP_TYPES[DEFAULT_MAP_TYPE])
    prompt = _build_prompt(map_type)

    client = _client()
    nodes: dict[str, dict] = {ROOT_ID: _root_node(cfg["root_label"])}
    edges: dict[str, dict] = {}

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
            known = [
                n["concept"]
                for n in nodes.values()
                if n["id"] != ROOT_ID
            ][-_KNOWN_CONCEPTS_HINT:]
            batch = await _extract_turn(client, prompt, emails, known)
            _merge_turn_into(nodes, edges, batch)
            scanned += len(emails)
            turns += 1

        if not page_token:
            break

    graph = _finalize(nodes, edges)
    graph["email_count"] = scanned
    graph["generated_at"] = datetime.now(timezone.utc).isoformat()
    return graph
