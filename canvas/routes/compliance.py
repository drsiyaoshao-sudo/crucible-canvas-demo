from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from canvas import forge_reader

router = APIRouter()
templates = Jinja2Templates(directory="canvas/templates")


@router.get("/compliance/{slug}", response_class=HTMLResponse)
async def compliance(request: Request, slug: str):
    prescreen = forge_reader.get_compliance_prescreen(slug)
    matrix = forge_reader.get_compliance_matrix(slug)
    calendar = forge_reader.get_regulatory_calendar(slug)

    return templates.TemplateResponse("compliance.html", {
        "request": request,
        "slug": slug,
        "prescreen": prescreen,
        "matrix": matrix,
        "calendar": calendar,
    })
