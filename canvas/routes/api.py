import json
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from canvas import forge_reader, runner, hearing

router = APIRouter(prefix="/api")


@router.get("/status")
async def api_status():
    ctx = forge_reader.get_company_context()
    slugs = forge_reader.list_product_slugs()
    return JSONResponse({
        "company": ctx["company_name"],
        "product_count": len(slugs),
        "slugs": slugs,
    })


@router.get("/product/{slug}")
async def api_product(slug: str):
    record = forge_reader.get_import_record(slug)
    candidates = forge_reader.get_patent_candidates(slug)
    gap_status = forge_reader.get_compliance_gap_status(slug)
    matrix = forge_reader.get_compliance_matrix(slug)
    return JSONResponse({
        "slug": slug,
        "version": record["version"],
        "import_date": record["import_date"],
        "phase": record["phase"],
        "candidate_count": len(candidates),
        "gap_status": gap_status,
        "has_matrix": matrix["found"],
    })


@router.get("/jobs")
async def api_jobs():
    jobs = [
        {
            "job_id": j.id,
            "job_type": j.job_type,
            "slug": j.slug,
            "status": j.status,
            "started_at": j.started_at.isoformat(),
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            "output_snippet": (j.output or "")[-300:],
        }
        for j in sorted(runner.JOBS.values(), key=lambda x: x.started_at, reverse=True)
    ]
    return JSONResponse({"jobs": jobs})


@router.post("/clear")
async def api_clear(payload: dict = None):
    """
    Clear all in-memory hearing sessions and forge jobs.
    Pass {"purge_docs": true} to also delete generated suggestion/prescreen files from forge.
    """
    purge = bool((payload or {}).get("purge_docs", False))
    deleted_files = hearing.clear_all(purge_generated_docs=purge)
    runner.JOBS.clear()
    return JSONResponse({
        "ok": True,
        "hearings_cleared": True,
        "jobs_cleared": True,
        "files_deleted": deleted_files,
    })


_COMMAND_MAP = {
    r"^novelty\s+(\S+)$":            lambda m: ("novelty",            m.group(1)),
    r"^compliance\s+screen\s+(\S+)$": lambda m: ("compliance_screen", m.group(1)),
    r"^compliance\s+map\s+(\S+)$":    lambda m: ("compliance_map",    m.group(1)),
}


@router.post("/command")
async def api_command(payload: dict):
    raw = (payload.get("cmd") or "").strip().lower()
    for pattern, resolver in _COMMAND_MAP.items():
        m = re.match(pattern, raw)
        if m:
            job_type, slug = resolver(m)
            job = await runner.launch_job(job_type, slug)
            return JSONResponse({"ok": True, "job_id": job.id, "job_type": job_type, "slug": slug})
    return JSONResponse(
        {"ok": False, "error": f"Unknown command: '{raw}'",
         "hint": "Try: novelty <slug> | compliance screen <slug> | compliance map <slug> | debate patent <slug> | debate compliance <slug>"},
        status_code=400,
    )


@router.get("/dashboard/refresh")
async def dashboard_refresh():
    """HTMX polling endpoint — returns lightweight product status JSON."""
    slugs = forge_reader.list_product_slugs()
    products = []
    for slug in slugs:
        candidates = forge_reader.get_patent_candidates(slug)
        gap_status = forge_reader.get_compliance_gap_status(slug)
        matrix = forge_reader.get_compliance_matrix(slug)
        products.append({
            "slug": slug,
            "candidate_count": len(candidates),
            "gap_status": gap_status,
            "has_matrix": matrix["found"],
        })
    return JSONResponse({"products": products})
