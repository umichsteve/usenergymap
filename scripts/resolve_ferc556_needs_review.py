"""
Resolve the 12 pre-2022 FERC Form 556 needs_review hybrid dockets against EIA-860M.

For each docket, look for an EIA plant within 3 km OR with a same-state fuzzy
name match. Classify:
  - EIA battery generator at that plant already in projects.json  -> duplicate (skip)
  - EIA battery generator operating but NOT in projects.json       -> add as operating
  - EIA plant exists but no battery generator                      -> skip (no_eia_battery)
  - No EIA match                                                   -> skip (no_eia_match)

Writes ferc556_needs_review_resolution.csv + a text report.
Does not modify projects.json directly; the add-operating rows are also written
back into the batch 2 candidates stream for the merge step.
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
    haversine_km,
    jaccard,
    name_tokens,
    slugify,
    title_case_name,
    elibrary_url,
    clean_display_name,
)

CANDIDATES = ROOT / "data/contacts/ferc556_hybrid_candidates.csv"
PROJECTS_JSON = ROOT / "projects.json"
EIA_XLSX = ROOT / ".cache/september_generator2026.xlsx"
OUT_REPORT = ROOT / "data/contacts/ferc556_needs_review_resolution.csv"

PROX_KM = 3.0
NAME_JACCARD = 0.5


def load_needs_review():
    with open(CANDIDATES) as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r["status"] == "needs_review"]


def load_eia_all():
    """Load EIA-860M operating + planned + proposed generators for ALL technologies.

    Returns a DataFrame with plant-level attributes, keeping battery vs non-battery
    separately flagged so we can classify "plant exists, no battery".
    """
    sheets = {}
    # Order matters downstream: Operating > Planned > Canceled or Postponed > Retired
    for sh in ("Operating", "Planned", "Canceled or Postponed", "Retired"):
        try:
            sheets[sh] = pd.read_excel(EIA_XLSX, sheet_name=sh, header=2)
        except Exception:
            sheets[sh] = pd.DataFrame()

    frames = []
    for sheet_name, df in sheets.items():
        if df.empty:
            continue
        df = df.copy()
        df["_sheet"] = sheet_name
        # Column variants across sheets
        if "Plant ID" not in df.columns:
            continue
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    # Normalize key cols
    for col in ("Latitude", "Longitude"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    df["Plant ID"] = pd.to_numeric(df["Plant ID"], errors="coerce").astype("Int64")
    df["Technology"] = df["Technology"].astype(str).fillna("")
    df["Plant State"] = df["Plant State"].astype(str).fillna("")
    df["Plant Name"] = df["Plant Name"].astype(str).fillna("")

    # Aggregate at plant level: list of generator rows per plant
    return df


def plant_summary(gdf: pd.DataFrame) -> pd.DataFrame:
    """One row per (Plant ID, Plant Name, State). Tracks whether any battery
    generator exists at the plant, the max battery MW/MWh across sheets, and the
    plant status (prefer operating battery, else planned/proposed battery)."""
    gdf = gdf.copy()
    gdf["_is_batt"] = gdf["Technology"].str.lower().eq("batteries")
    rows = []
    for pid, grp in gdf.groupby("Plant ID"):
        lats = grp["Latitude"].dropna()
        lngs = grp["Longitude"].dropna()
        if lats.empty or lngs.empty:
            continue
        name = str(grp["Plant Name"].iloc[0])
        state = str(grp["Plant State"].iloc[0])
        batt = grp[grp["_is_batt"]]
        has_batt = not batt.empty
        batt_mw = None
        batt_mwh = None
        batt_status = None
        if has_batt:
            if (batt["_sheet"] == "Operating").any():
                batt_status = "operating"
                b = batt[batt["_sheet"] == "Operating"]
            elif (batt["_sheet"] == "Planned").any():
                batt_status = "planned"
                b = batt[batt["_sheet"] == "Planned"]
            elif (batt["_sheet"] == "Canceled or Postponed").any():
                batt_status = "canceled"
                b = batt[batt["_sheet"] == "Canceled or Postponed"]
            else:
                batt_status = "retired"
                b = batt
            if "Nameplate Capacity (MW)" in b.columns:
                batt_mw = pd.to_numeric(b["Nameplate Capacity (MW)"], errors="coerce").sum(min_count=1)
            if "Nameplate Energy Capacity (MWh)" in b.columns:
                batt_mwh = pd.to_numeric(b["Nameplate Energy Capacity (MWh)"], errors="coerce").sum(min_count=1)

        rows.append({
            "plant_id": int(pid) if pd.notna(pid) else None,
            "plant_name": name,
            "state": state,
            "lat": float(lats.iloc[0]),
            "lng": float(lngs.iloc[0]),
            "county": str(grp["County"].iloc[0]) if "County" in grp.columns and pd.notna(grp["County"].iloc[0]) else "",
            "operator": str(grp["Entity Name"].iloc[0]) if "Entity Name" in grp.columns else "",
            "has_batt": bool(has_batt),
            "batt_mw": float(batt_mw) if batt_mw and batt_mw > 0 else None,
            "batt_mwh": float(batt_mwh) if batt_mwh and batt_mwh > 0 else None,
            "batt_status": batt_status,
            "tech_summary": ", ".join(sorted(set(grp["Technology"].dropna()))),
        })
    return pd.DataFrame(rows)


def load_projects_eia_ids():
    with open(PROJECTS_JSON) as f:
        data = json.load(f)
    ids = {p["id"] for p in data["projects"]}
    return ids


def classify():
    nr = load_needs_review()
    print(f"[resolve] 12 needs_review dockets -> {len(nr)} rows")

    gdf = load_eia_all()
    plants = plant_summary(gdf)
    print(f"[resolve] EIA plants (any tech): {len(plants)}, with battery: "
          f"{plants['has_batt'].sum()}")

    proj_ids = load_projects_eia_ids()

    out_rows = []
    add_rows = []
    for r in nr:
        docket = r["docket_number"]
        try:
            lat = float(r["lat"])
            lng = float(r["lng"])
        except ValueError:
            lat = lng = None
        state = r["state"]
        ferc_toks = name_tokens(r["name"])

        # Candidate pool: same state
        pool = plants[plants["state"] == state].copy()
        if lat is not None:
            pool["dist_km"] = haversine_km(lat, lng, pool["lat"].to_numpy(), pool["lng"].to_numpy())
        else:
            pool["dist_km"] = np.nan
        pool["jac"] = pool["plant_name"].apply(lambda s: jaccard(ferc_toks, name_tokens(s)))

        # Match rules:
        #   proximity <= 3 km OR name-jaccard >= 0.5 AND proximity <= 25 km
        # Distant same-name plants are usually unrelated facilities, so we gate
        # name matches on a soft distance budget.
        NAME_MATCH_MAX_KM = 25.0
        match = pool[
            (pool["dist_km"] <= PROX_KM)
            | ((pool["jac"] >= NAME_JACCARD) & (pool["dist_km"] <= NAME_MATCH_MAX_KM))
        ].copy()
        match = match.sort_values(["dist_km", "jac"], ascending=[True, False])

        classification = "no_eia_match"
        reason = ""
        chosen = None
        if not match.empty:
            chosen = match.iloc[0]
            eia_id = f"eia-{int(chosen['plant_id'])}"
            if chosen["has_batt"]:
                if eia_id in proj_ids:
                    classification = "duplicate_in_projects"
                    reason = "EIA battery already a projects.json row"
                elif chosen["batt_status"] == "operating":
                    classification = "add_operating"
                    reason = "EIA operating battery not in projects.json"
                elif chosen["batt_status"] == "canceled":
                    classification = "eia_battery_canceled"
                    reason = "EIA battery filing was canceled or postponed"
                else:
                    classification = "eia_battery_planned_not_in_projects"
                    reason = f"EIA battery status={chosen['batt_status']}, not operating"
            else:
                classification = "no_eia_battery"
                reason = f"EIA plant exists ({chosen['tech_summary']}) but battery not reported"
        else:
            classification = "no_eia_match"
            reason = "status unconfirmed"

        rec = {
            "docket": docket,
            "ferc_facility": r["name"],
            "state": state,
            "ferc_storage_mw": r["capacity_mw"],
            "ferc_storage_mwh": r["capacity_mwh"],
            "eia_plant_id": int(chosen["plant_id"]) if chosen is not None else "",
            "eia_plant_name": chosen["plant_name"] if chosen is not None else "",
            "eia_dist_km": f"{chosen['dist_km']:.2f}" if chosen is not None and pd.notna(chosen["dist_km"]) else "",
            "eia_name_jaccard": f"{chosen['jac']:.2f}" if chosen is not None else "",
            "eia_has_battery": bool(chosen["has_batt"]) if chosen is not None else False,
            "eia_batt_status": chosen["batt_status"] if chosen is not None else "",
            "eia_batt_mw": chosen["batt_mw"] if chosen is not None else "",
            "eia_batt_mwh": chosen["batt_mwh"] if chosen is not None else "",
            "eia_tech_summary": chosen["tech_summary"] if chosen is not None else "",
            "classification": classification,
            "reason": reason,
        }
        out_rows.append(rec)

        # Build add-operating row for merge if classification demands it
        if classification == "add_operating":
            eia_id = f"eia-{int(chosen['plant_id'])}"
            # Use EIA battery nameplate, not Form 556
            cap_mw = chosen["batt_mw"]
            cap_mwh = chosen["batt_mwh"]
            add_row = {
                "id": eia_id,
                "type": "bess",
                "name": str(chosen["plant_name"]).strip(),
                "operator": str(chosen["operator"]).strip(),
                "city": str(chosen["county"]).strip() or state,
                "state": state,
                "lat": round(float(chosen["lat"]), 4),
                "lng": round(float(chosen["lng"]), 4),
                "capacity_mw": int(round(cap_mw)) if cap_mw and cap_mw > 0 else None,
                "status": "operating",
                "source": "https://www.eia.gov/electricity/data/eia860m/",
                "ferc_docket": elibrary_url(docket),
            }
            if cap_mwh and cap_mwh > 0:
                add_row["capacity_mwh"] = int(round(cap_mwh))
            add_rows.append(add_row)

    with open(OUT_REPORT, "w", newline="") as f:
        if out_rows:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)

    # Print table
    print()
    print("=" * 110)
    print(f"{'DOCKET':15s} {'STATE':5s} {'CLASS':34s} {'EIA PLANT':30s} {'DIST':6s} {'STATUS':10s}")
    print("=" * 110)
    for r in out_rows:
        eia = f"{r['eia_plant_id']} {r['eia_plant_name'][:22]}" if r['eia_plant_id'] != "" else "-"
        bs = r["eia_batt_status"] if r["eia_batt_status"] not in (None, "") else ""
        bs = str(bs) if not (isinstance(bs, float) and pd.isna(bs)) else ""
        print(f"{r['docket']:15s} {r['state']:5s} {r['classification']:34s} "
              f"{eia:30s} {str(r['eia_dist_km']):>6s} {bs:10s}")
        print(f"   note: {r['reason']}")

    print()
    print(f"[resolve] classification counts:")
    from collections import Counter
    for k, v in Counter(r["classification"] for r in out_rows).items():
        print(f"  {k}: {v}")
    print(f"[resolve] add_operating rows prepared for merge: {len(add_rows)}")
    print(f"[resolve] wrote {OUT_REPORT.relative_to(ROOT)}")

    return out_rows, add_rows


if __name__ == "__main__":
    classify()
