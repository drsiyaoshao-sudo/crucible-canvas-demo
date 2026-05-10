from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from canvas import forge_reader

router = APIRouter()
templates = Jinja2Templates(directory="canvas/templates")


@router.get("/graph", response_class=HTMLResponse)
async def graph(request: Request):
    slugs = forge_reader.list_product_slugs()
    km = forge_reader.get_enriched_knowledge_map(slugs)
    return templates.TemplateResponse("graph.html", {
        "request": request,
        "knowledge_map": km,
        "registered_slugs": slugs,
    })
