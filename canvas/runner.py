"""
Background job runner for canvas.
LLM jobs use the Anthropic SDK directly — no subprocess file-write permission issues.
Compliance screen runs the forge Python screener (no LLM).
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FORGE_PATH = Path(os.getenv("FORGE_PATH", "/Users/siyaoshao/crucible-forge"))
PYTHON_BIN = "python3"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    id: str
    job_type: str
    slug: str
    status: JobStatus = JobStatus.QUEUED
    output: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None


JOBS: Dict[str, Job] = {}


# ---------------------------------------------------------------------------
# Forge file helpers
# ---------------------------------------------------------------------------

def _read_forge(rel: str) -> str:
    p = FORGE_PATH / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _load_agent_system_prompt(agent_name: str) -> str:
    """Load agent .md, strip YAML frontmatter, return body as system prompt."""
    text = _read_forge(f".claude/agents/{agent_name}.md")
    if text.startswith("---"):
        parts = text.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else text
    return text


def _forge_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    env_file = FORGE_PATH / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _get_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY") or _forge_env().get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not found in env or forge .env")
    return key


# ---------------------------------------------------------------------------
# Compliance screen — pure Python, no LLM
# ---------------------------------------------------------------------------

async def _run_compliance_screen(slug: str) -> str:
    forge_str = str(FORGE_PATH)
    env_path = str(FORGE_PATH / ".env")
    code = (
        f"import sys, os\n"
        f"sys.path.insert(0, {repr(forge_str)})\n"
        f"for line in open({repr(env_path)}).read().splitlines():\n"
        f"    line = line.strip()\n"
        f"    if line and not line.startswith('#') and '=' in line:\n"
        f"        k, v = line.split('=', 1); os.environ[k.strip()] = v.strip()\n"
        f"from forge.compliance.screener import screen\n"
        f"r = screen({repr(slug)})\n"
        f"print('Domains:', ', '.join(r.domains_detected))\n"
        f"print('Standards:', len(r.standards))\n"
        f"for s in r.standards:\n"
        f"    print(f'  [P{{s.priority}}] {{s.standard_id}}')\n"
    )
    proc = await asyncio.create_subprocess_exec(
        PYTHON_BIN, "-c", code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(FORGE_PATH),
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[:1000])
    return stdout.decode(errors="replace")


# ---------------------------------------------------------------------------
# LLM jobs — Anthropic SDK, write files ourselves
# ---------------------------------------------------------------------------

def _call_claude(system: str, user: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=_get_api_key())
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


async def _run_novelty(slug: str) -> str:
    system = _load_agent_system_prompt("novelty-scout")

    files: List[Tuple[str, str]] = [
        ("docs/company_context.md",                     _read_forge("docs/company_context.md")),
        (f"products/{slug}/feature_abstract.md",        _read_forge(f"products/{slug}/feature_abstract.md")),
        (f"products/{slug}/bom.csv",                    _read_forge(f"products/{slug}/bom.csv")),
        (f"products/{slug}/firmware_manifest.json",     _read_forge(f"products/{slug}/firmware_manifest.json")),
    ]

    file_block = "\n\n".join(
        f"### {path}\n```\n{content}\n```" for path, content in files if content
    )

    today = date.today().isoformat()
    out_path = f"docs/patent/disclosures/{slug}/candidates_suggestion_{today}.md"

    user = f"""Run a patent novelty scan for product slug '{slug}' in **suggestion mode** (FORGE_IP_MODE=suggestion).

The forge files you need are provided below. Do NOT read device_context.md — this is cloud/suggestion mode only.

{file_block}

Apply all four novelty heuristics from your agent definition:
1. Uncommon combination
2. Unexpected result
3. Non-standard method
4. Improvement with traceable cause

Surface at least 2–4 candidates. Follow your output format exactly.

Output the **complete content** of the file to write at `{out_path}`.
Begin your response with the SUGGESTION ONLY banner (exactly as specified in your agent definition).
Return ONLY the file content — no preamble, no explanation after."""

    loop = asyncio.get_event_loop()
    content = await loop.run_in_executor(None, _call_claude, system, user)

    out_file = FORGE_PATH / out_path
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(content, encoding="utf-8")

    candidate_count = len(re.findall(r"^## Candidate", content, re.MULTILINE))
    return f"Written: {out_path}\nCandidates found: {candidate_count}"


async def _run_compliance_map(slug: str) -> str:
    system = _load_agent_system_prompt("compliance-mapper")

    files: List[Tuple[str, str]] = [
        ("docs/company_context.md",                           _read_forge("docs/company_context.md")),
        (f"docs/candidates/{slug}/compliance_prescreen.md",   _read_forge(f"docs/candidates/{slug}/compliance_prescreen.md")),
        (f"products/{slug}/feature_abstract.md",              _read_forge(f"products/{slug}/feature_abstract.md")),
        (f"products/{slug}/bom.csv",                          _read_forge(f"products/{slug}/bom.csv")),
        (f"products/{slug}/firmware_manifest.json",           _read_forge(f"products/{slug}/firmware_manifest.json")),
    ]

    missing = [path for path, content in files if not content]
    if f"docs/candidates/{slug}/compliance_prescreen.md" in missing:
        raise RuntimeError("compliance_prescreen.md not found — run Compliance Screen first")

    file_block = "\n\n".join(
        f"### {path}\n```\n{content}\n```" for path, content in files if content
    )

    today = date.today().isoformat()
    out_path = f"docs/compliance/matrix/{slug}/prescreen_matrix_{today}.md"

    user = f"""Map product '{slug}' compliance against all applicable standards in **suggestion mode** (FORGE_IP_MODE=suggestion).

The forge files you need are provided below.

{file_block}

Use result labels: LIKELY_PASS, LIKELY_CONDITIONAL, NOT_TESTED, N/A.
Do NOT use PASS, FAIL, or COMPLIANT — those are reserved for formal test-evidence matrices.
Set "Overall gap status: PRE-SCREEN-COMPLETE".

Follow your output format exactly — include the clause-by-clause matrix table.

Output the **complete content** of the file to write at `{out_path}`.
Begin your response with the SUGGESTION ONLY banner (exactly as specified in your agent definition).
Return ONLY the file content — no preamble, no explanation after."""

    loop = asyncio.get_event_loop()
    content = await loop.run_in_executor(None, _call_claude, system, user)

    out_file = FORGE_PATH / out_path
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(content, encoding="utf-8")

    return f"Written: {out_path}\nGap status: PRE-SCREEN-COMPLETE"


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------

async def _run_job(job: Job) -> None:
    job.status = JobStatus.RUNNING
    try:
        if job.job_type == "compliance_screen":
            job.output = await _run_compliance_screen(job.slug)
        elif job.job_type == "novelty":
            job.output = await _run_novelty(job.slug)
        elif job.job_type == "compliance_map":
            job.output = await _run_compliance_map(job.slug)
        else:
            raise ValueError(f"Unknown job type: {job.job_type}")
        job.status = JobStatus.DONE
    except Exception as exc:
        job.output = str(exc)
        job.status = JobStatus.ERROR
    finally:
        job.finished_at = datetime.now()


async def launch_job(job_type: str, slug: str) -> Job:
    job = Job(id=str(uuid.uuid4())[:8], job_type=job_type, slug=slug)
    JOBS[job.id] = job
    asyncio.create_task(_run_job(job))
    return job


def get_job(job_id: str) -> Optional[Job]:
    return JOBS.get(job_id)
