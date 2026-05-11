"""
Judicial Hearing session management for Crucible-Canvas.

Models the forge judicial process: attorney-A and attorney-B argue assigned positions
from DERIVED-OK evidence; the human Justice directs and rules.

Evidence is gathered by an orchestrator agent using tool-use (list_forge_files /
read_forge_file / declare_child_hearing), mirroring the Glob/Read tools the
attorneys have in forge.
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FORGE_PATH = Path(os.getenv("FORGE_PATH", "/Users/siyaoshao/crucible-forge"))

# Paths the orchestrator may never read (PRIVATE tier, per judicial.md)
_BLOCKED_SEGMENTS = ["test_reports", "fto_reports", "drafts", "PRIVATE"]


def _path_allowed(rel: str) -> bool:
    """True if rel is DERIVED-OK or PUBLIC (not PRIVATE)."""
    for seg in _BLOCKED_SEGMENTS:
        if f"/{seg}/" in f"/{rel}/":
            return False
    if rel.endswith("device_context.md"):
        return False
    return True


@dataclass
class HearingTurn:
    speaker: str   # "attorney-a" | "attorney-b" | "justice" | "clerk" | "orchestrator"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ChildHearingSpec:
    motion: str
    position_a: str
    position_b: str
    rationale: str


@dataclass
class HearingSession:
    id: str
    motion: str
    position_a: str
    position_b: str
    slug: str
    topic: str          # "patent" | "compliance" | "general"
    turns: List[HearingTurn] = field(default_factory=list)
    status: str = "gathering_evidence"   # "gathering_evidence" | "open" | "ruled"
    ruling: str = ""
    active: List[str] = field(default_factory=list)   # attorneys currently computing
    evidence: Dict[str, str] = field(default_factory=dict)
    case_number: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    parent_id: Optional[str] = None
    child_hearing_ids: List[str] = field(default_factory=list)
    clerk_spawned: bool = False


HEARINGS: Dict[str, HearingSession] = {}
_case_counter = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read(rel: str) -> str:
    p = FORGE_PATH / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _load_agent_system(name: str) -> str:
    text = _read(f".claude/agents/{name}.md")
    if text.startswith("---"):
        parts = text.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else text
    return text


def _get_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        env_file = FORGE_PATH / ".env"
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not found")
    return key


def _call_claude(system: str, user: str, model: str = "claude-sonnet-4-6",
                 max_tokens: int = 2048) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=_get_api_key())
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


# ---------------------------------------------------------------------------
# Clerk agent — explicitly spawned to announce proceedings
# ---------------------------------------------------------------------------

_CLERK_SYSTEM = """You are the Clerk of the Crucible-Forge Judicial Court.

Your role:
- Announce proceedings formally and maintain the official record
- Introduce evidence, call parties to order, and mark procedural milestones
- Speak in formal, precise court language
- Be concise: under 150 words per announcement

