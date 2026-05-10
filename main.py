import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

load_dotenv()

from canvas.routes import dashboard, product, compliance, ip, graph, api, run, console, hearing, hil  # noqa: E402

app = FastAPI(title="Crucible-Canvas", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(dashboard.router)
app.include_router(product.router)
app.include_router(compliance.router)
app.include_router(ip.router)
app.include_router(graph.router)
app.include_router(api.router)
app.include_router(run.router)
app.include_router(console.router)
app.include_router(hearing.router)
app.include_router(hil.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8009)), reload=True)
