"""
Judicial Hearing session management for Crucible-Canvas.

Models the forge judicial process: attorney-A and attorney-B argue assigned positions
from DERIVED-OK evidence; the human Justice directs and rules.

Evidence is gathered by an orchestrator agent using tool-use (list_forge_files /
read_forge_file), mirroring the Glob/Read tools the attorneys have in forge.
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
from typing import Dict, List, Optional

FORGE_PATH = Path(os.getenv("FORGE_PATH", "/Users/siyaoshao/crucible-forge"))

# Paths the orchestrator may never read (PRIVATE tier, per judicial.md)
_BLOCKED_PREFIXES = [
    "products/",  # device_context and test_reports live here — filtered below
    "docs/patent/fto_reports/",
    "docs/compliance/drafts/",
]
_BLOCKED_SUFFIXES = ["device_context.md"]
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
    active: str = ""    # which attorney is computing right now
    evidence: Dict[str, str] = field(default_factory=dict)
    case_number: int = 0
    created_at: datetime = field(default_factory=datetime.now)


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
# Evidence orchestrator — uses tool-use to gather evidence like attorneys do
# ---------------------------------------------------------------------------

_ORCHESTRATOR_TOOLS = [
    {
        "name": "list_forge_files",
        "description": (
            "List files in the forge repository matching a glob pattern. "
            "Use this to discover what evidence files exist before reading them."
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
            "Read a DERIVED-OK or PUBLIC file from the forge repository. "
            "Never read: device_context.md, test_reports/, fto_reports/, compliance/drafts/."
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
]

_ORCHESTRATOR_SYSTEM = """You are the Evidence Orchestrator for a Crucible-Forge Judicial Hearing.

Your sole task: gather all relevant DERIVED-OK and PUBLIC evidence for the hearing so
the attorneys can argue from it.

