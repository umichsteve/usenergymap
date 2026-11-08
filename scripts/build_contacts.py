"""
build_contacts.py — Phase A: FERC Form 556 → projects.json match.

Reads the already-downloaded FERC Form 556 extracts in data/contacts/raw/,
collapses ds=35 to the latest submission per docket, and matches each candidate
BESS project (type=bess, status in {planned, under_construction, announced},
capacity_mw<=80) to a FERC filing by:

  1. Spatial: same state, Latitude/Longitude within 2 km of the project.
     Tie-break by closeness of MaxNetPwr_Capacity (converted kW->MW) to the
     project's capacity_mw.

  2. Name (fallback): same state, capacity within +/-20% of the project
     capacity, and a fuzzy token-set match score on Facility_Name,
     Applicant_Name, or any Direct_Owner_Name (ds=36). For hybrid candidates
     (name contains "Solar"/"Hybrid", or operator ends in "Solar LLC") the
     capacity window is dropped since the QF filing covers the solar side only.

Writes data/contacts/ferc556_matches.csv and prints a report. No network.
No projects.json writes. No PDF or eLibrary fetching.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_JSON = ROOT / "projects.json"
RAW_FERC = ROOT / "data/contacts/raw/ferc_form556.csv"
RAW_OWNERS = ROOT / "data/contacts/raw/ferc_form556_direct_owners.csv"
OUT_CSV = ROOT / "data/contacts/ferc556_matches.csv"

NAME_SCORE_THRESHOLD = 80  # 0-100, rapidfuzz token_set_ratio
SPATIAL_MAX_KM = 2.0
CAPACITY_WINDOW = 0.20  # +/-20 %

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


def norm_state(s) -> str | None:
    if pd.isna(s):
        return None
    s = str(s).strip()
    if not s:
        return None
    if len(s) == 2 and s.isalpha():
        return s.upper()
    return STATE_NAME_TO_CODE.get(s.title())


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    lat1 = np.radians(lat1)
    lon1 = np.radians(lon1)
    lat2 = np.radians(lat2)
    lon2 = np.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


HYBRID_HINT_RE = re.compile(r"\b(hybrid|solar)\b", re.IGNORECASE)
SOLAR_LLC_RE = re.compile(r"\bsolar\s+llc\.?$", re.IGNORECASE)


def is_hybrid(name: str | None, operator: str | None) -> bool:
    if name and HYBRID_HINT_RE.search(name):
        return True
    if operator and (HYBRID_HINT_RE.search(operator) or SOLAR_LLC_RE.search(operator.strip())):
        return True
    return False


def load_ferc() -> pd.DataFrame:
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
    return df_latest


def load_owners_map() -> dict[int, str]:
    df = pd.read_csv(RAW_OWNERS, low_memory=False)
    df = df.dropna(subset=["Form556_ID", "Direct_Owner_Name"])
    df["Direct_Owner_Name"] = df["Direct_Owner_Name"].astype(str).str.strip()
    df = df[df["Direct_Owner_Name"].astype(bool)]
    grouped = (
        df.sort_values(["Form556_ID", "Sequence"], na_position="last")
        .groupby("Form556_ID")["Direct_Owner_Name"]
        .apply(lambda s: "; ".join(dict.fromkeys(s)))
    )
    return grouped.to_dict()


def load_candidates() -> list[dict]:
    with open(PROJECTS_JSON) as f:
        data = json.load(f)
    projects = data["projects"]
    cands = [
        p for p in projects
        if p.get("type") == "bess"
        and p.get("status") in ("planned", "under_construction", "announced")
        and (p.get("capacity_mw") or 0) <= 80
    ]
    return cands


def match_spatial(cand: dict, df_latest: pd.DataFrame):
    state = cand.get("state")
    if not state:
        return None
    sub = df_latest[
        (df_latest["state_code"] == state)
        & df_latest["Latitude"].notna()
        & df_latest["Longitude"].notna()
    ]
    if sub.empty:
        return None
    lat = float(cand["lat"])
    lng = float(cand["lng"])
    dists = haversine_km(lat, lng, sub["Latitude"].to_numpy(float), sub["Longitude"].to_numpy(float))
    mask = dists <= SPATIAL_MAX_KM
    if not mask.any():
        return None
    hits = sub.loc[mask].copy()
    hits["_dist_km"] = dists[mask]
    cap = float(cand.get("capacity_mw") or 0)
    hits["_capdiff"] = (hits["MaxNet_MW"].fillna(np.inf) - cap).abs()
    hits = hits.sort_values(["_dist_km", "_capdiff"])
    row = hits.iloc[0]
    return row, "spatial", round(float(row["_dist_km"]), 3)


def _fuzzy_score(needle: str, haystacks: list[str]) -> int:
    if not needle:
        return 0
    best = 0
    for h in haystacks:
        if not h:
            continue
        s = fuzz.token_set_ratio(needle, h)
        if s > best:
            best = s
    return best


def match_name(cand: dict, df_latest: pd.DataFrame, owners_by_id: dict[int, str]):
    state = cand.get("state")
    if not state:
        return None
    sub = df_latest[df_latest["state_code"] == state]
    if sub.empty:
        return None
    cap = float(cand.get("capacity_mw") or 0)
    hybrid = is_hybrid(cand.get("name"), cand.get("operator"))
    if not hybrid and cap > 0:
        sub = sub[
            sub["MaxNet_MW"].notna()
            & (sub["MaxNet_MW"] >= cap * (1 - CAPACITY_WINDOW))
            & (sub["MaxNet_MW"] <= cap * (1 + CAPACITY_WINDOW))
        ]
    if sub.empty:
        return None

    name = (cand.get("name") or "").strip()
    operator = (cand.get("operator") or "").strip()

    best_row = None
    best_score = 0
    for _, row in sub.iterrows():
        haystacks: list[str] = []
        fac = row.get("Facility_Name")
        app = row.get("Applicant_Name")
        if isinstance(fac, str):
            haystacks.append(fac)
        if isinstance(app, str):
            haystacks.append(app)
        own = owners_by_id.get(row["Form556_ID"])
        if own:
            haystacks.extend(own.split("; "))
        score = max(_fuzzy_score(name, haystacks), _fuzzy_score(operator, haystacks))
        if score > best_score:
            best_score = score
            best_row = row
    if best_row is None or best_score < NAME_SCORE_THRESHOLD:
        return None
    return best_row, "name", round(best_score / 100.0, 3)


OUT_COLUMNS = [
    "project_id", "project_name", "operator",
    "docket_number", "latest_submission_date",
    "facility_name", "applicant_name", "applicant_phone",
    "applicant_city", "applicant_state",
    "contact_type", "contact_name", "contact_phone",
    "contact_organization_name",
    "direct_owners",
    "match_method", "match_score",
]


def _str(row, col) -> str:
    v = row.get(col)
    return v if isinstance(v, str) else ""


def build_row(cand: dict, hit, owners_by_id: dict[int, str]) -> dict:
    row, method, score = hit
    submission = row.get("Submission_Date_dt")
    sub_str = submission.date().isoformat() if pd.notna(submission) else ""
    return {
        "project_id": cand.get("id"),
        "project_name": cand.get("name"),
        "operator": cand.get("operator"),
        "docket_number": _str(row, "Docket_Number"),
        "latest_submission_date": sub_str,
        "facility_name": _str(row, "Facility_Name"),
        "applicant_name": _str(row, "Applicant_Name"),
        "applicant_phone": _str(row, "Applicant_Phone"),
        "applicant_city": _str(row, "Applicant_City"),
        "applicant_state": _str(row, "Applicant_State"),
        "contact_type": _str(row, "Contact_Type"),
        "contact_name": _str(row, "Contact_Name"),
        "contact_phone": _str(row, "Contact_Phone"),
        "contact_organization_name": _str(row, "Contact_Organization_Name"),
        "direct_owners": owners_by_id.get(row["Form556_ID"], ""),
        "match_method": method,
        "match_score": score,
    }


def main() -> None:
    print(f"[load] ferc       : {RAW_FERC.relative_to(ROOT)}")
    df_latest = load_ferc()
    print(f"[load]   collapsed: {len(df_latest):,} unique dockets (incl. SUB fallback)")
    owners_by_id = load_owners_map()
    print(f"[load]   owner map: {len(owners_by_id):,} Form556_IDs with Direct_Owner_Name")

    cands = load_candidates()
    print(f"[load] candidates : {len(cands)} BESS projects")

    matches: list[dict] = []
    matched_keys: set[str] = set()
    for cand in cands:
        hit = match_spatial(cand, df_latest)
        if hit is None:
            hit = match_name(cand, df_latest, owners_by_id)
        if hit is None:
            continue
        matches.append(build_row(cand, hit, owners_by_id))
        matched_keys.add(str(hit[0]["_key"]))

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        w.writeheader()
        for r in matches:
            w.writerow(r)
    print(f"[write] {OUT_CSV.relative_to(ROOT)}  ({len(matches)} rows)")

    report(matches, cands, df_latest, matched_keys)


def report(matches, cands, df_latest, matched_keys) -> None:
    total = len(cands)
    n_matches = len(matches)
    by_method: dict[str, int] = {}
    by_ctype: dict[str, int] = {}
    for m in matches:
        by_method[m["match_method"]] = by_method.get(m["match_method"], 0) + 1
        c = m["contact_type"] or "(blank)"
        by_ctype[c] = by_ctype.get(c, 0) + 1

    sellable = sum(1 for m in matches if m["contact_type"] in ("Applicant Employee", "Self"))

    print()
    print("=" * 72)
    print(f"MATCHES: {n_matches} / {total} candidates")
    for method, n in sorted(by_method.items()):
        print(f"  {method:8s} : {n}")

    print()
    print("CONTACT TYPE SPLIT (among matches):")
    for ctype, n in sorted(by_ctype.items(), key=lambda kv: -kv[1]):
        print(f"  {ctype:20s} : {n}")
    print(f"\nSELLABLE (Applicant Employee + Self): {sellable}")

    print()
    print("SAMPLE (up to 10, mixed methods):")
    spatial = [m for m in matches if m["match_method"] == "spatial"]
    name = [m for m in matches if m["match_method"] == "name"]
    k_name = min(5, len(name))
    k_spatial = min(10 - k_name, len(spatial))
    sample = spatial[:k_spatial] + name[:k_name]
    # top up if still short
    if len(sample) < 10:
        remaining = [m for m in matches if m not in sample]
        sample.extend(remaining[: 10 - len(sample)])
    for m in sample:
        print(
            f"  [{m['match_method']}:{m['match_score']}] {m['project_id']} "
            f"- {m['project_name']!r} / {m['operator']!r}  ->  "
            f"{m['docket_number'] or '(no docket)'}  "
            f"{m['facility_name']!r}  "
            f"contact={m['contact_name']!r} ({m['contact_type']})"
        )

    desc = df_latest["Facility_Description"].astype(str).str.lower()
    storage_mask = desc.str.contains(r"\b(?:battery|storage|bess)\b", regex=True, na=False)
    year_mask = df_latest["Submission_Date_dt"].dt.year >= 2023
    storage_2023 = df_latest[storage_mask & year_mask]
    unmatched_storage = storage_2023[~storage_2023["_key"].astype(str).isin(matched_keys)]
    print()
    print(
        f"UNMATCHED storage/battery/BESS dockets filed 2023+ "
        f"(not in matches): {len(unmatched_storage):,}"
    )


if __name__ == "__main__":
    main()
