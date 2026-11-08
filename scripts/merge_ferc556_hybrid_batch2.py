"""
Merge FERC Form 556 batch 2 rows into projects.json AND add ferc_docket
provenance field to:
  - new batch 2 rows            (same URL as `source`)
  - all existing bess-ferc-*    (same URL as `source`)
  - the 6 phase-1 collision pairs' eia-* rows  (eLibrary docketsheet URL)

Only these fields are written for new bess-ferc rows:
    id, type, name, operator, city, state, lat, lng,
    capacity_mw, capacity_mwh (omit if blank), co_located,
    status, source, source_label, ferc_docket

county_name / county_fips are inserted later by scripts/add-county.mjs.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects.json"
BATCH2_CSV = ROOT / "data/contacts/ferc556_hybrid_batch2_candidates.csv"

# Only ship planned, non-collision rows from batch 2
SHIP_FILTER = lambda r: r["status"] == "planned" and str(r.get("collision_flag", "")).lower() != "true"

# Phase-1 collision pairs: {eia_id: docket_number}. Only pairs the user confirmed
# as the same facility — pulled from ferc556_hybrid_collisions.csv (6 unique pairs).
PHASE1_COLLISIONS = {
    "eia-68043": "QF23-1217-003",
    "eia-61068": "QF19-140-004",
    "eia-66717": "QF19-752-011",
    "eia-57082": "QF10-417-004",
    "eia-65820": "QF23-860-001",
    "eia-68871": "QF23-1318-000",
}

FERC_DOCKET_URL = "https://elibrary.ferc.gov/eLibrary/docketsheet?docket_number={docket}"

FIELD_ORDER_NEW = [
    "id", "type", "name", "operator", "city", "state",
    "lat", "lng", "capacity_mw", "capacity_mwh",
    "co_located", "status", "source", "source_label", "ferc_docket",
]


def _num(s, as_int=False):
    s = (s or "").strip() if isinstance(s, str) else s
    if s in (None, ""):
        return None
    try:
        v = float(s)
    except (ValueError, TypeError):
        return None
    if as_int and v.is_integer():
        return int(v)
    return v


def build_new_row(r):
    row = {
        "id": r["id"],
        "type": r["type"],
        "name": r["name"],
        "operator": r["operator"],
        "city": r["city"],
        "state": r["state"],
        "lat": _num(r["lat"]),
        "lng": _num(r["lng"]),
        "capacity_mw": _num(r["capacity_mw"]),
        "status": r["status"],
        "source": r["source"],
        "source_label": r["source_label"],
        "co_located": True,
        "ferc_docket": r["source"],
    }
    mwh = _num(r.get("capacity_mwh", ""))
    if mwh is not None:
        row["capacity_mwh"] = mwh
    return {k: row[k] for k in FIELD_ORDER_NEW if k in row}


def insert_after(d, after_key, new_key, new_val):
    """Return a new dict where new_key is placed right after after_key."""
    if new_key in d:
        d = {k: (new_val if k == new_key else v) for k, v in d.items()}
        return d
    out = {}
    inserted = False
    for k, v in d.items():
        out[k] = v
        if k == after_key and not inserted:
            out[new_key] = new_val
            inserted = True
    if not inserted:
        out[new_key] = new_val
    return out


def main():
    data = json.loads(PROJECTS.read_text())
    before = len(data["projects"])
    existing_ids = {p["id"]: i for i, p in enumerate(data["projects"])}

    # --- (1) Add ferc_docket to existing bess-ferc-* rows (source URL is the docket URL) ---
    added_ferc_docket_batch1 = 0
    for i, p in enumerate(data["projects"]):
        if p["id"].startswith("bess-ferc-") and "ferc_docket" not in p:
            data["projects"][i] = insert_after(p, "source_label", "ferc_docket", p["source"])
            added_ferc_docket_batch1 += 1

    # --- (2) Add ferc_docket to the 6 phase-1 collision eia-* rows ---
    added_ferc_docket_eia = 0
    for eia_id, docket in PHASE1_COLLISIONS.items():
        if eia_id not in existing_ids:
            print(f"WARN: {eia_id} not in projects.json; skipping", file=sys.stderr)
            continue
        i = existing_ids[eia_id]
        p = data["projects"][i]
        if "ferc_docket" in p:
            continue
        data["projects"][i] = insert_after(p, "source", "ferc_docket",
                                           FERC_DOCKET_URL.format(docket=docket))
        added_ferc_docket_eia += 1

    # --- (3) Merge new batch 2 rows ---
    with open(BATCH2_CSV) as f:
        rows = list(csv.DictReader(f))
    ship = [r for r in rows if SHIP_FILTER(r)]

    added_new = 0
    for r in ship:
        row = build_new_row(r)
        if row["id"] in existing_ids:
            print(f"SKIP (id exists): {row['id']}", file=sys.stderr)
            continue
        data["projects"].append(row)
        existing_ids[row["id"]] = len(data["projects"]) - 1
        added_new += 1

    after = len(data["projects"])
    PROJECTS.write_text(json.dumps(data, indent=2))

    print(f"Added {added_new} new bess-ferc rows (projects.json: {before} -> {after})")
    print(f"Set ferc_docket on {added_ferc_docket_batch1} existing bess-ferc-* rows")
    print(f"Set ferc_docket on {added_ferc_docket_eia} phase-1-collision eia-* rows")
    print(f"Shippable in batch 2 CSV: {len(ship)}")


if __name__ == "__main__":
    main()
