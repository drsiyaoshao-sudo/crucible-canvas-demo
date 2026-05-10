"""
Read-only access to DERIVED-OK forge files. All forge I/O is funnelled through
this module. No route file touches FORGE_PATH directly.
"""
import csv
import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

FORGE_PATH = Path(os.getenv("FORGE_PATH", "/Users/siyaoshao/crucible-forge"))

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
    "docs/patent/disclosures/*/candidates_suggestion_*.md",
]

BLOCKED_PATTERNS = [
    "*/device_context.md",
    "*/test_reports/*",
    "*/fto_reports/*",
    "*/compliance/drafts/*",
    "*PRIVATE*",
]


def _allowed(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    for blocked in BLOCKED_PATTERNS:
        if fnmatch.fnmatch(rel, blocked):
            return False
    for pattern in DERIVED_OK_ALLOWLIST:
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False


def _safe_read(rel: str) -> Optional[str]:
    if not _allowed(rel):
        return None
    target = (FORGE_PATH / rel).resolve()
    try:
        target.relative_to(FORGE_PATH.resolve())
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Typed readers
# ---------------------------------------------------------------------------

def get_company_context() -> Dict[str, Any]:
    text = _safe_read("docs/company_context.md") or ""
    name_match = re.search(r"\*\*Company name:\*\*\s*(.+)", text)
    domain_match = re.search(r"\*\*Primary product domain:\*\*\s*(.+)", text)
    products = re.findall(r"^\|\s*([\w-]+)\s*\|[^|]+\|[^|]+\|[^|]+\|[^|]+\|", text, re.MULTILINE)
    products = [p for p in products if p not in ("Product slug", "---", "")]
    return {
        "company_name": name_match.group(1).strip() if name_match else "Unknown",
        "domain": domain_match.group(1).strip() if domain_match else "",
        "product_slugs": products,
        "raw": text,
    }


def _get_phase_from_context(slug: str) -> str:
    """Look up phase status for a slug from the company context product registry table."""
    text = _safe_read("docs/company_context.md") or ""
    pattern = rf"^\|\s*{re.escape(slug)}\s*\|[^|]+\|[^|]+\|[^|]+\|\s*([^|]+)\|"
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else "—"


def get_import_record(slug: str) -> Dict[str, Any]:
    text = _safe_read(f"products/{slug}/import_record.md") or ""
    version = re.search(r"\*\*Version:\*\*\s*(.+)", text)
    import_date = re.search(r"\*\*Import date:\*\*\s*(.+)", text)
    phase = _get_phase_from_context(slug)
    return {
        "version": version.group(1).strip() if version else "—",
        "import_date": import_date.group(1).strip() if import_date else "—",
        "phase": phase,
        "raw": text,
    }


def get_bom(slug: str) -> List[Dict[str, str]]:
    text = _safe_read(f"products/{slug}/bom.csv") or ""
    if not text:
        return []
    # Skip comment lines (# …) before passing to DictReader
    data_lines = [l for l in text.splitlines() if not l.startswith("#")]
    rows = []
    reader = csv.DictReader(data_lines)
    for row in reader:
        rows.append(dict(row))
    return rows


def get_firmware_manifest(slug: str) -> Dict[str, Any]:
    text = _safe_read(f"products/{slug}/firmware_manifest.json") or ""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def get_feature_abstract(slug: str) -> str:
    return _safe_read(f"products/{slug}/feature_abstract.md") or ""


def get_patent_candidates(slug: str) -> List[Dict[str, str]]:
    """Returns suggestion-mode patent candidates for a slug."""
    base = FORGE_PATH / "docs/patent/disclosures" / slug
    if not base.is_dir():
        return []
    candidates = []
    for f in sorted(base.glob("candidates_suggestion_*.md")):
        rel = str(f.relative_to(FORGE_PATH)).replace("\\", "/")
        if not _allowed(rel):
            continue
        text = f.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)", text, re.MULTILINE)
        first_line = title_match.group(1).strip() if title_match else f.stem
        summary_match = re.search(r"(?:##\s*Summary|##\s*Abstract)\s*\n+(.+)", text, re.MULTILINE)
        summary = summary_match.group(1).strip() if summary_match else ""
        heuristic_match = re.search(r"(?:heuristic|novelty score)[:\s]+([^\n]+)", text, re.IGNORECASE)
        heuristic = heuristic_match.group(1).strip() if heuristic_match else ""
        nonobvious_match = re.search(r"(?:non-?obvious(?:ness)?)[:\s]+([^\n]+)", text, re.IGNORECASE)
        nonobvious = nonobvious_match.group(1).strip() if nonobvious_match else ""
        candidates.append({
            "filename": f.name,
            "title": first_line,
            "summary": summary,
            "heuristic": heuristic,
            "nonobvious": nonobvious,
            "raw": text,
        })
    return candidates


