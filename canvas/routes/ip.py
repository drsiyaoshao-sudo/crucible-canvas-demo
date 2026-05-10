from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from canvas import forge_reader

router = APIRouter()
templates = Jinja2Templates(directory="canvas/templates")


@router.get("/ip/{slug}", response_class=HTMLResponse)
async def ip(request: Request, slug: str):
    candidates = forge_reader.get_patent_candidates(slug)
    return templates.TemplateResponse("ip.html", {
        "request": request,
        "slug": slug,
        "candidates": candidates,
    })