ALLOWED to read (DERIVED-OK / PUBLIC):
  products/<slug>/bom.csv, firmware_manifest.json, feature_abstract.md, import_record.md
  docs/company_context.md
  docs/governance/amendments.md, case_law.md
  docs/compliance/matrix/<slug>/*.md
  docs/candidates/<slug>/compliance_prescreen.md
  docs/patent/disclosures/<slug>/candidates_suggestion_*.md

NEVER read (PRIVATE / attorney-client):
  products/*/device_context.md
  products/*/test_reports/**
  docs/patent/fto_reports/**
  docs/compliance/drafts/**

Strategy:
1. list_forge_files to discover what exists
2. read_forge_file for each relevant file
3. When you have gathered sufficient evidence, stop tool calls and reply with exactly:
   EVIDENCE_COMPLETE

Be systematic. Prioritise topic-relevant files. Do not read the same file twice."""


def _execute_orchestrator_tool(name: str, inp: dict) -> str:
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
    return "[unknown tool]"


def _orchestrate_evidence_sync(slug: str, topic: str, motion: str) -> tuple[Dict[str, str], str]:
    """
    Run the evidence-orchestrator agentic loop (blocking, call via executor).
    Returns (evidence_dict, log_text).
    """
    import anthropic
    client = anthropic.Anthropic(api_key=_get_api_key())

    user_msg = (
        f"Gather evidence for this judicial hearing.\n"
        f"Motion: {motion}\n"
        f"Product slug: {slug}\n"
        f"Topic: {topic}\n\n"
        f"Start by listing available files for slug '{slug}', then read the most relevant ones. "
        f"When done, reply EVIDENCE_COMPLETE."
    )

    messages = [{"role": "user", "content": user_msg}]
    evidence: Dict[str, str] = {}
    log_lines: List[str] = ["[Orchestrator] Starting evidence collection…"]

    for _ in range(12):  # max tool-call rounds
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_ORCHESTRATOR_SYSTEM,
            tools=_ORCHESTRATOR_TOOLS,
            messages=messages,
        )

        # Collect text blocks for the log
        for block in response.content:
            if hasattr(block, "text") and block.text.strip():
                log_lines.append(f"[Orchestrator] {block.text.strip()[:200]}")

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = _execute_orchestrator_tool(block.name, block.input)
                    if block.name == "read_forge_file":
                        rel = block.input.get("path", "")
                        if result and result not in ("[file not found]", "[BLOCKED — PRIVATE path]"):
                            evidence[rel] = result
                            log_lines.append(f"[Orchestrator] read → {rel} ({len(result)} chars)")
                        elif result == "[BLOCKED — PRIVATE path]":
                            log_lines.append(f"[Orchestrator] BLOCKED → {rel}")
                    elif block.name == "list_forge_files":
                        files = json.loads(result) if result.startswith("[") else []
                        log_lines.append(f"[Orchestrator] list → {len(files)} file(s) found")
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
    return evidence, "\n".join(log_lines)


async def _gather_evidence_background(sid: str) -> None:
    """Background task: run orchestrator, update session, open courtroom."""
    session = HEARINGS.get(sid)
    if not session:
        return
    loop = asyncio.get_event_loop()
    try:
        evidence, log = await loop.run_in_executor(
            None, _orchestrate_evidence_sync, session.slug, session.topic, session.motion
        )
        session.evidence = evidence
        session.turns.append(HearingTurn(
            speaker="orchestrator",
            content=log,
        ))
        session.turns.append(HearingTurn(
            speaker="clerk",
            content=(
                f"Evidence gathering complete. {len(evidence)} file(s) loaded.\n"
                + "\n".join(f"  • {p}" for p in evidence.keys())
                + "\n\nCourtroom open. Justice may now direct attorneys."
            ),
        ))
    except Exception as exc:
        session.turns.append(HearingTurn(
            speaker="clerk",
            content=f"[Evidence orchestrator error: {exc}]\nProceeding with no pre-loaded evidence.",
        ))
    finally:
        session.status = "open"


# ---------------------------------------------------------------------------
# Attorney argument
# ---------------------------------------------------------------------------

async def attorney_argue(session: HearingSession, attorney: str, directive: str = "") -> str:
    agent_name = "attorney-A" if attorney == "attorney-a" else "attorney-B"
    system = _load_agent_system(agent_name)
    if not system:
        system = (
            f"You are {agent_name} in a Crucible-Forge Judicial Hearing. "
            "Argue your assigned position using the required 4-element structure: "
            "1-Amendment, 2-Precedent, 3-Outcome protected, 4-Consequences of opposing view."
        )

    my_position = session.position_a if attorney == "attorney-a" else session.position_b
    opposing   = session.position_b if attorney == "attorney-a" else session.position_a

    transcript = "\n\n".join(
        f"[{t.speaker.upper()}]\n{t.content}"
        for t in session.turns
        if t.speaker not in ("orchestrator",)
    ) or "(No prior turns — you open the hearing.)"

    evidence_block = "\n\n".join(
        f"### {path}\n```\n{content[:3000]}\n```"
        for path, content in session.evidence.items()
    ) or "(No evidence files loaded — argue from first principles.)"

    directive_line = (
        f"\nJUSTICE DIRECTIVE TO YOU: {directive}"
        if directive else
        "\nNo specific directive — make your argument following the required 4-element structure."
    )

    user = (
        f"JUDICIAL HEARING: {session.motion}\n"
        f"Product slug: {session.slug}  |  Topic: {session.topic}\n"
        f"Your assigned position: {my_position}\n"
        f"Opposing position: {opposing}\n\n"
        f"EVIDENCE (DERIVED-OK and PUBLIC tier only):\n{evidence_block}\n\n"
        f"HEARING TRANSCRIPT SO FAR:\n{transcript}\n"
        f"{directive_line}\n\n"
        "Keep your argument under 300 words. Cite specific file names and passages."
    )

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call_claude, system, user)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def create_session(motion: str, position_a: str, position_b: str,
                   slug: str, topic: str) -> HearingSession:
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
    )
    session.turns.append(HearingTurn(
        speaker="clerk",
        content=(
            f"Courtroom initialised. Case #{_case_counter}: {motion}\n"
            f"Attorney-A → {position_a}\n"
            f"Attorney-B → {position_b}\n"
            f"Evidence orchestrator dispatched — gathering DERIVED-OK files…"
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
    session.active = attorney
    try:
        text = await attorney_argue(session, attorney, directive)
        session.turns.append(HearingTurn(speaker=attorney, content=text))
    except Exception as exc:
        session.turns.append(HearingTurn(speaker=attorney, content=f"[ERROR: {exc}]"))
    finally:
        session.active = ""
    return session


def add_justice_turn(sid: str, content: str) -> HearingSession:
    session = HEARINGS[sid]
    session.turns.append(HearingTurn(speaker="justice", content=content))
    return session


def issue_ruling(sid: str, ruling: str) -> HearingSession:
    session = HEARINGS[sid]
    session.ruling = ruling
    session.status = "ruled"
    session.turns.append(HearingTurn(speaker="justice", content=f"RULING:\n{ruling}"))
    _append_case_law(session)
    return session


def _append_case_law(session: HearingSession) -> None:
    path = FORGE_PATH / "docs/governance/case_law.md"
    if not path.is_file():
        return
    existing = path.read_text(encoding="utf-8")
    entry = (
        f"\n### Case {session.case_number} — {session.motion}\n"
        f"Date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"Attorneys: attorney-A (position: {session.position_a}) "
        f"vs attorney-B (position: {session.position_b})\n"
        f"Product: {session.slug}  |  Topic: {session.topic}\n"
        f"Ruling: {session.ruling}\n"
        f"Constitutional basis: Article II — Human in the Loop\n"
        f"Status: ACTIVE\n"
    )
    sentinel = "*No cases recorded yet.*"
    updated = existing.replace(sentinel, entry.strip()) if sentinel in existing \
        else existing.rstrip() + "\n" + entry
    path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Clear — wipe all in-memory state and optionally delete generated forge docs
# ---------------------------------------------------------------------------

def clear_all(purge_generated_docs: bool = False) -> List[str]:
    """
    Clear HEARINGS, reset case counter. If purge_generated_docs, delete
    all suggestion/prescreen files written by forge agents (DERIVED-OK generated).
    Returns list of deleted file paths (relative to FORGE_PATH).
    """
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
