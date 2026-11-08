"""
Merge the 25 approved FERC Form 556 hybrid rows from
data/contacts/ferc556_hybrid_top25.csv into projects.json.

Only these fields are written per row:
    id, type, name, operator, city, state, lat, lng,
    capacity_mw, capacity_mwh (omit if blank), co_located,
    status, source, source_label

county_name and county_fips are intentionally omitted here; scripts/add-county.mjs
inserts them after the "state" line by point-in-polygon on Census shapefiles.

Formatting matches ingest_eia.py: json.dumps(data, indent=2), no trailing newline.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects.json"
TOP25 = ROOT / "data/contacts/ferc556_hybrid_top25.csv"

FIELD_ORDER = [
    "id", "type", "name", "operator", "city", "state",
    "lat", "lng", "capacity_mw", "capacity_mwh",
    "co_located", "status", "source", "source_label",
]


def _num(s, as_int=False):
    s = (s or "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if as_int and v.is_integer():
        return int(v)
    return v


def build_row(r):
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
    }
    mwh = _num(r.get("capacity_mwh", ""))
    if mwh is not None:
        row["capacity_mwh"] = mwh
    # Enforce field order
    ordered = {}
    for k in FIELD_ORDER:
        if k in row:
            ordered[k] = row[k]
    return ordered


def main():
    data = json.loads(PROJECTS.read_text())
    before = len(data["projects"])
    existing_ids = {p["id"] for p in data["projects"]}

    with open(TOP25) as f:
        rows = list(csv.DictReader(f))

    new_rows = []
    for r in rows:
        row = build_row(r)
        if row["id"] in existing_ids:
            print(f"SKIP (id exists): {row['id']}", file=sys.stderr)
            continue
        new_rows.append(row)
        existing_ids.add(row["id"])

    data["projects"].extend(new_rows)
    after = len(data["projects"])

    PROJECTS.write_text(json.dumps(data, indent=2))

    print(f"Added {len(new_rows)} rows to projects.json")
    print(f"Row count: {before} -> {after}")
    print(f"Delta: +{after - before}")


if __name__ == "__main__":
    main()
