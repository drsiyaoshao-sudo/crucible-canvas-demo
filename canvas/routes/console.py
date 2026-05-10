from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from canvas import forge_reader

templates = Jinja2Templates(directory="canvas/templates")
router = APIRouter()


@router.get("/console", response_class=HTMLResponse)
async def console_page(request: Request):
    slugs = forge_reader.list_product_slugs()
    return templates.TemplateResponse("console.html", {
        "request": request,
        "slugs": slugs,
    })
