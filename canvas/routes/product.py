from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from canvas import forge_reader

router = APIRouter()
templates = Jinja2Templates(directory="canvas/templates")


@router.get("/product/{slug}", response_class=HTMLResponse)
async def product(request: Request, slug: str):
    record = forge_reader.get_import_record(slug)
    bom = forge_reader.get_bom(slug)
    firmware = forge_reader.get_firmware_manifest(slug)
    abstract = forge_reader.get_feature_abstract(slug)
    candidates = forge_reader.get_patent_candidates(slug)

    return templates.TemplateResponse("product.html", {
        "request": request,
        "slug": slug,
        "record": record,
        "bom": bom,
        "firmware": firmware,
        "abstract": abstract,
        "candidates": candidates,
    })
