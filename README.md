# Crucible-Canvas

Read-only web UI for [Crucible-Forge](https://github.com/drsiyaoshao-sudo/crucible-forge).
Canvas mounts a forge repo and renders its derived outputs as live navigable pages.
No React, no npm, no build step — FastAPI + HTMX + Tailwind CDN.

> **Canvas never generates data.** Everything it displays was produced by the forge pipeline.

---

## System hierarchy

```
crucible-gaitsense  (github: main)
│   Evidence corpus — BOM, firmware, HIL logs, domain primitives
│   Slash commands: /regression /toolchain /advisor /session
│   ↓  imported into forge via /handoff import
│
crucible-forge  (github: demo/gaitsense-forge)
│   IP + compliance pipeline
│   Slash commands: /patent novelty  /compliance screen  /compliance map
│   Writes derived files: candidates_suggestion_*.md, compliance matrix, prescreen
│   ↓  read-only mount via FORGE_PATH
│
crucible-canvas  (github: main)   ← you are here
    FastAPI + HTMX web UI
    Pages: /dashboard  /product  /compliance  /ip  /graph  /console  /hil
    Never writes. Never calls external services.
```

Data flows one direction: **gaitsense → forge → canvas**.
Canvas calls back into gaitsense only through the `/hil` terminal (subprocess SSE — no file writes).

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.11+ |
| [crucible-forge](https://github.com/drsiyaoshao-sudo/crucible-forge) cloned locally | `main` branch |

Canvas is read-only. You do not need Ollama, Claude, or any AI dependency to run it.
Those are forge-side concerns.

---

## Forge setup (required before canvas shows anything)

Canvas is a window onto forge. Without a populated forge repo, every page renders empty.

### 1. Clone forge

```bash
git clone -b demo/gaitsense-forge https://github.com/drsiyaoshao-sudo/crucible-forge
cd crucible-forge
pip install -r requirements.txt
```

### 2. The demo product: GaitSense

The only product ingested into forge for the demo is **`gaitsense`** — an ankle-worn gait analysis wearable (Seeed XIAO nRF52840 Sense + LSM6DS3TR-C IMU, BLE output).

Its static artifacts are already committed to the forge repo:

| File | Path in forge | What canvas shows |
|---|---|---|
| BOM | `products/gaitsense/bom.csv` | Component table on `/product/gaitsense` |
| Firmware manifest | `products/gaitsense/firmware_manifest.json` | Board, hash, lock status |
| Feature abstract | `products/gaitsense/feature_abstract.md` | Algorithm description |
| Import record | `products/gaitsense/import_record.md` | Version, import date, tier assignments |

The following are generated at demo time by running forge pipeline commands (see [Demo walkthrough](#demo-walkthrough) below):

| Generated file | Pipeline command | Canvas page |
|---|---|---|
| `docs/patent/disclosures/gaitsense/candidates_suggestion_*.md` | `/patent novelty gaitsense` | `/ip/gaitsense`, `/product/gaitsense` |
| `docs/candidates/gaitsense/compliance_prescreen.md` | `/compliance screen gaitsense` | `/compliance/gaitsense` |
| `docs/compliance/matrix/gaitsense/*.md` | `/compliance map gaitsense` | `/compliance/gaitsense` |

### 3. Forge environment and API key

Copy `.env.example` to `.env` inside the forge repo and fill in the values:

```bash
cd crucible-forge
cp .env.example .env
```

```
# crucible-forge/.env

# Required for suggestion-mode IP and compliance pipelines
ANTHROPIC_API_KEY=sk-ant-...

# Execution mode for the IP pipeline:
#   suggestion  — uses Anthropic cloud API (no local GPU needed, DERIVED-OK output only)
#   local       — uses Ollama (requires local model, unlocks PRIVATE full-mode output)
#   (omit)      — auto-detects: suggestion if Ollama unreachable, local otherwise
FORGE_IP_MODE=suggestion

# Path to this forge repo (used by canvas)
FORGE_PATH=/path/to/crucible-forge
```

**API key usage — what calls the Anthropic API and when:**

| Pipeline command | Model | Calls API when |
|---|---|---|
| `/patent novelty gaitsense` | `claude-sonnet-4-6` | `FORGE_IP_MODE=suggestion` or Ollama unreachable |
| `/compliance screen gaitsense` | `claude-sonnet-4-6` | always (no local alternative) |
| `/compliance map gaitsense` | `claude-sonnet-4-6` with KV caching | always (no local alternative) |

Both pipelines use **prompt caching** (`cache_control: ephemeral`) on the regulatory corpus to minimise token cost on repeated runs. The compliance screener and mapper are cloud-only; only the IP novelty scout has a local Ollama fallback.

For the demo, set `FORGE_IP_MODE=suggestion` and provide a valid `ANTHROPIC_API_KEY`. No GPU or local model required.

### 4. Forge pipeline commands

All pipeline commands are run with the Claude CLI inside the forge repo directory.
You need `claude` (Claude Code CLI) installed and authenticated.

```bash
cd /path/to/crucible-forge

# Patent novelty scout — writes suggestion candidates
claude "/patent novelty gaitsense"

# Compliance pre-screen — priority 1/2/3 standards table
claude "/compliance screen gaitsense"

# Compliance gap map — clause-level gap table
claude "/compliance map gaitsense"
```

Each command writes files into forge. Canvas picks them up on next page load — no restart needed.

---

## GaitSense repo (required for `/hil`)

The `/hil` page runs live agent commands inside the crucible-gaitsense repo.
It is the only canvas page that requires a third repo.

### Clone

```bash
git clone -b main https://github.com/drsiyaoshao-sudo/crucible-gaitsense
```

### What the HIL page does

Canvas sends a user prompt → runs `claude --print -p "<prompt>"` as a subprocess inside
`GAITSENSE_PATH` → streams stdout line by line as Server-Sent Events back to the browser.

The gaitsense repo's own slash commands handle compile, flash, regression, and hardware
advisory tasks. Canvas is just a terminal window over them.

Key commands you can run from the HIL page:

| Command | What it does |
|---|---|
| `/regression` | Full simulation profile matrix against stage-gate criteria |
| `/toolchain` | Review and update hardware/firmware toolchain config |
| `/advisor hw` | Hardware design review |
| `/advisor sw` | Algorithm / software design review |
| `/session` | Start a new evidence capture session |

### Additional env vars needed

Add these to your `.env` alongside `FORGE_PATH`:

```
GAITSENSE_PATH=/absolute/path/to/crucible-gaitsense
CLAUDE_BIN=claude   # path to claude CLI if not on PATH
```

If `GAITSENSE_PATH` is missing or the directory does not exist, the HIL page renders a
warning banner and the run button does nothing.

---

## Quick start

```bash
# 0. Clone forge first (canvas reads from it) — use the demo branch
git clone -b demo/gaitsense-forge https://github.com/drsiyaoshao-sudo/crucible-forge
# note the absolute path — you'll need it in step 4

# 1. Clone this repo
git clone https://github.com/drsiyaoshao-sudo/crucible-canvas
cd crucible-canvas

# 2. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env:
#   FORGE_PATH=/absolute/path/to/crucible-forge   ← required
#   PORT=8009                                      ← optional

# 5. Start the server
python main.py
```

Open [http://localhost:8009/dashboard](http://localhost:8009/dashboard).
The GaitSense card will appear. Run forge pipeline commands (see below) to populate it.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `FORGE_PATH` | Yes | Absolute path to a cloned crucible-forge repo |
| `GAITSENSE_PATH` | For `/hil` only | Absolute path to a cloned crucible-gaitsense repo |
| `CLAUDE_BIN` | For `/hil` only | Path to the `claude` CLI binary (default: `claude`) |
| `PORT` | No | HTTP port (default `8009`) |

---

## Route map

All UI routes are read-only GET pages. API routes are used internally by HTMX.

### UI pages

| Route | Description |
|---|---|
| `/` | Redirects to `/dashboard` |
| `/dashboard` | Company overview — product cards with compliance status and IP candidate count |
| `/product/{slug}` | BOM table, firmware snapshot, feature abstract, patent candidates |
| `/compliance/{slug}` | Gap table (FCC/IEC/RED clauses), regulatory calendar, prescreen priority |
| `/ip/{slug}` | Suggestion-mode patent candidates with heuristic badges |
| `/graph` | Cytoscape.js knowledge map from `docs/knowledge_map.json` |
| `/console` | Live log stream from forge pipeline jobs |
| `/hil` | Hardware-in-the-loop test runner page |

### API / HTMX endpoints

| Route | Description |
|---|---|
| `GET /api/status` | Server health and forge path check |
| `GET /api/product/{slug}` | JSON product summary |
| `GET /api/jobs` | List running/recent pipeline jobs |
| `GET /api/job/{job_id}` | Poll a specific job by ID |
| `GET /api/dashboard/refresh` | HTMX fragment — refreshes dashboard product cards |
| `POST /api/clear` | Clear completed job queue |
| `POST /api/command` | Dispatch a forge pipeline command (used by the Run button in UI) |
| `GET /api/hil/run` | Trigger a HIL test run |

### Hearing (constitutional review) endpoints

| Route | Description |
|---|---|
| `GET /hearing/list` | List constitutional hearing sessions |
| `POST /hearing/new` | Create a new hearing session |
| `GET /hearing/{sid}` | View a hearing session |
| `POST /hearing/{sid}/argue/{attorney}` | Submit an argument from an attorney agent |
| `POST /hearing/{sid}/justice` | Submit a justice question |
| `POST /hearing/{sid}/rule` | Issue a ruling |

---

## Demo walkthrough

Two terminals. One runs canvas, the other runs forge pipeline commands. Canvas auto-refreshes from forge files — no restart at any step.

**Terminal A — canvas (keep running)**

```bash
cd crucible-canvas
python main.py
# → http://localhost:8009
```

**Terminal B — forge pipeline (run commands in sequence)**

```bash
cd crucible-forge
```

---

### Step 1 — Dashboard (starting state)

Navigate to [http://localhost:8009/dashboard](http://localhost:8009/dashboard).

```
┌─────────────────────────────────────────────────────────────┐
│  Crucible                                    [console] [hil] │
├─────────────────────────────────────────────────────────────┤
│  Acme Corp                                    1 product      │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  GaitSense Ankle Wearable          v1.0  [INGESTION] │   │
│  │                                                      │   │
│  │  compliance: none ●    IP candidates: none ●         │   │
│  │                                                      │   │
│  │  [▶ Run Patent Scout]  [▶ Run Compliance]            │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

Badges show grey "none" — correct, pipeline hasn't run yet. The Run buttons dispatch forge commands directly from the UI (or use Terminal B below).

---

### Step 2 — Patent novelty scout

**Option A — UI:** click **Run Patent Scout** on the GaitSense card. Watch the badge update live.

**Option B — terminal:**

```bash
# Terminal B
claude "/patent novelty gaitsense"
# ~30–60 s — writes candidates_suggestion_<timestamp>.md
```

Dashboard updates to:

```
│  compliance: none ●    IP candidates: 4 ●                   │
│  [▶ Run Patent Scout]  [▶ Run Compliance]                    │
```

---

### Step 3 — Product page

Click the **GaitSense** card title → `/product/gaitsense`

```
┌─────────────────────────────────────────────────────────────┐
│  gaitsense                          [INGESTION]  [v1.0]     │
├──────────────────┬──────────────────────────────────────────┤
│ Firmware         │  Patent Candidates                        │
│ Board: XIAO …    │  ┌─────────────────────────────────────┐ │
│ Hash: a3f9…      │  │ Cadence estimation via IMU   [sugg] │ │
│ Status: LOCKED ● │  │ Asymmetry index BLE stream   [sugg] │ │
│                  │  │ …                                   │ │
│ Feature Abstract │  └─────────────────────────────────────┘ │
│ [Gait cadence…]  │                                          │
│ [expand ▼]       │                                          │
├──────────────────┴──────────────────────────────────────────┤
│ Bill of Materials                                            │
│  Ref     │ Mfr           │ Part            │ Tier           │
│  U1      │ STMicro       │ LSM6DS3TR-C     │ PRIMARY        │
│  …                                                          │
└─────────────────────────────────────────────────────────────┘
```

Click **expand** under Feature Abstract to see the full algorithm description inline (HTMX fetch, no page reload).

---

### Step 4 — IP candidates page

Click **IP** in the nav → `/ip/gaitsense`

```
┌─────────────────────────────────────────────────────────────┐
│  ⚠ SUGGESTION ONLY — Generated by cloud pipeline.           │
│    Verify all claims with qualified patent counsel.          │
├─────────────────────────────────────────────────────────────┤
│  IP Candidates — gaitsense            4 candidates           │
│                                                              │
│  ┌──────────────────────────────────────────────── [sugg] ┐ │
│  │ Cadence estimation via IMU integration                  │ │
│  │ Heuristic: novel signal processing chain                │ │
│  │ Non-obviousness: [expand ▼]                             │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────── [sugg] ┐ │
│  │ …                                                       │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

Every card carries a **SUGGESTION ONLY** banner and a "verify with patent counsel" note. No Ollama required — cloud-mode pipeline.

---

### Step 5 — Compliance screening

```bash
# Terminal B
claude "/compliance screen gaitsense"   # ~20–30 s
claude "/compliance map gaitsense"      # ~30–60 s
```

Or click **Run Compliance** on the dashboard card (runs both in sequence).

Navigate to `/compliance/gaitsense`:

```
┌─────────────────────────────────────────────────────────────┐
│  Compliance — gaitsense                                      │
│  ⚠ SUGGESTION ONLY — matrix generated from prescreen data   │
├────────────────────┬───────────────┬────────────────────────┤
│ Priority Standards │ Reg Calendar  │ Compliance Gap Table   │
│                    │               │                        │
│ [P1] FCC Part 15   │ FCC filing    │ Clause    │ Status     │
│ [P1] IEC 60601-1   │ 45d ⏰        │ FCC 15B   │ PASS   ●  │
│ [P2] EU RED        │               │ IEC 60601 │ COND   ●  │
│ [P3] ISO 14971     │               │ EU RED    │ NOT    ●  │
│                    │               │ …                      │
└────────────────────┴───────────────┴────────────────────────┘
```

Gap table colours:
- Green — `LIKELY_PASS`
- Amber — `LIKELY_CONDITIONAL`
- Red — `NOT_TESTED`

---

### Step 6 — Knowledge graph

Navigate to [/graph](http://localhost:8009/graph).

```
┌─────────────────────────────────────────────────────────────┐
│  Knowledge Graph                                             │
│                                                              │
│    [compliance] ──── [gaitsense] ──── [patent]              │
│                           │                                  │
│                      [IMU / BLE]                             │
│                                                              │
│  Click a node:                                               │
│  ┌────────────────────────┐                                  │
│  │ gaitsense              │                                  │
│  │ Ankle wearable gait    │                                  │
│  │ analysis device        │                                  │
│  │ → /product/gaitsense   │                                  │
│  └────────────────────────┘                                  │
└─────────────────────────────────────────────────────────────┘
```

Nodes coloured by domain. Click any node to open a sidebar with its description and, if it is a registered product, a link to its product page.

---

### Step 7 — HIL terminal (optional, requires crucible-gaitsense)

Navigate to [/hil](http://localhost:8009/hil).

```
┌─────────────────────────────────────────────────────────────┐
│  crucible-gaitsense agent terminal                           │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄   │
│  $ /regression                                               │
│  [regression-runner] loading stage-gate criteria…            │
│  [regression-runner] profile: walk_flat — PASS               │
│  [regression-runner] profile: stair_ascent — PASS            │
│  [regression-runner] profile: asymmetry_50pct — CONDITIONAL  │
│  [DONE]                                                      │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ > /regression                              [Enter ↵] │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

Type any gaitsense slash command (`/regression`, `/advisor hw`, `/toolchain`, `/session`) and press Enter. Output streams line by line. Arrow-up recalls previous commands.

---

**End state**: evidence built physics-first, governed constitutionally, visualised live.

---

## What canvas will never do

- Write to `FORGE_PATH` or any file
- Read private forge files (`device_context.md`, `test_reports/`, FTO reports, compliance drafts)
- Expose any `*PRIVATE*` path in a template or API response
- Pull in npm, webpack, vite, or any build step
- Connect to a database or external network service

All CSS and JS dependencies (Tailwind, HTMX, Alpine.js, Cytoscape.js) are loaded from CDN at runtime.
