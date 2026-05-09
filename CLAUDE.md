# Crucible-Canvas — Claude Code Entry Point

You are operating inside Crucible-Canvas, the UI layer over Crucible-Forge.
Canvas reads forge outputs and renders them as a live web interface. It does not
write to forge — all data flows one direction: forge → canvas.

Read this file in full before doing anything.

---

## What canvas is

A lightweight FastAPI + HTMX web app that mounts a forge repo (via `FORGE_PATH`)
and renders its DERIVED-OK outputs as navigable pages. No React, no npm, no build step.
Tailwind CSS via CDN. Alpine.js for minimal interactivity. Cytoscape.js for the graph page.

**Article II**: Canvas is read-only in v1. It displays, never transmits. No agent in canvas
sends data externally or writes to the forge repo.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Server | FastAPI + uvicorn | async, minimal, Python-native |
| Templates | Jinja2 (via FastAPI) | server-side render, HTMX-compatible |
| Styling | Tailwind CSS CDN | no build step |
| Interactivity | HTMX + Alpine.js CDN | no React, no bundler |
| Graph | Cytoscape.js CDN | renders docs/knowledge_map.json |
| Data source | Forge files via FORGE_PATH | mount forge repo read-only |

---

## Environment

```
FORGE_PATH=/Users/siyaoshao/crucible-forge   # path to the forge repo
PORT=8000                                     # default
```

Copy `.env.example` to `.env` and fill in `FORGE_PATH`.

---

## Repository layout (target)

```
crucible-canvas/
├── CLAUDE.md                  ← this file
├── .env.example
├── requirements.txt           ← fastapi, uvicorn, jinja2, python-dateutil
├── main.py                    ← FastAPI app, route registration
├── canvas/
│   ├── forge_reader.py        ← reads forge files via FORGE_PATH (DERIVED-OK only)
│   ├── routes/
│   │   ├── dashboard.py       ← GET /dashboard
│   │   ├── product.py         ← GET /product/{slug}
│   │   ├── compliance.py      ← GET /compliance/{slug}
│   │   ├── ip.py              ← GET /ip/{slug}
│   │   ├── graph.py           ← GET /graph
│   │   └── api.py             ← GET /api/status, /api/product/{slug}
│   └── templates/
│       ├── base.html          ← nav, Tailwind CDN, HTMX CDN, Alpine CDN
│       ├── dashboard.html
│       ├── product.html
│       ├── compliance.html
│       ├── ip.html
│       └── graph.html
└── static/                    ← favicon only; all CSS/JS from CDN
```

---

## Pages — build in this order

### 1. `/dashboard` (build first — demo entry point)

Reads from forge:
- `products/` directory — slug list, import dates
- `docs/company_context.md` — company name, product registry table
- `docs/compliance/matrix/<slug>/` — most recent matrix, overall gap status
- `docs/patent/disclosures/<slug>/` — candidate file names and count

Displays:
- Company name + product count
- Product card per slug: version, import date, phase status, compliance status badge, candidate count
- "Run pipeline" hint if no compliance matrix or no candidates exist yet (demo: they will be generated live)

### 2. `/product/{slug}`

Reads from forge:
- `products/<slug>/import_record.md` — tier assignments, import date
- `products/<slug>/bom.csv` — component table (DERIVED-OK)
- `products/<slug>/firmware_manifest.json` — board, hash, libraries (DERIVED-OK)
- `products/<slug>/feature_abstract.md` — algorithm description (DERIVED-OK)
- `docs/patent/disclosures/<slug>/` — candidate file list + first-line summary per file

Displays:
- BOM table
- Firmware snapshot (board, hash, lock status)
- Feature abstract (truncated, expandable via HTMX)
- Patent candidates list (suggestion mode badge if filename contains `_suggestion_`)

### 3. `/compliance/{slug}`

Reads from forge:
- `docs/candidates/<slug>/compliance_prescreen.md` — priority table
- `docs/compliance/matrix/<slug>/` — most recent matrix, row-by-row gap table
- `docs/compliance/regulatory_calendar.md` — next action dates for this slug