def get_compliance_prescreen(slug: str) -> Dict[str, Any]:
    text = _safe_read(f"docs/candidates/{slug}/compliance_prescreen.md") or ""
    if not text:
        return {"found": False, "rows": [], "raw": ""}
    rows = []
    current_priority = "?"
    skip_headers = {"standard id", "standard", "---"}
    for line in text.splitlines():
        prio_match = re.match(r"^##\s+Priority\s+(\d)", line, re.IGNORECASE)
        if prio_match:
            current_priority = prio_match.group(1)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and cells[0].lower() not in skip_headers and not cells[0].startswith("---"):
            rows.append({
                "priority": current_priority,
                "standard": cells[0],
                "rationale": cells[1] if len(cells) > 1 else "",
                "jurisdiction": cells[2] if len(cells) > 2 else "",
            })
    return {"found": bool(rows), "rows": rows, "raw": text}


def get_compliance_matrix(slug: str) -> Dict[str, Any]:
    base = FORGE_PATH / "docs/compliance/matrix" / slug
    if not base.is_dir():
        return {"found": False, "rows": [], "filename": "", "is_prescreen": False}
    files = sorted(base.glob("*.md"), reverse=True)
    if not files:
        return {"found": False, "rows": [], "filename": "", "is_prescreen": False}
    chosen = files[0]
    rel = str(chosen.relative_to(FORGE_PATH)).replace("\\", "/")
    text = _safe_read(rel) or ""
    if not text:
        return {"found": False, "rows": [], "filename": chosen.name, "is_prescreen": False}

    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0] and cells[0] not in ("Standard", "---", "standard", ""):
            status = cells[2] if len(cells) > 2 else ""
            rows.append({
                "standard": cells[0],
                "clause": cells[1] if len(cells) > 1 else "",
                "status": status,
                "notes": cells[3] if len(cells) > 3 else "",
            })
    return {
        "found": bool(rows),
        "rows": rows,
        "filename": chosen.name,
        "is_prescreen": "prescreen" in chosen.name,
        "raw": text,
    }


def get_regulatory_calendar(slug: str) -> List[Dict[str, str]]:
    text = _safe_read("docs/compliance/regulatory_calendar.md") or ""
    entries = []
    for line in text.splitlines():
        if f"| {slug}" in line or f"|{slug}" in line or line.startswith(f"| {slug}"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 6 and cells[0] == slug:
                entries.append({
                    "product": cells[0],
                    "standard": cells[1],
                    "cert_date": cells[2] or "—",
                    "next_renewal": cells[3] or "—",
                    "days_until": cells[4] or "—",
                    "owner": cells[5] or "—",
                })
    return entries


def get_knowledge_map() -> Dict[str, Any]:
    text = _safe_read("docs/knowledge_map.json") or ""
    if not text:
        return {"nodes": [], "edges": []}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"nodes": [], "edges": []}


