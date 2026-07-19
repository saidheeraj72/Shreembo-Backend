"""
Seed the Bounce Board knowledge base with 28 starter issues (7 per industry),
inserting rows into bounce_board_kb_issues and embedding them into Qdrant.

Idempotent: rows upsert on a deterministic UUID derived from each slug, and
Qdrant point IDs are deterministic too — safe to re-run.

Usage (repo root, after applying migrations/add_bounce_board.sql):
    python scripts/seed_bounce_board_kb.py
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bounce_board import kb  # noqa: E402

_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "bounce-board-kb-seed")


def _sid(slug: str) -> str:
    """Deterministic UUID per seed slug so re-runs upsert instead of duplicating."""
    return str(uuid.uuid5(_NS, slug))


# (slug, industry, source_type, severity, title, tags, summary, related_slugs)
SEED_ISSUES = [
    # Shipping / Marine
    ("kb-shp-1", "shipping", "incident", "critical", "Port congestion causing 3–5 day vessel wait at discharge", ["port", "demurrage", "berth"], "Recurring berth unavailability at hub ports drove demurrage costs up 38% QoQ; waiting time correlates with weekly arrival bunching.", ["kb-shp-2", "kb-shp-5"]),
    ("kb-shp-2", "shipping", "best_practice", "medium", "Virtual arrival / just-in-time steaming to cut waiting and bunker burn", ["bunker", "JIT", "speed"], "JIT arrival agreements with terminals reduced anchorage waiting 41% and bunker consumption 12% in documented deployments.", ["kb-shp-1"]),
    ("kb-shp-3", "shipping", "regulation", "high", "IMO CII rating decline risk for aging fleet segments", ["IMO", "CII", "emissions"], "Carbon Intensity Indicator trajectories show C→D band slippage for vessels >15 years unless speed/route optimization is applied.", []),
    ("kb-shp-4", "shipping", "sop", "medium", "Standard port call coordination SOP (agent, terminal, customs)", ["SOP", "port call"], "A 14-step pre-arrival checklist aligning agent nominations, terminal windows and customs pre-clearance; cuts idle time 15–20%.", ["kb-shp-1"]),
    ("kb-shp-5", "shipping", "incident", "high", "Crew change delays triggering charter-party disputes", ["crew", "charter"], "Crew rotation failures at restricted ports created off-hire claims; mitigation is a 3-port fallback rotation matrix.", []),
    ("kb-shp-6", "shipping", "manual", "low", "Bunker procurement and hedging desk manual", ["bunker", "hedging"], "Procedures for staggered bunker purchasing, quality claims and hedge ratios by route exposure.", ["kb-shp-2"]),
    ("kb-shp-7", "shipping", "company_doc", "medium", "Fleet utilization quarterly review pack", ["utilization", "KPI"], "Internal benchmark: fleet utilization 78% vs. top-quartile 89%; laden/ballast ratio and idle-day breakdown by trade lane.", []),
    # Healthcare
    ("kb-hlt-1", "healthcare", "incident", "critical", "ED overcrowding: door-to-doctor time exceeding 90 minutes", ["ED", "patient flow"], "Emergency department boarding due to inpatient bed blocking; LWBS (left-without-being-seen) rate rose to 7.2%.", ["kb-hlt-2"]),
    ("kb-hlt-2", "healthcare", "best_practice", "medium", "Discharge-before-noon program to unlock inpatient beds", ["discharge", "beds"], "Structured morning discharge huddles raised before-noon discharges from 12% to 34%, easing ED boarding within 8 weeks.", ["kb-hlt-1"]),
    ("kb-hlt-3", "healthcare", "regulation", "high", "HIPAA breach exposure from unsecured messaging between staff", ["HIPAA", "privacy"], "Audit found PHI shared over consumer messaging apps; requires sanctioned secure-messaging rollout and policy enforcement.", []),
    ("kb-hlt-4", "healthcare", "sop", "medium", "Nurse rostering SOP with acuity-based staffing ratios", ["staffing", "rostering"], "Acuity-adjusted ratios with float-pool escalation; reduces unplanned overtime 22% while protecting care quality.", []),
    ("kb-hlt-5", "healthcare", "incident", "high", "OR turnover time 48 min vs. 25 min benchmark", ["OR", "throughput"], "Parallel processing (induction rooms, standardized tray kits) documented to recover 1–2 cases/day per theatre.", []),
    ("kb-hlt-6", "healthcare", "manual", "low", "Revenue cycle denials management manual", ["billing", "denials"], "Playbook for denial root-cause coding, resubmission SLAs and payer scorecards; typical recovery 3–5% of net revenue.", []),
    ("kb-hlt-7", "healthcare", "company_doc", "medium", "Quarterly patient experience (HCAHPS) review", ["HCAHPS", "experience"], "Internal scores trail state average on responsiveness and discharge information domains; improvement levers listed.", ["kb-hlt-2"]),
    # Manufacturing
    ("kb-mfg-1", "manufacturing", "incident", "critical", "Line 3 unplanned downtime at 14% — chronic bearing failures", ["downtime", "maintenance"], "Vibration analysis shows lubrication-interval drift; downtime cost ≈ $18k/hour. Candidate for predictive maintenance pilot.", ["kb-mfg-2"]),
    ("kb-mfg-2", "manufacturing", "best_practice", "medium", "Predictive maintenance (IIoT vibration + thermal) rollout pattern", ["PdM", "IIoT"], "Sensor retrofit + threshold alarms cut unplanned downtime 35–50% within two quarters in comparable plants.", ["kb-mfg-1"]),
    ("kb-mfg-3", "manufacturing", "regulation", "high", "OSHA lockout/tagout citations risk on legacy presses", ["OSHA", "LOTO", "safety"], "Audit gap: energy-isolation procedures missing on 6 presses; citation exposure plus injury risk during changeovers.", []),
    ("kb-mfg-4", "manufacturing", "sop", "medium", "SMED changeover SOP for stamping lines", ["SMED", "changeover"], "Single-minute-exchange-of-die staging: externalize setup steps, parallel tasks; documented changeover reduction 45→18 min.", []),
    ("kb-mfg-5", "manufacturing", "incident", "high", "Supplier quality escapes driving 2.8% line rejection", ["quality", "supplier"], "Incoming inspection sampling missed dimensional drift from two casting suppliers; PPAP re-qualification required.", []),
    ("kb-mfg-6", "manufacturing", "manual", "low", "TPM autonomous maintenance operator manual", ["TPM", "operators"], "Operator-led cleaning/inspection/lubrication standards with visual controls; foundation for OEE improvement.", ["kb-mfg-2"]),
    ("kb-mfg-7", "manufacturing", "company_doc", "medium", "Plant OEE monthly scorecard", ["OEE", "KPI"], "OEE 61% vs. 75% target: availability is the dominant loss (14pp), then performance (7pp), quality (3pp).", ["kb-mfg-1"]),
    # Logistics / Supply chain
    ("kb-log-1", "logistics", "incident", "critical", "Last-mile on-time delivery dropped to 87% in metro zones", ["last-mile", "OTD"], "Routing density decay plus driver churn; failed-first-attempt rate 11%. Customer complaints up 2.4× in affected zones.", ["kb-log-2"]),
    ("kb-log-2", "logistics", "best_practice", "medium", "Dynamic route optimization with time-window clustering", ["routing", "optimization"], "Zone-based dynamic routing recovered 6–9pp of on-time performance and cut cost-per-drop 14% in similar networks.", ["kb-log-1"]),
    ("kb-log-3", "logistics", "regulation", "high", "Driver hours-of-service compliance gaps in long-haul fleet", ["HOS", "compliance"], "Telematics audit shows 4% of trips breach duty-hour limits; fines plus insurance escalation risk.", []),
    ("kb-log-4", "logistics", "sop", "medium", "Warehouse wave-picking SOP with slotting refresh", ["warehouse", "picking"], "ABC re-slotting every 8 weeks plus wave-picking windows; pick rate improvement 18% documented.", []),
    ("kb-log-5", "logistics", "incident", "high", "Stockouts on A-class SKUs despite high aggregate inventory", ["inventory", "stockout"], "Inventory skewed to slow movers; service-level-driven safety stock policy needed instead of uniform weeks-of-cover.", []),
    ("kb-log-6", "logistics", "manual", "low", "Freight procurement RFQ and lane benchmarking manual", ["freight", "procurement"], "Annual RFQ calendar, lane rate benchmarks and mini-bid triggers when spot spread exceeds 8%.", []),
    ("kb-log-7", "logistics", "company_doc", "medium", "Network design review: DC footprint vs. demand map", ["network", "DC"], "Two-DC footprint leaves 28% of demand beyond next-day reach; candidate micro-fulfillment sites listed.", ["kb-log-1"]),
]


def _content_md(title: str, industry: str, summary: str) -> str:
    return (
        f"## {title}\n\n{summary}\n\n"
        "**Key points**\n\n"
        f"- Documented pattern observed across multiple operations in the {industry} knowledge base.\n"
        "- Includes root-cause notes, affected KPIs and the mitigation playbook applied historically.\n"
        "- Referenced by the analysis engine when similar context signals are detected.\n\n"
        "**Recommended playbook**\n\n"
        "1. Quantify current impact against baseline KPIs.\n"
        "2. Apply the documented countermeasures and assign a single owner.\n"
        "3. Review outcome after 4–6 weeks and update this entry.\n"
    )


async def main() -> None:
    for slug, industry, source_type, severity, title, tags, summary, related in SEED_ISSUES:
        await kb.create_issue(
            issue_id=_sid(slug),
            title=title,
            industry=industry,
            source_type=source_type,
            severity=severity,
            summary=summary,
            content_md=_content_md(title, industry, summary),
            tags=tags,
            related_issue_ids=[_sid(r) for r in related],
        )
        print(f"seeded {slug}: {title}")
    print(f"\nDone — {len(SEED_ISSUES)} knowledge base issues seeded and embedded.")


if __name__ == "__main__":
    asyncio.run(main())
