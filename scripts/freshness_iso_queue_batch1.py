#!/usr/bin/env python3
"""Freshness pass: take the 216 non-colliding LBNL survivors, match each on queue ID
against the current ISO queue file, and rebuild batch 1 from verified rows only.

Sources (all public, saved under data/queues/raw/current/, gitignored):
  CAISO   https://www.caiso.com/documents/publicqueuereport.xlsx         as-of 2026-11-08
  ERCOT   https://www.ercotqueue.com/data/projects.csv                   as-of 2026-11-05
          (ercotqueue.com republishes ERCOT's monthly GIS Report, CC-BY-4.0)
  SPP     https://opsportal.spp.org/Studies/GenerateActiveCSV            as-of 2026-11-06
  MISO    https://www.misoenergy.org/api/giqueue/getprojects             as-of 2026-11-08
  NYISO   https://www.nyiso.com/documents/20142/1407078/NYISO-Interconnection-Queue.xlsx
                                                                          as-of 2026-11-08
  ISO-NE  — no accessible single public file (GridInterConnect requires login)
  PJM     — no accessible single public file (Queue Point requires login)

Drops:
  - West and Southeast rows (non-ISO utilities, no single current source)
  - PJM, ISO-NE rows ("no current queue source")
  - Rows where current status = withdrawn, suspended, completed/in-service
  - Rows where current MW < 20
  - Rows not found in the current file (treated as withdrawn)
"""

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/queues/raw/current"
OUT_DIR = ROOT / "data/queues"
CANDIDATES = OUT_DIR / "iso_candidates.csv"

# As-of dates recorded at ingest time (see header).
AS_OF = {
    "CAISO":  "2026-11-08",   # Report Run Date on the CAISO xlsx
    "ERCOT":  "2026-11-05",   # ercotqueue manifest generated_at
    "SPP":    "2026-11-06",   # SPP CSV "Last Updated On" cell
    "MISO":   "2026-11-08",   # fetched today
}

SOURCE_URL = {
    "CAISO":  "https://www.caiso.com/documents/publicqueuereport.xlsx",
    "ERCOT":  "https://www.ercot.com/gridinfo/resource",
    "SPP":    "https://opsportal.spp.org/Studies/GenerateActiveCSV",
    "MISO":   "https://www.misoenergy.org/planning/resource-utilization/generator-interconnection/",
}


# ===== Normalizers =====================================================

STATUS_KEEP = "keep"
STATUS_DROP_WITHDRAWN = "withdrawn"
STATUS_DROP_SUSPENDED = "suspended"
STATUS_DROP_COMPLETED = "completed_or_in_service"
STATUS_DROP_NOT_FOUND = "not_found"
STATUS_DROP_LOW_MW = "mw_below_20"
STATUS_DROP_HYBRID = "now_hybrid"
STATUS_DROP_NO_SOURCE = "no_current_queue_source"
STATUS_DROP_NON_ISO = "non_iso_utility"


