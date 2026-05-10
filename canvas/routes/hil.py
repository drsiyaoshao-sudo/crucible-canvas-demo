"""
HIL terminal — thin SSE bridge between canvas and the crucible-gaitsense agent stack.

Canvas sends user input → runs `claude --print -p "<prompt>"` in GAITSENSE_PATH →
streams stdout line by line as SSE. Agents in the gaitsense repo (/toolchain,
/regression, uart-reader, hw-advisor, etc.) handle compile, flash, and testing.

No pre-recorded output. No fixed demo steps. Corpus-supremacy.
"""
import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="canvas/templates")
router = APIRouter()

GAITSENSE_PATH = Path(os.getenv("GAITSENSE_PATH", "/Users/siyaoshao/crucible-gaitsense"))
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")

# Empty MCP config — passed via --strict-mcp-config so the repo's MCP servers never start
_mcp_empty = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
_mcp_empty.write('{"mcpServers":{}}')
_mcp_empty.flush()
_MCP_EMPTY_PATH = _mcp_empty.name


@router.get("/hil", response_class=HTMLResponse)
async def hil_page(request: Request):
    return templates.TemplateResponse("hil.html", {
        "request": request,
        "gaitsense_path": str(GAITSENSE_PATH),
        "gaitsense_exists": GAITSENSE_PATH.is_dir(),
    })


@router.get("/api/hil/run")
async def hil_run_stream(prompt: str = ""):
    """SSE: run `claude --print -p <prompt>` inside the gaitsense repo, stream stdout."""

    async def gen():
        if not prompt.strip():
            yield "data: (empty prompt)\n\n"
            yield "data: [DONE]\n\n"
            return

        if not GAITSENSE_PATH.is_dir():
            yield f"data: ERROR: GAITSENSE_PATH not found: {GAITSENSE_PATH}\n\n"
            yield "data: [DONE]\n\n"
            return

        yield f"data: $ {CLAUDE_BIN} --print -p \"{prompt.strip()}\"\n\n"
        yield "data:  \n\n"

        try:
            proc = await asyncio.create_subprocess_exec(
                CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
                "--strict-mcp-config", "--mcp-config", _MCP_EMPTY_PATH,
                "-p", prompt.strip(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(GAITSENSE_PATH),
            )
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                yield f"data: {line if line else ' '}\n\n"
                await asyncio.sleep(0)

            rc = await proc.wait()
            yield "data:  \n\n"
            if rc != 0:
                yield f"data: [exit {rc}]\n\n"

        except FileNotFoundError:
            yield f"data: ERROR: '{CLAUDE_BIN}' not found — check CLAUDE_BIN env var\n\n"
        except Exception as exc:
            yield f"data: ERROR: {exc}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
