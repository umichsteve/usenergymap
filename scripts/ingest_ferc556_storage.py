"""
ingest_ferc556_storage.py — Phase 1: FERC Form 556 -> candidate BESS rows for
projects.json. No projects.json writes. No network.

Data truth (see probe output in the PR description): Form 556 is a PURPA
Qualifying Facility (QF) filing and carries an Energy_Input *generation fuel*
code -- there is no "storage" code. Standalone BESS almost never qualifies as a
QF because batteries have no fuel input. Nearly all 2023+ dockets that mention
"battery/storage/BESS" are solar+storage (or hydro/biomass) hybrids.

The funnel applied here:

  1. Collapse ds=35 to the latest submission per Docket_Number.
  2. Keep only rows filed 2023-01-01 or later with battery/storage/BESS/batteries
     keyword in Facility_Description.
  3. Drop rows whose description contains "withdraw".
  4. Collapse recerts across docket numbers for the same facility
     (Applicant_Name + state + ~110m lat/lng bucket).
  5. Hybrid split -- a row is a hybrid if EITHER
         Energy_Input matches a generation-fuel code (R-SP/R-HR/R-W/R-ST/B-*/G-/F-*)
      OR Facility_Description contains solar/photovoltaic/pv/wind/hybrid/
         co-located/paired/aggregat.
     Hybrids go to ferc556_hybrid_defer.csv -- Form 556 reports the generation-
     side MaxNetPwr_Capacity, so using it as storage capacity would be wrong.
  6. Standalone pool must also mention "battery|batteries|bess" explicitly
     (not just "storage" -- hydro reservoirs trip that word).
  7. MW floor: MaxNetPwr_Capacity / 1000 >= 10.
  8. Lat/lng required (map rendering needs coords); sub-split to
     ferc556_no_coords.csv.
  9. Collision check vs ALL 1,608 projects.json rows (every type and status)
     by 1km proximity OR same-state name-token Jaccard >= 0.5. Collisions are
     flagged for manual review, not auto-dropped from candidates.

All survivors go to ferc556_ingest_candidates.csv ranked by capacity_mw desc.
All drops have a drop_reason. The top-25 by capacity ships to ferc556_top25.csv.

Status is always "planned" -- Form 556 has no in-service / commercial-operation
date column, and if a facility were in EIA-860M operating set build_contacts.py
would have matched it already.

county_fips is left blank; scripts/add-county.mjs fills it on merge.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_JSON = ROOT / "projects.json"
RAW_FERC = ROOT / "data/contacts/raw/ferc_form556.csv"

OUT_CANDIDATES = ROOT / "data/contacts/ferc556_ingest_candidates.csv"
OUT_DROPS = ROOT / "data/contacts/ferc556_drops.csv"
OUT_COLLISIONS = ROOT / "data/contacts/ferc556_collisions.csv"
OUT_HYBRID = ROOT / "data/contacts/ferc556_hybrid_defer.csv"
OUT_NOCOORDS = ROOT / "data/contacts/ferc556_no_coords.csv"
OUT_TOP25 = ROOT / "data/contacts/ferc556_top25.csv"

MW_FLOOR = 10.0
COLLISION_KM = 1.0
COLLISION_JACCARD = 0.5
RECERT_BUCKET_DEG = 0.001  # ~110 m

STATE_NAME_TO_CODE = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "District Of Columbia": "DC", "Florida": "FL",
    "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY",
    "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
    "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN",
    "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
    "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY", "Puerto Rico": "PR",
}

STORAGE_KW_RE = re.compile(r"\b(battery|batteries|storage|bess)\b", re.I)
BATTERY_KW_RE = re.compile(r"\b(battery|batteries|bess)\b", re.I)
HYBRID_DESC_RE = re.compile(
    r"\b(solar|photovoltaic|pv|wind|hybrid|co-?located|paired|aggregat)\b",
    re.I,
)
WITHDRAW_RE = re.compile(r"withdraw", re.I)
HYBRID_FUEL_PREFIXES = ("R-SP", "R-HR", "R-W", "R-ST", "B-", "G-", "F-")

CUTOFF = pd.Timestamp("2023-01-01")

DROP_COLUMNS = [
    "docket_number", "submission_date", "applicant_name", "facility_name",
    "state", "lat", "lng", "capacity_mw", "energy_input", "drop_reason",
]

CANDIDATE_COLUMNS = [
    "id", "type", "name", "operator", "city", "state", "county_name",
    "lat", "lng", "capacity_mw", "status", "source", "source_label",
    "docket_number", "submission_date", "energy_input", "facility_description",
    "co_located", "collision_flag", "collision_with",
]


def norm_state(s):
    if pd.isna(s):
        return None
    s = str(s).strip()
    if not s:
        return None
    if len(s) == 2 and s.isalpha():
        return s.upper()
    return STATE_NAME_TO_CODE.get(s.title())


def slugify(s):
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(s)).strip("-").lower()
    return s or "x"


def elibrary_url(docket):
    return (
        "https://elibrary.ferc.gov/eLibrary/docketsheet?docket_number="
        f"{docket}"
    )


def title_case_name(s):
    if not isinstance(s, str) or not s.strip():
        return ""
    s = s.strip()
    if s.isupper() or s.islower():
        return s.title()
    return s


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    lat1 = np.radians(lat1)
    lon1 = np.radians(lon1)
    lat2 = np.radians(lat2)
    lon2 = np.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def name_tokens(s):
    s = re.sub(r"[^A-Za-z0-9]+", " ", str(s or "")).lower()
    toks = {t for t in s.split() if len(t) >= 3 and t not in {
        "the", "and", "llc", "inc", "corp", "holdings", "project", "facility",
        "energy", "power", "solar", "storage", "farm", "farms", "site", "plant",
    }}
    return toks


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_ferc():
    df = pd.read_csv(RAW_FERC, low_memory=False)
    df["Submission_Date_dt"] = pd.to_datetime(df["Submission_Date"], errors="coerce")
    key = df["Docket_Number"].astype("string")
    key = key.where(
        key.notna() & (key.str.strip() != ""),
        other="SUB:" + df["Submission_ID"].astype(str),
    )
    df["_key"] = key
    df_sorted = df.sort_values("Submission_Date_dt", ascending=False, na_position="last")
    df_latest = df_sorted.drop_duplicates(subset=["_key"], keep="first").copy()
    df_latest["MaxNet_MW"] = pd.to_numeric(df_latest["MaxNetPwr_Capacity"], errors="coerce") / 1000.0
    df_latest["state_code"] = df_latest["Facility_State"].map(norm_state)
    df_latest["lat_f"] = pd.to_numeric(df_latest["Latitude"], errors="coerce")
    df_latest["lng_f"] = pd.to_numeric(df_latest["Longitude"], errors="coerce")
    return df_latest


def is_hybrid(row):
    ei = str(row.get("Energy_Input") or "")
    if any(ei.startswith(p) for p in HYBRID_FUEL_PREFIXES):
        return True, f"fuel_code:{ei.split(' ', 1)[0].strip('-') or ei}"
    desc = str(row.get("Facility_Description") or "")
    m = HYBRID_DESC_RE.search(desc)
    if m:
        return True, f"desc:{m.group(1).lower()}"
    return False, ""


def load_projects():
    with open(PROJECTS_JSON) as f:
        data = json.load(f)
    return data["projects"]


def main():
    counts = {}

    df_latest = load_ferc()
    counts["ferc_latest_rows"] = len(df_latest)

    # Rule 2: storage keyword + filed 2023+
    desc_lower = df_latest["Facility_Description"].astype(str).str.lower()
    storage_mask = desc_lower.str.contains(STORAGE_KW_RE.pattern, regex=True, na=False)
    year_mask = df_latest["Submission_Date_dt"] >= CUTOFF
    pool = df_latest[storage_mask & year_mask].copy()
    counts["pool_storage_keyword_2023plus"] = len(pool)

    drops = []

    # Rule 3: drop "withdraw" text (also catches "withdrawal of QF status")
    wd_mask = pool["Facility_Description"].astype(str).apply(lambda s: bool(WITHDRAW_RE.search(s)))
    for _, r in pool[wd_mask].iterrows():
        drops.append(_drop_row(r, "withdraw_text"))
    pool = pool[~wd_mask].copy()
    counts["after_withdraw_drop"] = len(pool)

    # Rule 4: collapse recerts across docket numbers for same facility
    pool["_bucket"] = list(zip(
        pool["Applicant_Name"].fillna("").astype(str).str.strip().str.lower(),
        pool["state_code"].fillna(""),
        (pool["lat_f"] / RECERT_BUCKET_DEG).round().fillna(-9999),
        (pool["lng_f"] / RECERT_BUCKET_DEG).round().fillna(-9999),
    ))
    pool_sorted = pool.sort_values("Submission_Date_dt", ascending=False, na_position="last")
    keep_mask = ~pool_sorted["_bucket"].duplicated(keep="first")
    superseded = pool_sorted[~keep_mask]
    for _, r in superseded.iterrows():
        drops.append(_drop_row(r, "superseded_by_newer_recert"))
    pool = pool_sorted[keep_mask].copy()
    counts["after_recert_collapse"] = len(pool)

    # Rule 5: hybrid split (by fuel code OR description keyword)
    hybrid_info = pool.apply(is_hybrid, axis=1)
    pool["_hybrid"] = hybrid_info.apply(lambda t: t[0])
    pool["_hybrid_reason"] = hybrid_info.apply(lambda t: t[1])

    hybrids = pool[pool["_hybrid"]].copy()
    standalones = pool[~pool["_hybrid"]].copy()
    counts["hybrids_deferred"] = len(hybrids)
    counts["standalones_pool"] = len(standalones)

    # Write hybrid_defer.csv
    _write_defer(hybrids, OUT_HYBRID)

    # Rule 6: standalones must explicitly name battery/batteries/BESS
    # (the pool keyword "storage" alone catches hydro reservoirs)
    battery_mask = standalones["Facility_Description"].astype(str).apply(
        lambda s: bool(BATTERY_KW_RE.search(s))
    )
    non_battery = standalones[~battery_mask]
    for _, r in non_battery.iterrows():
        drops.append(_drop_row(r, "storage_keyword_but_no_battery_mention"))
    standalones = standalones[battery_mask].copy()
    counts["after_battery_explicit"] = len(standalones)

    # Rule 7: MW floor on MaxNet (treated as storage capacity for standalones)
    mw_ok = standalones["MaxNet_MW"] >= MW_FLOOR
    below = standalones[~mw_ok | standalones["MaxNet_MW"].isna()]
    for _, r in below.iterrows():
        reason = "below_mw_floor" if pd.notna(r["MaxNet_MW"]) else "missing_capacity"
        drops.append(_drop_row(r, reason))
    standalones = standalones[mw_ok].copy()
    counts["after_mw_floor_10"] = len(standalones)

    # Rule 8: lat/lng required
    coords_ok = standalones["lat_f"].notna() & standalones["lng_f"].notna() & \
        (standalones["lat_f"] != 0) & (standalones["lng_f"] != 0)
    no_coords = standalones[~coords_ok]
    _write_defer(no_coords, OUT_NOCOORDS)
    counts["no_coords_deferred"] = len(no_coords)
    standalones = standalones[coords_ok].copy()
    counts["after_coords"] = len(standalones)

    # Rule 9: collision check vs ALL 1,608 projects
    projects = load_projects()
    counts["projects_in_json"] = len(projects)
    collisions = []
    collision_flags = {}  # idx -> list of (project_id, reason, distance_km_or_jaccard)
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

    for idx, row in standalones.iterrows():
        hits = []
        # proximity (any state)
        lat_arr = proj_df["lat"].to_numpy(float)
        lng_arr = proj_df["lng"].to_numpy(float)
        dists = haversine_km(row["lat_f"], row["lng_f"], lat_arr, lng_arr)
        near = np.where(dists <= COLLISION_KM)[0]
        for i in near:
            hits.append((proj_df.iloc[i]["id"], f"proximity_{dists[i]:.2f}km"))
        # same-state name jaccard
        if row["state_code"]:
            same_state = proj_df[proj_df["state"] == row["state_code"]]
            ferc_toks = name_tokens(row.get("Facility_Name"))
            for _, prow in same_state.iterrows():
                j = jaccard(ferc_toks, prow["tokens"])
                if j >= COLLISION_JACCARD:
                    hits.append((prow["id"], f"name_jaccard_{j:.2f}"))
        if hits:
            collision_flags[idx] = hits
            for pid, reason in hits:
                collisions.append({
                    "ferc_docket": row.get("Docket_Number") or f"SUB:{row.get('Submission_ID')}",
                    "ferc_facility": row.get("Facility_Name"),
                    "ferc_applicant": row.get("Applicant_Name"),
                    "ferc_state": row["state_code"],
                    "ferc_capacity_mw": row["MaxNet_MW"],
                    "project_id": pid,
                    "reason": reason,
                })

    counts["candidates_with_collisions"] = len(collision_flags)
    counts["candidates_clean"] = len(standalones) - len(collision_flags)

    # Build candidate rows
    candidates = []
    for idx, row in standalones.iterrows():
        docket = row.get("Docket_Number") or f"SUB{row.get('Submission_ID')}"
        docket = str(docket).strip()
        collision_hits = collision_flags.get(idx, [])
        candidates.append({
            "id": f"bess-ferc-{slugify(docket)}",
            "type": "bess",
            "name": title_case_name(row.get("Facility_Name")) or f"FERC {docket}",
            "operator": title_case_name(row.get("Applicant_Name")),
            "city": title_case_name(row.get("Facility_City")),
            "state": row["state_code"] or "",
            "county_name": "",  # add-county.mjs will fill on merge
            "lat": round(float(row["lat_f"]), 5),
            "lng": round(float(row["lng_f"]), 5),
            "capacity_mw": round(float(row["MaxNet_MW"]), 2),
            "status": "planned",
            "source": elibrary_url(docket),
            "source_label": "FERC Form 556",
            "docket_number": docket,
            "submission_date": row["Submission_Date_dt"].date().isoformat() if pd.notna(row["Submission_Date_dt"]) else "",
            "energy_input": row.get("Energy_Input") or "",
            "facility_description": (str(row.get("Facility_Description") or "")[:500]).replace("\n", " ").replace("\r", " "),
            "co_located": False,
            "collision_flag": bool(collision_hits),
            "collision_with": "; ".join(f"{pid} ({reason})" for pid, reason in collision_hits),
        })

    candidates.sort(key=lambda r: r["capacity_mw"], reverse=True)

    # Write outputs
    _write_rows(OUT_CANDIDATES, CANDIDATE_COLUMNS, candidates)
    _write_rows(OUT_DROPS, DROP_COLUMNS, drops)
    with open(OUT_COLLISIONS, "w", newline="") as f:
        if collisions:
            w = csv.DictWriter(f, fieldnames=list(collisions[0].keys()))
            w.writeheader()
            w.writerows(collisions)
        else:
            f.write("ferc_docket,ferc_facility,ferc_applicant,ferc_state,ferc_capacity_mw,project_id,reason\n")
    _write_rows(OUT_TOP25, CANDIDATE_COLUMNS, candidates[:25])

    _report(counts, candidates, drops, hybrids, collisions)


def _drop_row(r, reason):
    return {
        "docket_number": r.get("Docket_Number") or f"SUB{r.get('Submission_ID')}",
        "submission_date": r["Submission_Date_dt"].date().isoformat() if pd.notna(r["Submission_Date_dt"]) else "",
        "applicant_name": r.get("Applicant_Name"),
        "facility_name": r.get("Facility_Name"),
        "state": r.get("state_code"),
        "lat": r.get("lat_f"),
        "lng": r.get("lng_f"),
        "capacity_mw": r.get("MaxNet_MW"),
        "energy_input": r.get("Energy_Input"),
        "drop_reason": reason,
    }


def _write_defer(df, path):
    if df is None or df.empty:
        with open(path, "w", newline="") as f:
            f.write("docket_number,submission_date,applicant_name,facility_name,state,lat,lng,capacity_mw,energy_input,hybrid_reason,facility_description\n")
        return
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "docket_number": r.get("Docket_Number") or f"SUB{r.get('Submission_ID')}",
            "submission_date": r["Submission_Date_dt"].date().isoformat() if pd.notna(r["Submission_Date_dt"]) else "",
            "applicant_name": r.get("Applicant_Name"),
            "facility_name": r.get("Facility_Name"),
            "state": r.get("state_code"),
            "lat": r.get("lat_f"),
            "lng": r.get("lng_f"),
            "capacity_mw": r.get("MaxNet_MW"),
            "energy_input": r.get("Energy_Input"),
            "hybrid_reason": r.get("_hybrid_reason", ""),
            "facility_description": (str(r.get("Facility_Description") or "")[:500]).replace("\n", " ").replace("\r", " "),
        })
    _write_rows(path, list(rows[0].keys()), rows)


def _write_rows(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _report(counts, candidates, drops, hybrids, collisions):
    print()
    print("=" * 72)
    print("FUNNEL")
    print("=" * 72)
    for k, v in counts.items():
        print(f"  {k:40s} : {v:>6}")

    print()
    print("=" * 72)
    print("STATUS SPLIT (candidates)")
    print("=" * 72)
    statuses = {}
    for c in candidates:
        statuses[c["status"]] = statuses.get(c["status"], 0) + 1
    for s, n in statuses.items():
        print(f"  {s:20s} : {n}")
    print("  (always 'planned' -- Form 556 has no in-service date column)")

    print()
    print("=" * 72)
    print("HYBRID FINDING")
    print("=" * 72)
    print(f"  Hybrid rows deferred: {len(hybrids):,}")
    if len(hybrids):
        reasons = hybrids["_hybrid_reason"].value_counts()
        for r, n in reasons.items():
            print(f"    {r:30s} : {n}")

    print()
    print("=" * 72)
    print(f"TOP {min(25, len(candidates))} CANDIDATES (by capacity_mw)")
    print("=" * 72)
    for c in candidates[:25]:
        flag = " [COLLISION]" if c["collision_flag"] else ""
        print(f"  {c['capacity_mw']:7.2f} MW  {c['state']}  {c['name'][:50]:50s}  {c['docket_number']}{flag}")
        print(f"    {c['source']}")
        if c["collision_flag"]:
            print(f"    collides with: {c['collision_with']}")

    print()
    print("=" * 72)
    print("COLLISION COUNT")
    print("=" * 72)
    print(f"  Candidates flagged: {sum(1 for c in candidates if c['collision_flag'])}")
    print(f"  Total collision pairs: {len(collisions)}")

    print()
    print("Wrote:")
    for p in (OUT_CANDIDATES, OUT_TOP25, OUT_DROPS, OUT_HYBRID, OUT_NOCOORDS, OUT_COLLISIONS):
        print(f"  {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