def _graph_patent_candidates(slug: str) -> List[Dict[str, Any]]:
    base = FORGE_PATH / "docs/patent/disclosures" / slug
    if not base.is_dir():
        return []
    results = []
    for f in sorted(base.glob("candidates_suggestion_*.md")):
        rel = str(f.relative_to(FORGE_PATH)).replace("\\", "/")
        if not _allowed(rel):
            continue
        raw = f.read_text(encoding="utf-8")
        for section in re.split(r"\n(?=## Candidate \d+:)", raw):
            m = re.match(r"## Candidate (\d+):\s*(.+)", section.strip())
            if not m:
                continue
            idx = int(m.group(1))
            title = m.group(2).strip()
            novelty_m = re.search(r"\*\*Novelty basis:\*\*\s*(.+)", section)
            desc_m = re.search(r"\*\*Description:\*\*\s*\n+(.+?)(?:\n\n|\*\*)", section, re.DOTALL)
            results.append({
                "idx": idx,
                "title": title,
                "short_title": title[:48] + "…" if len(title) > 48 else title,
                "novelty_basis": novelty_m.group(1).strip() if novelty_m else "",
                "description": (desc_m.group(1).strip()[:200] + "…") if desc_m else "",
            })
    return results


def _graph_compliance_standards(slug: str) -> List[Dict[str, Any]]:
    base = FORGE_PATH / "docs/compliance/matrix" / slug
    if not base.is_dir():
        return []
    files = sorted(base.glob("*.md"), reverse=True)
    if not files:
        return []
    rel = str(files[0].relative_to(FORGE_PATH)).replace("\\", "/")
    raw = _safe_read(rel) or ""
    if not raw:
        return []

    STATUS_RANK = {"NOT_TESTED": 0, "LIKELY_CONDITIONAL": 1, "LIKELY_PASS": 2, "N/A": 3}
    STATUS_COLORS = {"LIKELY_PASS": "#22c55e", "LIKELY_CONDITIONAL": "#f59e0b",
                     "NOT_TESTED": "#ef4444", "N/A": "#6b7280"}

    # Single pass: extract standard ID/name/priority from ### headers, then track clause statuses.
    # Header format: ### <ID> — <Name> (<Priority hint>)
    # ID must start with uppercase and contain only A-Z 0-9 - .  (no spaces — skips "Priority 2 Standards" etc.)
    standards: Dict[str, Dict] = {}
    current_std: Optional[str] = None

    for line in raw.splitlines():
        sec_m = re.match(r"^### ([A-Z0-9][A-Z0-9\-\.]+)\s*—\s*(.+?)(?:\s*\(([^)]+)\))?\s*$", line)
        if sec_m:
            sid = sec_m.group(1)
            name = sec_m.group(2).strip()
            raw_prio = (sec_m.group(3) or "").strip()
            if re.match(r"^P\d+$", raw_prio):
                priority = raw_prio
            elif "Priority 1" in raw_prio or raw_prio == "1":
                priority = "P1"
            elif "Priority 2" in raw_prio:
                priority = "P2"
            else:
                priority = raw_prio or "?"
            standards[sid] = {"id": sid, "name": name, "priority": priority, "worst_rank": 2}
            current_std = sid
            continue
        if current_std and line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 3 and cells[0] not in ("Clause ID", "---", "") and cells[2] in STATUS_RANK:
                standards[current_std]["worst_rank"] = min(
                    standards[current_std]["worst_rank"], STATUS_RANK[cells[2]]
                )

    rank_to_status = {v: k for k, v in STATUS_RANK.items()}
    results = []
    for info in standards.values():
        worst = rank_to_status.get(info["worst_rank"], "LIKELY_PASS")
        results.append({"id": info["id"], "name": info["name"], "priority": info["priority"],
                        "worst_status": worst, "color": STATUS_COLORS.get(worst, "#9ca3af")})
    return results


def _graph_bom_components(slug: str) -> List[Dict[str, Any]]:
    SKIP_REFS = {"R1", "R2", "W1", "VEL", "SR", "U2"}
    rows = get_bom(slug)
    return [
        {"ref": r.get("ref", "").strip(),
         "component": r.get("component", r.get("ref", "")).strip(),
         "part_number": r.get("part_number", "").strip(),
         "description": r.get("description", "").strip()}
        for r in rows if r.get("ref", "").strip() not in SKIP_REFS
    ]


