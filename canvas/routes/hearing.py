from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from canvas import hearing as h

router = APIRouter(prefix="/hearing")


def _session_json(s: h.HearingSession) -> dict:
    return {
        "id": s.id,
        "case_number": s.case_number,
        "motion": s.motion,
        "position_a": s.position_a,
        "position_b": s.position_b,
        "slug": s.slug,
        "topic": s.topic,
        "status": s.status,
        "ruling": s.ruling,
        "active": s.active,
        "evidence_files": list(s.evidence.keys()),
        "turns": [
            {
                "speaker": t.speaker,
                "content": t.content,
                "timestamp": t.timestamp.isoformat(),
            }
            for t in s.turns
        ],
    }


@router.get("/list")
async def list_hearings():
    return JSONResponse({"hearings": [_session_json(s) for s in h.list_sessions()]})


@router.post("/new")
async def new_hearing(payload: dict, background_tasks: BackgroundTasks):
    motion    = (payload.get("motion")     or "").strip()
    position_a = (payload.get("position_a") or "").strip()
    position_b = (payload.get("position_b") or "").strip()
    slug      = (payload.get("slug")       or "").strip()
    topic     = (payload.get("topic")      or "general").strip()
    if not motion or not position_a or not position_b:
        return JSONResponse({"error": "motion, position_a, and position_b are required"}, status_code=400)
    session = h.create_session(motion, position_a, position_b, slug, topic)
    # Kick off evidence orchestrator as a background task
    background_tasks.add_task(h._gather_evidence_background, session.id)
    return JSONResponse(_session_json(session))


@router.get("/{sid}")
async def get_hearing(sid: str):
    s = h.get_session(sid)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_session_json(s))


@router.post("/{sid}/argue/{attorney}")
async def argue(sid: str, attorney: str, payload: dict, background_tasks: BackgroundTasks):
    s = h.get_session(sid)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    if attorney not in ("attorney-a", "attorney-b"):
        return JSONResponse({"error": "attorney must be attorney-a or attorney-b"}, status_code=400)
    if s.status == "gathering_evidence":
        return JSONResponse({"error": "evidence orchestrator still running — wait for courtroom to open"}, status_code=409)
    if s.active:
        return JSONResponse({"error": f"{s.active} is already arguing"}, status_code=409)
    if s.status == "ruled":
        return JSONResponse({"error": "hearing is closed"}, status_code=409)
    directive = (payload.get("directive") or "").strip()
    s.active = attorney
    background_tasks.add_task(h.run_attorney_turn, sid, attorney, directive)
    return JSONResponse({"ok": True, "active": attorney})


@router.post("/{sid}/justice")
async def justice_speaks(sid: str, payload: dict):
    s = h.get_session(sid)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    content = (payload.get("content") or "").strip()
    if not content:
        return JSONResponse({"error": "content required"}, status_code=400)
    h.add_justice_turn(sid, content)
    return JSONResponse({"ok": True})


@router.post("/{sid}/rule")
async def rule(sid: str, payload: dict):
    s = h.get_session(sid)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    ruling = (payload.get("ruling") or "").strip()
    if not ruling:
        return JSONResponse({"error": "ruling required"}, status_code=400)
    h.issue_ruling(sid, ruling)
    return JSONResponse({"ok": True})