Do NOT argue any position. State facts about the case and proceedings only.
Format: lead with the procedural event in ALL CAPS, then the announcement body."""


def _call_clerk_sync(event: str, context: str) -> str:
    return _call_claude(
        _CLERK_SYSTEM,
        f"Procedural event: {event}\n\nContext:\n{context}",
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
    )


# ---------------------------------------------------------------------------
# Evidence orchestrator — tool-use loop to gather evidence
# ---------------------------------------------------------------------------

_ORCHESTRATOR_TOOLS = [
    {
        "name": "query_product_graph",
        "description": (
            "Read a specific section of the product's firmware_manifest.json knowledge graph. "
            "Returns structured JSON for that section rather than a raw text blob. "
            "Call this FIRST to understand the product's hardware, libraries, BLE spec, "
            "and any blocked toolchains before reading flat markdown files. "
            "Sections: board, softdevice, app, flash_method, libraries, host_tools, ble, "
            "blocked_toolchains, or 'all' for the full graph."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Product slug, e.g. 'gaitsense'"
                },
                "section": {
                    "type": "string",
                    "description": (
                        "Section key to retrieve: board | softdevice | app | flash_method | "
                        "libraries | host_tools | ble | blocked_toolchains | all"
                    )
                },
            },
            "required": ["slug", "section"],
        },
    },
    {
        "name": "list_forge_files",
        "description": (
            "List files in the forge repository matching a glob pattern. "
            "Use to discover patent candidates, compliance matrices, and other flat files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern relative to FORGE_PATH, e.g. 'products/gaitsense/*.csv'"
                }
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_forge_file",
        "description": (
            "Read a DERIVED-OK or PUBLIC flat file from the forge repository. "
            "Blocked: device_context.md, test_reports/, fto_reports/, compliance/drafts/."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to FORGE_PATH"
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "declare_child_hearing",
        "description": (
            "Declare a focused sub-session on a discrete contested technical point found in the evidence. "
            "Use when a specific prior art reference, claim element, or compliance clause "
            "warrants dedicated adversarial argument separate from the main motion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motion": {
                    "type": "string",
                    "description": "The specific contested technical question for the sub-session"
                },
                "position_a": {
                    "type": "string",
                    "description": "Examiner-A position"
                },
                "position_b": {
                    "type": "string",
                    "description": "Examiner-B position"
                },
                "rationale": {
                    "type": "string",
                    "description": "Why this sub-session is warranted based on the evidence"
                },
            },
            "required": ["motion", "position_a", "position_b", "rationale"],
        },
    },
]

_ORCHESTRATOR_SYSTEM = """You are the Evidence Orchestrator for a patent novelty and compliance review session.

Your role: assemble the technical evidence base that the examiners will debate. You do not argue.

STRATEGY — follow in order:
1. Call query_product_graph(slug, section="all") to load the product's hardware/firmware knowledge
   graph. This gives you exact component names, library versions, BLE spec, and blocked toolchains
   as structured data — use these specific facts to anchor the debate.
2. Call list_forge_files to discover patent candidates, compliance matrices, and other flat files.
3. Call read_forge_file for feature_abstract.md first (the core technical claim), then candidates
   and compliance files relevant to the topic.
4. If the evidence reveals a discrete contested technical sub-point (a specific prior art reference,
   a single claim element, a contested compliance clause), call declare_child_hearing for it.
5. End with a CASE STATEMENT.