def _graph_algorithm_features(slug: str) -> List[Dict[str, Any]]:
    text = get_feature_abstract(slug)
    if not text:
        return []
    m = re.search(r"##\s+The Solution\s*\n+(.*?)(?=\n##|\Z)", text, re.DOTALL)
    if not m:
        return []
    solution = " ".join(m.group(1).split())  # normalise whitespace
    # Split into individual sentences, then group into 4 feature chunks
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", solution) if s.strip()]
    n = len(sents)
    target = min(n, 4)
    # Distribute sentences evenly into exactly `target` chunks
    chunks = [" ".join(sents[(i * n) // target:((i + 1) * n) // target]) for i in range(target)]
    features = []
    for i, chunk in enumerate(chunks):
        first_sent = re.split(r"(?<=[.!?])\s+(?=[A-Z])", chunk)[0]
        # Skip leading subordinate clause ("When X fires, …") to avoid stub labels
        if re.match(r"^(When|As |To |While|Upon|After)\b", first_sent):
            first_sent = re.sub(r"^[^,]+,\s*", "", first_sent)
        short = re.split(r"[,;]", first_sent)[0]
        label = (short[:58] + "…") if len(short) > 58 else short
        features.append({
            "idx": i + 1,
            "label": label,
            "description": (chunk[:220] + "…") if len(chunk) > 220 else chunk,
        })
    return features


# Keywords used to derive cross-edges between BOM/features and patents/standards
_RADIO_KEYWORDS = {"ble", "nrf", "2.4", "radio", "wireless", "bluetooth"}
_BATTERY_KEYWORDS = {"lipo", "lithium", "battery", "cell", "bat"}
_RADIO_STANDARDS = {"FCC-15B", "FCC-15C", "CE-RED", "EN-300-328", "EN-301-489", "EN-50360"}
_BATTERY_STANDARDS = {"IEC-62133-2", "UL-2054", "UN-38.3"}

_DOMAIN_TERMS = [
    "push-off", "plantar", "ring buffer", "heel-strike", "terrain",
    "retrospective", "angular velocity", "single imu", "confirmation",
    "universal", "primary trigger", "silent", "loading",
]


def _texts_share_term(a: str, b: str) -> bool:
    a_l, b_l = a.lower(), b.lower()
    return any(t in a_l and t in b_l for t in _DOMAIN_TERMS)


def get_enriched_knowledge_map(registered_slugs: List[str]) -> Dict[str, Any]:
    """Product-focused graph: only registered slugs + patent/compliance/BOM/feature nodes.
    Framework and domain-vertical nodes from knowledge_map.json are excluded."""
    slug_set = set(registered_slugs)
    nodes: List[Dict] = []
    edges: List[Dict] = []
    existing_ids: set = set()
    edge_counter = 0

    def next_eid() -> str:
        nonlocal edge_counter
        edge_counter += 1
        return f"gen-e{edge_counter}"

    for slug in registered_slugs:
        nodes.append({"id": slug, "type": "product", "label": slug, "status": "active",
                      "description": f"Product: {slug}"})
        existing_ids.add(slug)

        # Collect derived nodes so we can cross-link them afterwards
        patent_nids: List[Dict] = []
        std_nids: List[Dict] = []
        bom_nids: List[Dict] = []
        feat_nids: List[Dict] = []

        for cand in _graph_patent_candidates(slug):
            nid = f"patent-{slug}-{cand['idx']}"
            nodes.append({
                "id": nid, "type": "patent_candidate",
                "label": f"P{cand['idx']}", "full_title": cand["title"],
                "novelty_basis": cand["novelty_basis"],
                "description": cand["description"], "status": "candidate",
                "color": "#a855f7",
            })
            existing_ids.add(nid)
            edges.append({"id": next_eid(), "from": slug, "to": nid, "relation": "discloses"})
            patent_nids.append({"id": nid, "text": f"{cand['title']} {cand['description']}"})

        for std in _graph_compliance_standards(slug):
            nid = f"std-{std['id']}"
            if nid not in existing_ids:
                nodes.append({
                    "id": nid, "type": "standard", "label": std["id"],
                    "description": std["name"], "priority": std["priority"],
                    "status": std["worst_status"], "color": std["color"],
                })
                existing_ids.add(nid)
            edges.append({"id": next_eid(), "from": slug, "to": nid, "relation": "maps_to"})
            std_nids.append({"id": nid, "std_id": std["id"]})

        for comp in _graph_bom_components(slug):
            nid = f"bom-{slug}-{comp['ref']}"
            nodes.append({
                "id": nid, "type": "bom_component", "label": comp["ref"],
                "component": comp["component"], "part_number": comp["part_number"],
                "description": comp["description"], "color": "#06b6d4",
            })
            existing_ids.add(nid)
            edges.append({"id": next_eid(), "from": slug, "to": nid, "relation": "includes"})
            bom_nids.append({"id": nid, "ref": comp["ref"],
                             "text": f"{comp['component']} {comp['description']} {comp['part_number']}"})

        for feat in _graph_algorithm_features(slug):
            nid = f"feat-{slug}-{feat['idx']}"
            nodes.append({
                "id": nid, "type": "feature", "label": f"F{feat['idx']}",
                "full_title": feat["label"], "description": feat["description"],
                "color": "#f97316",
            })
            existing_ids.add(nid)
            edges.append({"id": next_eid(), "from": slug, "to": nid, "relation": "implements"})
            feat_nids.append({"id": nid, "text": feat["description"]})

        # --- Cross-edges: feature → patent (supports) ---
        for feat in feat_nids:
            for pat in patent_nids:
                if _texts_share_term(feat["text"], pat["text"]):
                    edges.append({"id": next_eid(), "from": feat["id"], "to": pat["id"],
                                  "relation": "supports"})

        # --- Cross-edges: BOM → compliance (triggers) ---
        for comp in bom_nids:
            desc_l = comp["text"].lower()
            is_radio = any(k in desc_l for k in _RADIO_KEYWORDS)
            # Only the dedicated battery cell (ref starts with BAT) triggers battery standards;
            # MCU charging circuits are in scope via U1 → radio standards already
            is_battery = comp["ref"].startswith("BAT") or comp["ref"].startswith("BATT")
            for std in std_nids:
                if std["std_id"] in _RADIO_STANDARDS and is_radio:
                    edges.append({"id": next_eid(), "from": comp["id"], "to": std["id"],
                                  "relation": "triggers"})
                elif std["std_id"] in _BATTERY_STANDARDS and is_battery:
                    edges.append({"id": next_eid(), "from": comp["id"], "to": std["id"],
                                  "relation": "triggers"})

        # --- Cross-edges: BOM → patent (evidences) ---
        # Patents that cite bom.csv as evidence link to the key hardware component (U1)
        u1_nid = next((b["id"] for b in bom_nids if b["ref"] == "U1"), None)
        if u1_nid:
            for pat in patent_nids:
                if "bom.csv" in pat["text"].lower() or "single" in pat["text"].lower():
                    edges.append({"id": next_eid(), "from": u1_nid, "to": pat["id"],
                                  "relation": "evidences"})

    return {"nodes": nodes, "edges": edges}


def list_product_slugs() -> List[str]:
    products_dir = FORGE_PATH / "products"
    if not products_dir.is_dir():
        return []
    return [d.name for d in sorted(products_dir.iterdir()) if d.is_dir()]


def get_compliance_gap_status(slug: str) -> str:
    matrix = get_compliance_matrix(slug)
    if not matrix["found"]:
        return "none"
    statuses = [r["status"].upper() for r in matrix["rows"]]
    if any("NOT_TESTED" in s or "FAIL" in s for s in statuses):
        return "red"
    if any("CONDITIONAL" in s for s in statuses):
        return "amber"
    if statuses:
        return "green"
    return "none"
