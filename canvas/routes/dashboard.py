from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from canvas import forge_reader

router = APIRouter()
templates = Jinja2Templates(directory="canvas/templates")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    ctx = forge_reader.get_company_context()
    slugs = forge_reader.list_product_slugs()

    products = []
    for slug in slugs:
        record = forge_reader.get_import_record(slug)
        candidates = forge_reader.get_patent_candidates(slug)
        gap_status = forge_reader.get_compliance_gap_status(slug)
        matrix = forge_reader.get_compliance_matrix(slug)
        products.append({
            "slug": slug,
            "version": record["version"],
            "import_date": record["import_date"],
            "phase": record["phase"],
            "candidate_count": len(candidates),
            "gap_status": gap_status,
            "has_matrix": matrix["found"],
            "matrix_is_prescreen": matrix["is_prescreen"],
        })

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "company": ctx,
        "products": products,
    })


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")