ALLOWED PATHS (DERIVED-OK / PUBLIC):
  products/<slug>/bom.csv, firmware_manifest.json, feature_abstract.md, import_record.md
  docs/company_context.md
  docs/compliance/matrix/<slug>/*.md
  docs/candidates/<slug>/compliance_prescreen.md
  docs/patent/disclosures/<slug>/candidates_suggestion_*.md

BLOCKED (do not read):
  products/*/device_context.md
  products/*/test_reports/**
  docs/patent/fto_reports/**
  docs/compliance/drafts/**

For patent topic: use the board/library graph nodes as the technical substrate; read
  feature_abstract.md and candidates_suggestion_*.md to identify the claimed combination.
For compliance topic: use the graph's board/libraries to identify applicable standards;
  read compliance_prescreen.md and matrix files for clause-level gap status.

End with:
CASE STATEMENT
<technical summary: exact component names from graph, claimed algorithm, contested points>
END CASE STATEMENT

Do not read the same file twice."""


def _execute_orchestrator_tool(
    name: str, inp: dict, pending_children: List[ChildHearingSpec]
) -> str:
    if name == "query_product_graph":
        slug = inp.get("slug", "").strip()
        section = inp.get("section", "all").strip()
        manifest_path = FORGE_PATH / "products" / slug / "firmware_manifest.json"
        if not manifest_path.is_file():
            return json.dumps({"error": f"firmware_manifest.json not found for slug '{slug}'"})
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if section == "all":
            return json.dumps(manifest)
        if section in manifest:
            return json.dumps({section: manifest[section]})
        return json.dumps({"error": f"section '{section}' not found", "available": list(manifest.keys())})
    if name == "list_forge_files":
        pattern = inp.get("pattern", "")
        matches = sorted(glob.glob(str(FORGE_PATH / pattern)))
        rels = [str(Path(m).relative_to(FORGE_PATH)) for m in matches]
        allowed = [r for r in rels if _path_allowed(r)]
        return json.dumps(allowed) if allowed else "[]"
    if name == "read_forge_file":
        rel = inp.get("path", "")
        if not _path_allowed(rel):
            return "[BLOCKED — PRIVATE path]"
        content = _read(rel)
        return content[:4000] if content else "[file not found]"
    if name == "declare_child_hearing":
        spec = ChildHearingSpec(
            motion=inp.get("motion", ""),
            position_a=inp.get("position_a", ""),
            position_b=inp.get("position_b", ""),
            rationale=inp.get("rationale", ""),
        )
        pending_children.append(spec)
        return json.dumps({"status": "queued", "motion": spec.motion})
    return "[unknown tool]"


def _orchestrate_evidence_sync(
    slug: str, topic: str, motion: str,
    existing_paths: Optional[List[str]] = None,
    extra_query: str = "",
) -> Tuple[Dict[str, str], str, List[ChildHearingSpec]]:
    """
    Run the evidence-orchestrator agentic loop (blocking, call via executor).
    Returns (evidence_dict, log_text, child_hearing_specs).
    """
    import anthropic
    client = anthropic.Anthropic(api_key=_get_api_key())

    skip_note = ""
    if existing_paths:
        skip_note = (
            "\nAlready loaded (DO NOT re-read):\n"
            + "\n".join(f"  - {p}" for p in existing_paths)
            + "\n"
        )

    extra_note = f"\nSpecific additional request: {extra_query}" if extra_query else ""

    user_msg = (
        f"Gather evidence for this judicial hearing.\n"
        f"Motion: {motion}\n"
        f"Product slug: {slug}\n"
        f"Topic: {topic}\n"
        f"{skip_note}{extra_note}\n"
        f"Start by listing available files for slug '{slug}', then read the most relevant ones. "
        f"When done, produce a CASE STATEMENT."
    )

    messages = [{"role": "user", "content": user_msg}]
    evidence: Dict[str, str] = {}
    log_lines: List[str] = ["[Orchestrator] Starting evidence collection…"]
    pending_children: List[ChildHearingSpec] = []
    case_statement = ""

    for _ in range(14):  # max tool-call rounds
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_ORCHESTRATOR_SYSTEM,
            tools=_ORCHESTRATOR_TOOLS,
            messages=messages,
        )

        for block in response.content:
            if hasattr(block, "text") and block.text.strip():
                text = block.text.strip()
                log_lines.append(f"[Orchestrator] {text[:300]}")
                # Extract CASE STATEMENT if present
                if "CASE STATEMENT" in text and "END CASE STATEMENT" in text:
                    start = text.index("CASE STATEMENT") + len("CASE STATEMENT")
                    end = text.index("END CASE STATEMENT")
                    case_statement = text[start:end].strip()

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = _execute_orchestrator_tool(block.name, block.input, pending_children)
                    if block.name == "read_forge_file":
                        rel = block.input.get("path", "")
                        if result and result not in ("[file not found]", "[BLOCKED — PRIVATE path]"):
                            evidence[rel] = result
                            log_lines.append(f"[Orchestrator] read → {rel} ({len(result)} chars)")
                        elif result == "[BLOCKED — PRIVATE path]":
                            log_lines.append(f"[Orchestrator] BLOCKED → {rel}")
                    elif block.name == "query_product_graph":
                        section = block.input.get("section", "all")
                        log_lines.append(f"[Orchestrator] graph → {block.input.get('slug','')} [{section}] ({len(result)} chars)")
                        # Also store graph data as evidence so attorneys can reference it
                        graph_key = f"products/{block.input.get('slug','')}/firmware_manifest.json#{section}"
                        evidence[graph_key] = result
                    elif block.name == "list_forge_files":
                        files = json.loads(result) if result.startswith("[") else []
                        log_lines.append(f"[Orchestrator] list → {len(files)} file(s) found")
                    elif block.name == "declare_child_hearing":
                        parsed = json.loads(result)
                        log_lines.append(f"[Orchestrator] sub-session queued → {parsed.get('motion', '')[:80]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    log_lines.append(f"[Orchestrator] Done — {len(evidence)} file(s) collected.")
    if pending_children:
        log_lines.append(f"[Orchestrator] {len(pending_children)} child hearing(s) queued.")

    log_text = "\n".join(log_lines)
    if case_statement:
        log_text += f"\n\nCASE STATEMENT:\n{case_statement}"
    return evidence, log_text, pending_children


async def _gather_evidence_background(sid: str) -> None:
    """Background task: run orchestrator, spawn clerk, open courtroom."""
    session = HEARINGS.get(sid)
    if not session:
        return
    loop = asyncio.get_event_loop()
    try:
        evidence, log, child_specs = await loop.run_in_executor(
            None, _orchestrate_evidence_sync,
            session.slug, session.topic, session.motion, None, "",
        )
        session.evidence = evidence
        session.turns.append(HearingTurn(speaker="orchestrator", content=log))

        # Spawn the child hearings the orchestrator declared
        for spec in child_specs:
            child = create_session(
                motion=spec.motion,
                position_a=spec.position_a,
                position_b=spec.position_b,
                slug=session.slug,
                topic=session.topic,
                parent_id=sid,
            )
            session.child_hearing_ids.append(child.id)
            asyncio.ensure_future(_gather_evidence_background(child.id))

        # Explicitly spawn the clerk to announce courtroom opening
        session.clerk_spawned = False
        clerk_context = (
            f"Case #{session.case_number}: {session.motion}\n"
            f"Attorney-A → {session.position_a}\n"
            f"Attorney-B → {session.position_b}\n"
            f"Evidence gathered: {len(evidence)} file(s):\n"
            + "\n".join(f"  • {p}" for p in evidence.keys())
            + (f"\n\nChild hearings declared: {len(child_specs)}" if child_specs else "")
        )
        clerk_text = await loop.run_in_executor(
            None, _call_clerk_sync,
            "COURTROOM OPEN — EVIDENCE LOADED", clerk_context,
        )
        session.clerk_spawned = True
        session.turns.append(HearingTurn(speaker="clerk", content=clerk_text))

    except Exception as exc:
        session.turns.append(HearingTurn(
            speaker="clerk",
            content=f"[Clerk spawn error: {exc}]\nProceeding with no pre-loaded evidence.",
        ))
    finally:
        session.status = "open"


# ---------------------------------------------------------------------------
# Mid-hearing evidence gathering
# ---------------------------------------------------------------------------

async def gather_more_evidence(sid: str, query: str) -> None:
    """Background task: re-run orchestrator mid-hearing to find new documents."""
    session = HEARINGS.get(sid)
    if not session:
        return
    loop = asyncio.get_event_loop()
    try:
        existing_paths = list(session.evidence.keys())
        new_evidence, log, child_specs = await loop.run_in_executor(
            None, _orchestrate_evidence_sync,
            session.slug, session.topic, session.motion,
            existing_paths, query,
        )
        added = {k: v for k, v in new_evidence.items() if k not in session.evidence}
        session.evidence.update(added)
        session.turns.append(HearingTurn(speaker="orchestrator", content=log))

        for spec in child_specs:
            child = create_session(
                motion=spec.motion,
                position_a=spec.position_a,
                position_b=spec.position_b,
                slug=session.slug,
                topic=session.topic,
                parent_id=sid,
            )
            session.child_hearing_ids.append(child.id)
            asyncio.ensure_future(_gather_evidence_background(child.id))

        # Spawn clerk to announce new evidence
        clerk_context = (
            f"Case #{session.case_number}: {session.motion}\n"
            f"Query: {query or '(general)'}\n"
            f"New files loaded: {len(added)}\n"
            + "\n".join(f"  • {p}" for p in added.keys())
        )
        clerk_text = await loop.run_in_executor(
            None, _call_clerk_sync,
            "NEW EVIDENCE ADMITTED MID-HEARING", clerk_context,
        )
        session.turns.append(HearingTurn(speaker="clerk", content=clerk_text))

    except Exception as exc:
        session.turns.append(HearingTurn(
            speaker="clerk",
            content=f"[Mid-hearing evidence error: {exc}]",
        ))


# ---------------------------------------------------------------------------
# Attorney argument
# ---------------------------------------------------------------------------

_EXAMINER_SYSTEM = {
    "patent": """\
You are a technical patent examiner in a novelty review session.
You have been assigned a position — argue it fully using the product evidence.

Your argument must address:
1. The specific technical combination claimed (name exact components, algorithms, and data flows
   from the firmware graph and feature abstract)
2. Novelty: whether this combination is novel over cited prior art — cite specific prior art
   references by name if they appear in the candidate files
3. Non-obviousness: why a person skilled in the art would or would not have arrived at this
   combination from the prior art references alone
4. Technical scope: what the claim covers and what it does not

Be precise. Name the exact IC (e.g. LSM6DS3TR-C), the exact algorithm (e.g. push-off primary
trigger, ring-buffer retrospective heel-strike inference), the exact BLE data format. Vague
arguments about "novelty in general" are not acceptable.
Under 300 words. No governance language, no amendments, no constitutional citations.""",

    "compliance": """\
You are a regulatory compliance analyst in a gap review session.
You have been assigned a position — argue it fully using the compliance evidence.

Your argument must address:
1. The specific standard and clause at issue (cite clause number verbatim)
2. Gap status from the matrix: LIKELY_PASS, LIKELY_CONDITIONAL, or NOT_TESTED — quote the row
3. What test or verification would resolve the gap (or confirm pass status)
4. Risk consequence: what happens to the product's market access if this gap is not closed

Name the exact standard (e.g. FCC Part 15B, IEC 60601-1-2, ETSI EN 300 328), the exact
clause, and the exact product hardware involved (board part number, radio stack version).
Under 300 words. No governance language, no amendments, no constitutional citations.""",

    "general": """\
You are a technical analyst in an adversarial evidence review session.
You have been assigned a position — argue it fully using the product evidence.

Ground every claim in specific technical facts: component names, algorithm descriptions,
standard clause numbers, library versions. Do not argue from authority or intuition.
Under 300 words. No governance language.""",
}


async def attorney_argue(session: HearingSession, attorney: str, directive: str = "") -> str:
    topic = session.topic if session.topic in _EXAMINER_SYSTEM else "general"
    label = "Examiner-A" if attorney == "attorney-a" else "Examiner-B"
    system = _EXAMINER_SYSTEM[topic]

    my_position = session.position_a if attorney == "attorney-a" else session.position_b
    opposing   = session.position_b if attorney == "attorney-a" else session.position_a

    # Snapshot the transcript at the moment this examiner starts — prevents
    # context contamination when both argue simultaneously.
    transcript = "\n\n".join(
        f"[{t.speaker.upper()}]\n{t.content}"
        for t in session.turns
        if t.speaker not in ("orchestrator",)
    ) or "(No prior turns — you open the session.)"

    # Separate graph evidence (structured JSON) from flat-file evidence (markdown/csv)
    # so examiners see them under distinct headings for cleaner reference.
    graph_items = {k: v for k, v in session.evidence.items() if "#" in k}
    flat_items  = {k: v for k, v in session.evidence.items() if "#" not in k}

    graph_block = "\n\n".join(
        f"### PRODUCT GRAPH — {path.split('#')[1].upper()}\n```json\n{content}\n```"
        for path, content in graph_items.items()
    )
    flat_block = "\n\n".join(
        f"### {path}\n```\n{content[:3000]}\n```"
        for path, content in flat_items.items()
    )
    evidence_block = "\n\n".join(filter(None, [graph_block, flat_block])) \
        or "(No evidence loaded — argue from first principles.)"

    directive_line = (
        f"\nCHAIR DIRECTIVE TO YOU: {directive}"
        if directive else ""
    )

    user = (
        f"REVIEW SESSION: {session.motion}\n"
        f"Product: {session.slug}  |  Topic: {topic}\n"
        f"Your assigned position: {my_position}\n"
        f"Opposing position: {opposing}\n\n"
        f"PRODUCT EVIDENCE:\n{evidence_block}\n\n"
        f"TRANSCRIPT SO FAR:\n{transcript}\n"
        f"{directive_line}\n\n"
        f"State your argument as {label}. Under 300 words. "
        "Cite specific component names, algorithm steps, clause numbers, or prior art references."
    )

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call_claude, system, user)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def create_session(motion: str, position_a: str, position_b: str,
                   slug: str, topic: str,
                   parent_id: Optional[str] = None) -> HearingSession:
    global _case_counter
    _case_counter += 1
    sid = str(uuid.uuid4())[:8]
    session = HearingSession(
        id=sid,
        motion=motion,
        position_a=position_a,
        position_b=position_b,
        slug=slug,
        topic=topic,
        case_number=_case_counter,
        status="gathering_evidence",
        parent_id=parent_id,
    )
    # Clerk spawn event logged immediately (static bootstrap — clerk speaks once open)
    session.turns.append(HearingTurn(
        speaker="clerk",
        content=(
            f"[Clerk spawning…] Session #{_case_counter}: {motion}\n"
            f"Examiner-A → {position_a}\n"
            f"Examiner-B → {position_b}\n"
            + (f"Sub-session of #{HEARINGS[parent_id].case_number if parent_id and parent_id in HEARINGS else parent_id}\n"
               if parent_id else "")
            + "Evidence orchestrator dispatched — loading product graph + DERIVED-OK files…"
        ),
    ))
    HEARINGS[sid] = session
    return session


def get_session(sid: str) -> Optional[HearingSession]:
    return HEARINGS.get(sid)


def list_sessions() -> List[HearingSession]:
    return sorted(HEARINGS.values(), key=lambda s: s.created_at, reverse=True)


async def run_attorney_turn(sid: str, attorney: str, directive: str = "") -> HearingSession:
    session = HEARINGS[sid]
    if attorney not in session.active:
        session.active.append(attorney)
    try:
        text = await attorney_argue(session, attorney, directive)
        session.turns.append(HearingTurn(speaker=attorney, content=text))
    except Exception as exc:
        session.turns.append(HearingTurn(speaker=attorney, content=f"[ERROR: {exc}]"))
    finally:
        if attorney in session.active:
            session.active.remove(attorney)
    return session


def add_justice_turn(sid: str, content: str) -> HearingSession:
    session = HEARINGS[sid]
    session.turns.append(HearingTurn(speaker="justice", content=content))
    return session


def _attorney_record_ruling_sync(session: HearingSession, attorney: str, ruling: str) -> str:
    """Prevailing examiner drafts the review record entry."""
    label = "Examiner-A" if attorney == "attorney-a" else "Examiner-B"
    system = (
        f"You are {label}, the prevailing examiner in a patent novelty / compliance review session. "
        "Draft a concise review record entry summarising the technical basis for the ruling. "
        "Focus on the specific technical facts (component names, algorithm steps, clause numbers) "
        "that determined the outcome. No governance language."
    )
    my_position = session.position_a if attorney == "attorney-a" else session.position_b
    evidence_summary = "\n".join(f"  • {p}" for p in session.evidence.keys()) or "  (none)"

    user = (
        f"You prevailed in Review Session #{session.case_number}.\n"
        f"Motion: {session.motion}\n"
        f"Your position (upheld): {my_position}\n"
        f"Chair's Ruling: {ruling}\n"
        f"Evidence on record:\n{evidence_summary}\n\n"
        "Draft a review record entry (under 200 words) capturing: "
        "the specific technical argument that prevailed, the key evidence cited, "
        "and what precedent this sets for future reviews of similar claims."
    )
    return _call_claude(system, user, max_tokens=400)


def _append_case_law(session: HearingSession, entry_body: str) -> None:
    path = FORGE_PATH / "docs/governance/case_law.md"
    if not path.is_file():
        return
    existing = path.read_text(encoding="utf-8")
    header = (
        f"\n### Case {session.case_number} — {session.motion}\n"
        f"Date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"Attorneys: attorney-A (position: {session.position_a}) "
        f"vs attorney-B (position: {session.position_b})\n"
        f"Product: {session.slug}  |  Topic: {session.topic}\n"
        f"Ruling: {session.ruling}\n"
        f"Constitutional basis: Article II — Human in the Loop\n"
        f"Status: ACTIVE\n\n"
        f"{entry_body}\n"
    )
    sentinel = "*No cases recorded yet.*"
    updated = existing.replace(sentinel, header.strip()) if sentinel in existing \
        else existing.rstrip() + "\n" + header
    path.write_text(updated, encoding="utf-8")


async def issue_ruling(sid: str, ruling: str,
                       prevailing_attorney: Optional[str] = None) -> HearingSession:
    session = HEARINGS[sid]
    session.ruling = ruling
    session.status = "ruled"
    session.turns.append(HearingTurn(speaker="justice", content=f"RULING:\n{ruling}"))

    loop = asyncio.get_event_loop()

    if prevailing_attorney in ("attorney-a", "attorney-b"):
        # Prevailing attorney drafts the case law entry
        try:
            entry_body = await loop.run_in_executor(
                None, _attorney_record_ruling_sync, session, prevailing_attorney, ruling
            )
            session.turns.append(HearingTurn(
                speaker=prevailing_attorney,
                content=f"[CASE LAW ENTRY DRAFTED]\n{entry_body}",
            ))
            _append_case_law(session, entry_body)
        except Exception as exc:
            _append_case_law(session, f"[Entry draft error: {exc}]\nRuling: {ruling}")
    else:
        # No prevailing party — record the ruling directly
        _append_case_law(session, f"Summary: {ruling}")

    # Spawn clerk to close the session
    try:
        clerk_context = (
            f"Case #{session.case_number}: {session.motion}\n"
            f"Ruling: {ruling}\n"
            + (f"Prevailing counsel: {prevailing_attorney}" if prevailing_attorney else "No prevailing party designated.")
        )
        clerk_text = await loop.run_in_executor(
            None, _call_clerk_sync,
            "HEARING CLOSED — RULING RECORDED TO CASE LAW", clerk_context,
        )
        session.turns.append(HearingTurn(speaker="clerk", content=clerk_text))
    except Exception:
        pass

    return session


# ---------------------------------------------------------------------------
# Clear — wipe all in-memory state and optionally delete generated forge docs
# ---------------------------------------------------------------------------

def clear_all(purge_generated_docs: bool = False) -> List[str]:
    global _case_counter
    HEARINGS.clear()
    _case_counter = 0

    deleted: List[str] = []
    if purge_generated_docs:
        patterns = [
            "docs/patent/disclosures/*/candidates_suggestion_*.md",
            "docs/compliance/matrix/*/prescreen_matrix_*.md",
            "docs/candidates/*/compliance_prescreen.md",
        ]
        for pattern in patterns:
            for p in glob.glob(str(FORGE_PATH / pattern)):
                Path(p).unlink(missing_ok=True)
                deleted.append(str(Path(p).relative_to(FORGE_PATH)))
    return deleted