def year_from(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.year
    s = str(v).strip()
    # dd/mm/yyyy or mm/dd/yyyy or yyyy-mm-dd
    m = re.search(r"\b(20\d{2})\b", s)
    return int(m.group(1)) if m else None


def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


# ===== CAISO ============================================================

def load_caiso():
    """Return dict q_id(str) → {status, mw, year, now_hybrid}."""
    wb = openpyxl.load_workbook(RAW / "caiso_public_queue_report.xlsx",
                                read_only=True, data_only=True)
    out = {}
    # Active sheet
    ws = wb["Grid GenerationQueue"]
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i < 4:
            continue
        q = row[1]
        if q is None:
            continue
        qid = str(q).strip()
        type1, type2, type3 = row[6], row[7], row[8]
        mw1 = to_float(row[12])
        net = to_float(row[15])
        current_online = row[26]
        proposed = row[25]
        status_raw = (row[4] or "").strip().upper()
        now_hybrid = bool((type2 and str(type2).strip()) or (type3 and str(type3).strip()))
        # In Grid GenerationQueue, any row here is ACTIVE regardless of what the label says.
        out[qid] = {
            "status_raw": status_raw or "ACTIVE",
            "sheet": "Active",
            "mw": net or mw1,
            "year": year_from(current_online) or year_from(proposed),
            "now_hybrid": now_hybrid,
        }

    # Withdrawn
    ws = wb["Withdrawn Generation Projects"]
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i < 4:
            continue
        q = row[1]
        if q is None:
            continue
        qid = str(q).strip()
        if qid in out:
            continue  # already in active somehow; prefer active
        out[qid] = {"status_raw": "WITHDRAWN", "sheet": "Withdrawn",
                    "mw": None, "year": None, "now_hybrid": False}

    # Completed
    ws = wb["Completed Generation Projects"]
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i < 4:
            continue
        q = row[1]
        if q is None:
            continue
        qid = str(q).strip()
        if qid in out:
            continue
        out[qid] = {"status_raw": "COMPLETED", "sheet": "Completed",
                    "mw": None, "year": None, "now_hybrid": False}

    return out


# ===== ERCOT ============================================================

def load_ercot():
    out = {}
    with (RAW / "ercotqueue_projects.csv").open() as fh:
        for r in csv.DictReader(fh):
            qid = r["inr"].strip()
            fuel = (r.get("fuel") or "").strip()
            stage = (r.get("funnel_stage") or "").strip()
            status_raw = (r.get("status_raw") or "").strip()
            mw = to_float(r.get("capacity_mw"))
            yr = year_from(r.get("projected_cod"))
            now_hybrid = fuel not in ("Battery", "Storage") and fuel != ""
            out[qid] = {
                "status_raw": status_raw,
                "stage": stage,
                "mw": mw,
                "year": yr,
                "now_hybrid": now_hybrid,
            }
    return out


# ===== SPP ==============================================================

def load_spp():
    out = {}
    with (RAW / "spp_active_queue.csv").open() as fh:
        fh.readline()  # strip "Last Updated On,..."
        for r in csv.DictReader(fh):
            qid = (r.get("Generation Interconnection Number") or "").strip()
            if not qid:
                continue
            gen_type = (r.get("Generation Type") or "").strip()
            status_raw = (r.get("Status") or "").strip().upper()
            mw = to_float(r.get("Capacity")) or to_float(r.get("MAX Summer MW"))
            isd = r.get("In-Service Date") or r.get("Commercial Operation Date")
            yr = year_from(isd)
            # SPP's Battery/Storage is standalone; Hybrid is not.
            now_hybrid = (gen_type.lower() == "hybrid")
            out[qid] = {
                "status_raw": status_raw,
                "mw": mw,
                "year": yr,
                "now_hybrid": now_hybrid,
            }
    return out


# ===== MISO =============================================================

def load_miso():
    out = {}
    rows = json.loads((RAW / "miso_gi_queue.json").read_text())
    for r in rows:
        qid = (r.get("projectNumber") or "").strip()
        if not qid:
            continue
        status_raw = (r.get("applicationStatus") or "").strip()
        fuel = (r.get("fuelType") or "").strip()
        mw = r.get("summerNetMW") or r.get("winterNetMW")
        yr = year_from(r.get("inService"))
        now_hybrid = fuel.lower() == "hybrid"
        out[qid] = {
            "status_raw": status_raw,
            "mw": mw,
            "year": yr,
            "now_hybrid": now_hybrid,
        }
    return out


# ===== Classifier =======================================================

def classify(iso, cur, orig_mw):
    """Decide keep/drop. Returns (verdict, status_label_or_None)."""
    if cur is None:
        return (STATUS_DROP_NOT_FOUND, None)

    raw = (cur.get("status_raw") or "").upper()

    # Hard drops based on status strings seen across ISOs
    if any(w in raw for w in ("WITHDRAWN",)):
        return (STATUS_DROP_WITHDRAWN, None)
    if any(w in raw for w in ("SUSPEND",)):
        return (STATUS_DROP_SUSPENDED, None)
    if iso == "CAISO" and cur.get("sheet") == "Completed":
        return (STATUS_DROP_COMPLETED, None)
    if iso == "ERCOT" and cur.get("stage") == "5_commissioned":
        return (STATUS_DROP_COMPLETED, None)
    if iso == "SPP" and "COMMERCIAL OPERATION" in raw:
        return (STATUS_DROP_COMPLETED, None)
    if iso == "MISO" and raw in ("DONE", "LEGACY: DONE"):
        return (STATUS_DROP_COMPLETED, None)

    if cur.get("now_hybrid"):
        return (STATUS_DROP_HYBRID, None)

    mw = cur.get("mw") or orig_mw
    if not mw or mw < 20:
        return (STATUS_DROP_LOW_MW, None)

    # Promote under_construction when a strong signal exists; otherwise planned.
    if iso == "ERCOT" and cur.get("stage") == "4_construction":
        return (STATUS_KEEP, "under_construction")
    return (STATUS_KEEP, "planned")


# ===== Main =============================================================

def main():
    # Load the 216 non-colliding survivors (from the previous ingest).
    cand = list(csv.DictReader(CANDIDATES.open()))
    survivors = [r for r in cand if not r["_collision"]]

    # Load current ISO data
    loaders = {"CAISO": load_caiso, "ERCOT": load_ercot, "SPP": load_spp, "MISO": load_miso}
    current = {iso: fn() for iso, fn in loaders.items()}
    for iso, m in current.items():
        print(f"[current] {iso:<6} loaded {len(m)} rows", file=sys.stderr)

    # Classify each survivor
    outcomes = []  # list of dicts
    kept = []
    for s in survivors:
        iso = s["iso"]
        qid = s["q_id"]
        orig_mw = to_float(s["mw_1"])
        if iso in ("West", "Southeast"):
            outcomes.append({**s, "verdict": STATUS_DROP_NON_ISO, "cur_mw": "", "cur_year": "", "status": ""})
            continue
        if iso in ("PJM", "ISO-NE"):
            outcomes.append({**s, "verdict": STATUS_DROP_NO_SOURCE, "cur_mw": "", "cur_year": "", "status": ""})
            continue
        cur = current.get(iso, {}).get(qid)
        verdict, status_label = classify(iso, cur, orig_mw)
        row = {
            **s,
            "verdict": verdict,
            "cur_mw": (cur or {}).get("mw") or "",
            "cur_year": (cur or {}).get("year") or "",
            "status_raw": (cur or {}).get("status_raw") or "",
            "status": status_label or "",
        }
        outcomes.append(row)
        if verdict == STATUS_KEEP:
            kept.append({**s, **row, "cur_mw": cur["mw"], "cur_year": cur.get("year"), "status": status_label})

    # Write freshness report
    rep_path = OUT_DIR / "iso_freshness_report.csv"
    fieldnames = ["iso", "q_id", "name", "state", "county_name", "mw_1",
                  "cur_mw", "cur_year", "status_raw", "status", "verdict"]
    with rep_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(outcomes)

    # Funnel counts per ISO
    from collections import Counter
    per_iso_total = Counter(s["iso"] for s in survivors)
    per_verdict = Counter(o["verdict"] for o in outcomes)
    per_iso_kept = Counter(o["iso"] for o in outcomes if o["verdict"] == STATUS_KEEP)
    print(f"\n[funnel] 216 non-colliding survivors breakdown:", file=sys.stderr)
    print(f"  total by ISO:   {dict(per_iso_total)}", file=sys.stderr)
    print(f"  verdict counts: {dict(per_verdict)}", file=sys.stderr)
    print(f"  kept by ISO:    {dict(per_iso_kept)}", file=sys.stderr)

    # Rebuild batch 1: 25 largest by current MW
    kept.sort(key=lambda r: float(r["cur_mw"]), reverse=True)
    batch = kept[:25]
    print(f"\n[batch] {len(batch)} rows selected (max 25)", file=sys.stderr)

    # (final CSV written after we build the JSON records below)

    # Build final project objects
    final = []
    for s in batch:
        iso = s["iso"]
        qid = s["q_id"]
        name_lbnl = (s["project_name"] or "").strip()
        generic = (name_lbnl == "" or name_lbnl.lower() in
                   ("battery storage", "energy storage"))
        # Build a short county name (no trailing " County")
        short = re.sub(r"\s+(County|Parish|City|Borough|Municipality|Census Area|Municipio)$", "",
                       s["county_name"], flags=re.IGNORECASE)
        if generic:
            name = f"{iso} queue {qid} ({short}, {s['state']})"
        else:
            # title-case LBNL name if screaming caps / all lower
            name = re.sub(r"\s+", " ", name_lbnl).strip()
            if name.isupper() or name.islower():
                name = name.title()
        mw = float(s["cur_mw"])
        mw = int(mw) if mw == int(mw) else round(mw, 1)
        year_online = int(s["cur_year"]) if s["cur_year"] else None

        slug = re.sub(r"[^a-z0-9]+", "-", (qid or "").lower()).strip("-")
        pid = f"iso-{iso.lower().replace(' ', '-')}-{slug}"

        obj = {
            "id": pid,
            "type": "bess",
            "name": name,
            "city": short,
            "state": s["state"],
            "county_name": s["county_name"],
            "county_fips": s["county_fips"],
            "lat": float(s["lat"]),
            "lng": float(s["lng"]),
            "capacity_mw": mw,
            "status": s["status"],
        }
        if year_online:
            obj["year_online"] = year_online
        obj["source"] = SOURCE_URL[iso]
        obj["source_label"] = f"{iso} queue (current)"
        obj["queue_id"] = qid
        obj["iso"] = iso
        obj["queue_asof"] = AS_OF[iso]
        obj["location_approximate"] = True
        final.append(obj)

    # Guard against duplicate ids
    seen = set()
    for o in final:
        base = o["id"]; i = 2
        while o["id"] in seen:
            o["id"] = f"{base}-{i}"; i += 1
        seen.add(o["id"])

    (OUT_DIR / "iso_batch1.json").write_text(json.dumps(final, indent=2) + "\n")
    print(f"[batch] wrote {OUT_DIR / 'iso_batch1.json'} ({len(final)} rows)", file=sys.stderr)

    # Also write iso_batch1.csv (flat view for humans)
    csv_fields = ["id", "name", "iso", "queue_id", "queue_asof", "state", "county_name",
                  "county_fips", "lat", "lng", "capacity_mw", "status", "year_online",
                  "source", "source_label"]
    with (OUT_DIR / "iso_batch1.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=csv_fields, extrasaction="ignore")
        w.writeheader()
        for o in final:
            w.writerow(o)

    # Print the final 25
    print(f"\n-- batch 1 (fresh, {len(final)} rows) --", file=sys.stderr)
    for o in final:
        print(f"  {o['id']:<28} | {o['name'][:42]:<42} | {o['iso']:<6} | {o['queue_id']:<14} | {o['state']} | {o['county_name']:<22} | {o['capacity_mw']} MW | {o['status']:<17} | {o.get('year_online','-')} | asof {o['queue_asof']}", file=sys.stderr)


if __name__ == "__main__":
    main()