Displays:
- Priority 1/2/3 standards table from prescreen
- Gap table from matrix (LIKELY_PASS → green, LIKELY_CONDITIONAL → amber, NOT_TESTED → red)
- Regulatory calendar entries for this product
- SUGGESTION ONLY banner if matrix filename contains `prescreen_`

### 4. `/ip/{slug}`

Reads from forge:
- `docs/patent/disclosures/<slug>/candidates_suggestion_*.md` — suggestion candidates only
- Does NOT read `candidates_*.md` (PRIVATE full-mode output — not available without Ollama)

Displays:
- SUGGESTION ONLY banner (always — cloud IP pipeline)
- Candidate cards: title, heuristic badge, non-obviousness rationale
- "Verify with patent counsel" reminder on every card

### 5. `/graph`

Reads from forge:
- `docs/knowledge_map.json` — nodes and edges

Displays:
- Cytoscape.js graph, nodes coloured by domain
- Click a node → sidebar with node description and links to `/product/<id>` if registered

---

## forge_reader.py — the only file that touches FORGE_PATH

All forge file access goes through `canvas/forge_reader.py`. No route reads forge files directly.

Rules the reader must enforce:
- Only reads files under `FORGE_PATH` — never writes
- Only reads DERIVED-OK or PUBLIC files — never reads `device_context.md`, `test_reports/`,
  `docs/patent/fto_reports/`, `docs/compliance/drafts/`, or any `*PRIVATE*` path
- Returns empty/default values when a file is missing — never 404s the page
- Paths are validated against an allowlist (no path traversal)

```python
DERIVED_OK_ALLOWLIST = [
    "products/*/bom.csv",
    "products/*/firmware_manifest.json",
    "products/*/feature_abstract.md",
    "products/*/import_record.md",
    "docs/company_context.md",
    "docs/knowledge_map.json",
    "docs/candidates/*/compliance_prescreen.md",
    "docs/compliance/matrix/*/*.md",
    "docs/compliance/regulatory_calendar.md",
    "docs/patent/disclosures/*/candidates_suggestion_*.md",  # suggestion only
]
```

---

## Demo flow (what the judges see)

1. Open `/dashboard` — GaitSense card shows "no compliance matrix yet, no IP candidates yet"
2. In terminal: `/patent novelty gaitsense` → novelty-scout runs, writes candidates file
3. Refresh `/dashboard` → candidate count appears; click into `/product/gaitsense` → candidates listed
4. In terminal: `/compliance screen gaitsense` then `/compliance map gaitsense`
5. Refresh `/compliance/gaitsense` → gap table appears with FCC/IEC/RED clause citations
6. Click `/graph` → knowledge map with gaitsense node highlighted
7. End: "Evidence built physics-first, governed constitutionally, visualised live"

Canvas never generates data — it only renders what forge has produced. The demo shows
the forge pipeline running live, with canvas as the read-only window.

---

## Build sequence

Build in this order. Each step must run before the next.

1. **Scaffold** — `main.py`, `requirements.txt`, `.env.example`, `canvas/__init__.py`
2. **forge_reader.py** — reads all DERIVED-OK forge files; returns typed dicts; no routes yet
3. **`/dashboard`** — first page; proves forge_reader works end-to-end
4. **`/product/{slug}`** — BOM table + firmware snapshot + feature abstract + candidate list
5. **`/compliance/{slug}`** — gap table with colour coding + regulatory calendar
6. **`/ip/{slug}`** — suggestion candidates with banners
7. **`/graph`** — Cytoscape graph from knowledge_map.json
8. **`/api/*`** — JSON endpoints for HTMX polling (used by dashboard for live refresh)

Do not start step N+1 until step N renders correctly in a browser.

---

## What you must not do

- Never write to `FORGE_PATH` — canvas is read-only
- Never read `device_context.md`, `test_reports/`, FTO reports, or compliance drafts
- Never expose PRIVATE content in any template or API response
- Never add npm, webpack, vite, or any build step — CDN only
- Never add a database — canvas reads forge files directly; no caching layer in v1
