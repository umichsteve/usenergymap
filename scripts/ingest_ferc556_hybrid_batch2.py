"""
Batch 2: FERC Form 556 hybrids from the 10-20 MW facility band.

Phase 1 (ingest_ferc556_hybrid.py) ran the 20-1000 MW band. This script reuses
the same extraction rules on the 10-20 MW facility band. It also pulls
QF23-1297-001 (11.25 MW / 45 MWh, already extracted) from the Phase 1
candidates CSV so batch 2 ships it together.

Same rules:
  - battery term required
  - storage_mw only from explicit battery rating (A_pair or B_lone_mw)
  - MWh only from stated MWh OR stated N-hour duration
  - storage_mw >= 10
  - docket year >= 2022 -> planned; older -> needs_review (not shipped)
  - collision check vs ALL current projects.json rows (1 km OR same-state
    name-token Jaccard >= 0.5)

Writes ferc556_hybrid_batch2_candidates.csv and
ferc556_hybrid_batch2_collisions.csv.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ingest_ferc556_hybrid import (  # noqa: E402
    BATTERY_RE,
    NEGATION_RE,
    NON_BATTERY_STORAGE_RE,
    clean_display_name,
    docket_year,
    elibrary_url,
    extract_bess,
    haversine_km,
    infer_mwh_from_duration,
    jaccard,
    name_tokens,
    slugify,
    title_case_name,
)

HYBRID_DEFER = ROOT / "data/contacts/ferc556_hybrid_defer.csv"
RAW_FERC = ROOT / "data/contacts/raw/ferc_form556.csv"
PROJECTS_JSON = ROOT / "projects.json"
PHASE1_CANDIDATES = ROOT / "data/contacts/ferc556_hybrid_candidates.csv"

OUT_CANDIDATES = ROOT / "data/contacts/ferc556_hybrid_batch2_candidates.csv"
OUT_COLLISIONS = ROOT / "data/contacts/ferc556_hybrid_batch2_collisions.csv"

MW_FLOOR = 10.0
CAP_BAND = (10.0, 20.0)
COLLISION_KM = 1.0
COLLISION_JACCARD = 0.5

INCLUDE_IDS = {"bess-ferc-qf23-1297-001"}


def main():
    counts = {}

    df = pd.read_csv(HYBRID_DEFER, low_memory=False)
    df["cap"] = pd.to_numeric(df["capacity_mw"], errors="coerce")
    counts["hybrid_defer_rows"] = len(df)

    # Hydrate full facility_description from raw FERC (defer CSV is clipped to 500 chars).
    raw = pd.read_csv(
        RAW_FERC,
        low_memory=False,
        usecols=["Docket_Number", "Submission_ID", "Facility_Description",
                 "Facility_City", "Submission_Date"],
    )
    raw["_dt"] = pd.to_datetime(raw["Submission_Date"], errors="coerce")
    raw = raw.sort_values("_dt", ascending=False, na_position="last")
    raw["_key"] = raw["Docket_Number"].astype("string")
    raw["_key"] = raw["_key"].where(
        raw["_key"].notna() & (raw["_key"].str.strip() != ""),
        other="SUB:" + raw["Submission_ID"].astype(str),
    )
    raw_latest = raw.drop_duplicates(subset=["_key"], keep="first")
    desc_by = dict(zip(raw_latest["_key"].astype(str),
                       raw_latest["Facility_Description"].astype(str)))
    city_by = dict(zip(raw_latest["_key"].astype(str),
                       raw_latest["Facility_City"].astype(str)))
    df["facility_description"] = (
        df["docket_number"].astype(str).map(desc_by).fillna(df["facility_description"])
    )
    df["facility_city"] = df["docket_number"].astype(str).map(city_by).fillna("")

    band = df[(df["cap"] >= CAP_BAND[0]) & (df["cap"] <= CAP_BAND[1])].copy()
    counts["in_band_10_20_mw"] = len(band)

    # Collapse by docket prefix (take latest submission per facility)
    band["_prefix"] = band["docket_number"].astype(str).str.replace(r"-\d+$", "", regex=True)
    band["_dt"] = pd.to_datetime(band["submission_date"], errors="coerce")
    band_sorted = band.sort_values("_dt", ascending=False, na_position="last")
    keep_mask = ~band_sorted["_prefix"].duplicated(keep="first")
    band = band_sorted[keep_mask].copy()
    counts["after_docket_prefix_collapse"] = len(band)

    desc_series = band["facility_description"].fillna("").astype(str)
    has_battery = desc_series.apply(lambda s: bool(BATTERY_RE.search(s)))
    negated = desc_series.apply(
        lambda s: bool(NEGATION_RE.search(s))
        and len(BATTERY_RE.findall(s)) <= len(NEGATION_RE.findall(s))
    )
    non_batt_storage_only = desc_series.apply(
        lambda s: bool(NON_BATTERY_STORAGE_RE.search(s)) and not BATTERY_RE.search(s)
    )
    keep = has_battery & ~negated & ~non_batt_storage_only
    pool = band[keep].copy()
    counts["after_battery_filter"] = len(pool)

    pool["_parsed"] = pool["facility_description"].apply(extract_bess)
    pool["_storage_mw"] = pool["_parsed"].apply(lambda d: d["storage_mw"])
    pool["_storage_mwh"] = pool["_parsed"].apply(lambda d: d["storage_mwh"])
    pool["_coupling"] = pool["_parsed"].apply(lambda d: d["coupling"])
    pool["_evidence"] = pool["_parsed"].apply(lambda d: d["evidence"])
    pool["_strategy"] = pool["_parsed"].apply(lambda d: d["strategy"])

    strat_counts = pool["_strategy"].value_counts().to_dict()
    counts.update({f"strategy_{k}": v for k, v in strat_counts.items()})

    pool = pool[pool["_storage_mw"].notna()].copy()
    counts["after_bess_sizing_extracted"] = len(pool)

    pool = pool[pool["_storage_mw"] >= MW_FLOOR].copy()
    counts["after_storage_mw_floor_10"] = len(pool)

    pool["lat"] = pd.to_numeric(pool["lat"], errors="coerce")
    pool["lng"] = pd.to_numeric(pool["lng"], errors="coerce")
    coords_ok = (
        pool["lat"].notna() & pool["lng"].notna()
        & (pool["lat"] != 0) & (pool["lng"] != 0)
    )
    pool = pool[coords_ok].copy()
    counts["after_coords"] = len(pool)

    # Collision check vs all current projects.json rows
    with open(PROJECTS_JSON) as f:
        projects = json.load(f)["projects"]
    counts["projects_in_json"] = len(projects)
    proj_df = pd.DataFrame([
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "state": p.get("state"),
            "lat": p.get("lat"),
            "lng": p.get("lng"),
            "tokens": name_tokens(p.get("name")),
        }
        for p in projects
    ])
    proj_df["lat"] = pd.to_numeric(proj_df["lat"], errors="coerce")
    proj_df["lng"] = pd.to_numeric(proj_df["lng"], errors="coerce")
    lat_arr = proj_df["lat"].to_numpy(float)
    lng_arr = proj_df["lng"].to_numpy(float)

    collision_flags = {}
    collisions_rows = []
    for idx, row in pool.iterrows():
        dists = haversine_km(row["lat"], row["lng"], lat_arr, lng_arr)
        near = np.where(dists <= COLLISION_KM)[0]
        hits = [(proj_df.iloc[i]["id"], f"proximity_{dists[i]:.2f}km") for i in near]
        state_code = row.get("state")
        if state_code:
            same_state = proj_df[proj_df["state"] == state_code]
            ferc_toks = name_tokens(row.get("facility_name"))
            for _, p in same_state.iterrows():
                j = jaccard(ferc_toks, p["tokens"])
                if j >= COLLISION_JACCARD:
                    hits.append((p["id"], f"name_jaccard_{j:.2f}"))
        if hits:
            collision_flags[idx] = hits
            for pid, reason in hits:
                collisions_rows.append({
                    "ferc_docket": row.get("docket_number"),
                    "ferc_facility": row.get("facility_name"),
                    "ferc_applicant": row.get("applicant_name"),
                    "ferc_state": state_code,
                    "ferc_storage_mw": row["_storage_mw"],
                    "project_id": pid,
                    "reason": reason,
                })
    counts["candidates_with_collisions"] = len(collision_flags)

    candidates = []
    status_counts = {"planned": 0, "needs_review": 0}
    for idx, row in pool.iterrows():
        docket = str(row.get("docket_number") or "").strip()
        yr = docket_year(docket)
        status = "planned" if yr is not None and yr >= 2022 else "needs_review"
        status_counts[status] += 1

        facility = clean_display_name(row.get("facility_name")) or f"FERC {docket}"
        name = facility if "hybrid" in facility.lower() else f"{facility} Hybrid"

        storage_mw = float(row["_storage_mw"])
        storage_mwh = row["_storage_mwh"]
        mwh_strategy = "A_pair" if pd.notna(storage_mwh) else ""
        if pd.isna(storage_mwh):
            inferred_mwh, hours, _ = infer_mwh_from_duration(
                row.get("facility_description"), storage_mw,
            )
            if inferred_mwh is not None:
                storage_mwh = inferred_mwh
                mwh_strategy = f"duration_{hours}h"

        candidates.append({
            "id": f"bess-ferc-{slugify(docket)}",
            "type": "bess",
            "name": name,
            "operator": title_case_name(row.get("applicant_name")),
            "city": title_case_name(row.get("facility_city")) or "",
            "state": row.get("state") or "",
            "county_name": "",
            "lat": round(float(row["lat"]), 5),
            "lng": round(float(row["lng"]), 5),
            "capacity_mw": round(float(storage_mw), 2),
            "capacity_mwh": round(float(storage_mwh), 2) if pd.notna(storage_mwh) else "",
            "mwh_source": mwh_strategy,
            "coupling": row["_coupling"],
            "co_located": True,
            "status": status,
            "docket_year": yr if yr is not None else "",
            "source": elibrary_url(docket),
            "source_label": "FERC Form 556",
            "docket_number": docket,
            "submission_date": row.get("submission_date") or "",
            "energy_input": row.get("energy_input") or "",
            "facility_mw_reported": row.get("capacity_mw"),
            "extraction_strategy": row["_strategy"],
            "evidence": row["_evidence"],
            "collision_flag": bool(collision_flags.get(idx)),
            "collision_with": "; ".join(
                f"{pid} ({reason})" for pid, reason in collision_flags.get(idx, [])
            ),
        })

    # Pull in the always-include rows from Phase-1 candidates (they live outside
    # the 10-20 band but are approved for batch 2 inclusion).
    include_rows = []
    with open(PHASE1_CANDIDATES) as f:
        for r in csv.DictReader(f):
            if r["id"] in INCLUDE_IDS:
                include_rows.append(r)
    counts["phase1_include_rows_pulled"] = len(include_rows)

    # Re-collision-check the include rows against current projects.json
    for r in include_rows:
        try:
            lat = float(r["lat"]); lng = float(r["lng"])
        except ValueError:
            continue
        dists = haversine_km(lat, lng, lat_arr, lng_arr)
        near = np.where(dists <= COLLISION_KM)[0]
        hits = [(proj_df.iloc[i]["id"], f"proximity_{dists[i]:.2f}km") for i in near]
        state_code = r.get("state")
        if state_code:
            same_state = proj_df[proj_df["state"] == state_code]
            ferc_toks = name_tokens(r.get("name"))
            for _, p in same_state.iterrows():
                j = jaccard(ferc_toks, p["tokens"])
                if j >= COLLISION_JACCARD:
                    hits.append((p["id"], f"name_jaccard_{j:.2f}"))
        r["collision_flag"] = "True" if hits else "False"
        r["collision_with"] = "; ".join(f"{pid} ({reason})" for pid, reason in hits)
        candidates.append(r)
        for pid, reason in hits:
            collisions_rows.append({
                "ferc_docket": r.get("docket_number"),
                "ferc_facility": r.get("name"),
                "ferc_applicant": r.get("operator"),
                "ferc_state": state_code,
                "ferc_storage_mw": r.get("capacity_mw"),
                "project_id": pid,
                "reason": reason,
            })

    counts["status_planned"] = status_counts["planned"]
    counts["status_needs_review"] = status_counts["needs_review"]

    candidates.sort(key=lambda r: float(r["capacity_mw"]), reverse=True)

    cols = [
        "id", "type", "name", "operator", "city", "state", "county_name",
        "lat", "lng", "capacity_mw", "capacity_mwh", "mwh_source", "coupling",
        "co_located", "status", "docket_year", "source", "source_label",
        "docket_number", "submission_date", "energy_input",
        "facility_mw_reported", "extraction_strategy", "evidence",
        "collision_flag", "collision_with",
    ]
    _write_csv(OUT_CANDIDATES, cols, candidates)

    if collisions_rows:
        _write_csv(OUT_COLLISIONS, list(collisions_rows[0].keys()), collisions_rows)
    else:
        with open(OUT_COLLISIONS, "w") as f:
            f.write("ferc_docket,ferc_facility,ferc_applicant,ferc_state,"
                    "ferc_storage_mw,project_id,reason\n")

    print()
    print("=" * 72)
    print("BATCH 2 FUNNEL (10-20 MW band + always-include rows)")
    print("=" * 72)
    for k, v in counts.items():
        print(f"  {k:40s} : {v:>6}")

    ship = [c for c in candidates if c["status"] == "planned" and not (
        c["collision_flag"] is True or str(c["collision_flag"]).lower() == "true"
    )]
    print()
    print("=" * 72)
    print(f"BATCH 2 SHIPPABLE ROWS ({len(ship)}) — planned, no collision")
    print("=" * 72)
    for c in ship:
        mwh = (
            f"{float(c['capacity_mwh']):>7.2f} MWh"
            if c.get("capacity_mwh") not in ("", None)
            else "   -    MWh"
        )
        print(f"  {float(c['capacity_mw']):7.2f} MW / {mwh}  [{c['coupling']:7s}]  "
              f"{c['state']}  {c['docket_number']:14s}  {c['name'][:55]}")

    print()
    print("Wrote:")
    print(f"  {OUT_CANDIDATES.relative_to(ROOT)}")
    print(f"  {OUT_COLLISIONS.relative_to(ROOT)}")


def _write_csv(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


if __name__ == "__main__":
    main()
