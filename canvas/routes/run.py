from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from canvas import runner

router = APIRouter()

VALID_JOB_TYPES = {"novelty", "compliance_screen", "compliance_map"}


@router.post("/run/{job_type}/{slug}")
async def start_job(job_type: str, slug: str):
    if job_type not in VALID_JOB_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown job type: {job_type}")
    job = await runner.launch_job(job_type, slug)
    return JSONResponse({"job_id": job.id, "status": job.status, "job_type": job_type, "slug": slug})


@router.get("/api/job/{job_id}")
async def get_job_status(job_id: str):
    job = runner.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse({
        "job_id": job.id,
        "job_type": job.job_type,
        "slug": job.slug,
        "status": job.status,
        "output": job.output[-2000:] if job.output else "",
        "started_at": job.started_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    })
