"""
ingest_ferc556_hybrid.py -- Phase 2: co-located BESS from FERC Form 556 hybrids.

Phase 1 (ingest_ferc556_storage.py) deferred 700 rows as hybrids because Form
556 reports the *generation*-side MaxNetPwr_Capacity, which is overwhelmingly
the solar nameplate. For these rows, the BESS sizing has to be read out of
Facility_Description text. Regex alone picks the wrong number (solar MW)
when the description mentions multiple MWs -- so this parser requires either

  (A) an MW+MWh pair where the gap between them is <= 60 chars AND a battery
      term (battery|BESS|lithium|li-ion|ESS|energy storage) is within the
      60-char window around the pair AND MWh > MW (sanity check for duration);
  OR
  (B) an MW token with a battery term within 25 chars AND no solar term closer
      than that battery term. MWh stays blank in this case (no inference).

Nothing else sets storage_mw. If neither pattern matches, the row is logged
with drop_reason=no_bess_sizing. Evidence phrase (<= 25 words from the
window around the chosen MW) ships in the output so the reviewer can verify.

Funnel:
  1. hybrid_defer pool -> capacity_mw in [20, 1000] (excludes QF24-897 corrupt
     418,496 MW entry).
  2. Must have battery/BESS/lithium/ESS term; description "storage" that only
     refers to thermal/cold/gas/fuel/water is dropped.
  3. Parse BESS MW/MWh/coupling/evidence per rules above.
  4. storage_mw >= 10.
  5. Collision check vs ALL 1,608 projects.json rows (1 km proximity OR
     same-state name-token Jaccard >= 0.5).
  6. Status: docket year prefix. QF22+ -> planned. Older -> needs_review
     (not in top-25, not merged).

Writes ferc556_hybrid_candidates.csv, ferc556_hybrid_drops.csv,
ferc556_hybrid_collisions.csv, ferc556_hybrid_top25.csv. No projects.json
writes. county_fips is left blank; scripts/add-county.mjs fills it on merge.
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
HYBRID_DEFER = ROOT / "data/contacts/ferc556_hybrid_defer.csv"
RAW_FERC = ROOT / "data/contacts/raw/ferc_form556.csv"

OUT_CANDIDATES = ROOT / "data/contacts/ferc556_hybrid_candidates.csv"
OUT_DROPS = ROOT / "data/contacts/ferc556_hybrid_drops.csv"
OUT_COLLISIONS = ROOT / "data/contacts/ferc556_hybrid_collisions.csv"
OUT_TOP25 = ROOT / "data/contacts/ferc556_hybrid_top25.csv"

MW_FLOOR = 10.0
CAP_BAND = (20.0, 1000.0)
COLLISION_KM = 1.0
COLLISION_JACCARD = 0.5

PAIR_GAP_MAX = 60      # chars between MW and MWh
LONE_MW_WIN = 50       # chars: battery term must be this close to lone MW
EVIDENCE_WORDS = 25

NUM_MW_RE = re.compile(
    r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*(?:-\s*)?(MW|kW)(?!h)"
    r"(?:\s*[-/]?\s*(AC|DC))?",
    re.I,
)
NUM_MWH_RE = re.compile(
    r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*(?:-\s*)?(MWh|kWh)",
    re.I,
)
BATTERY_RE = re.compile(
    r"\b(battery|batteries|bess|lithium|li-?ion|\bESS\b|energy\s+storage)\b",
    re.I,
)
SOLAR_RE = re.compile(
    r"\b(solar|photovoltaic|\bPV\b|modules?|inverter|array)\b",
    re.I,
)
NON_BATTERY_STORAGE_RE = re.compile(
    r"\b(thermal|cold|chilled|water|gas|fuel|propane|hydrogen|LNG|LPG|CNG)\s+"
    r"storage\b",
    re.I,
)
NEGATION_RE = re.compile(
    r"\b(no|without|not\s+have|does\s+not\s+have|no\s+battery\s+backup)\s+"
    r"(a\s+)?(battery|batteries|bess|energy\s+storage)",
    re.I,
)
DOCKET_YEAR_RE = re.compile(r"^QF(\d{2})-", re.I)
COUPLING_RE = re.compile(
    r"\b(AC[-\s]?coupled|DC[-\s]?coupled|AC[-\s]?coupling|DC[-\s]?coupling)\b",
    re.I,
)
# Legal-suffix strip for display name (operator keeps full legal name)
LEGAL_SUFFIX_RE = re.compile(
    r",?\s+(L\.?L\.?C\.?|Inc\.?|LP|L\.P\.|Corp(?:oration)?|Ltd\.?|Co\.?)\s*$",
    re.I,
)
# Duration patterns for MWh inference: "4-hour", "4 hour", "4 hours of storage",
# "4-hr duration", etc. Hours must be in [0.25, 12] to be a plausible BESS duration.
DURATION_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*[-\s]?(?:hour|hr)s?\b",
    re.I,
)
DURATION_MAX_DIST = 100  # chars: duration token must be this close to a battery term


def _num(s):
    return float(str(s).replace(",", ""))


def _mw_from(val, unit):
    return val / 1000.0 if unit.lower() == "kw" else val


def _mwh_from(val, unit):
    return val / 1000.0 if unit.lower() == "kwh" else val


def extract_bess(desc):
    """Return dict(storage_mw, storage_mwh, coupling, evidence, strategy)."""
    desc = str(desc or "")
    blank = {
        "storage_mw": None, "storage_mwh": None,
        "coupling": "unknown", "evidence": "", "strategy": "none",
    }
    if not desc.strip():
        return blank

    # Negation guard: if the only battery mention is in negation form, drop
    battery_hits = list(BATTERY_RE.finditer(desc))
    if not battery_hits:
        return blank
    negated = list(NEGATION_RE.finditer(desc))
    if negated and len(negated) >= len(battery_hits):
        return {**blank, "strategy": "negated"}

    mw_toks = []
    for m in NUM_MW_RE.finditer(desc):
        try:
            val = _num(m.group(1))
        except ValueError:
            continue
        mw_toks.append({
            "start": m.start(), "end": m.end(),
            "val": _mw_from(val, m.group(2)),
            "ac_dc": (m.group(3) or "").upper(),
            "raw": m.group(0),
        })
    mwh_toks = []
    for m in NUM_MWH_RE.finditer(desc):
        try:
            val = _num(m.group(1))
        except ValueError:
            continue
        mwh_toks.append({
            "start": m.start(), "end": m.end(),
            "val": _mwh_from(val, m.group(2)),
            "raw": m.group(0),
        })

    # Strategy A: MW+MWh paired, battery nearby, MWh > MW
    pairs = []
    for mw in mw_toks:
        for mwh in mwh_toks:
            gap = abs(mw["start"] - mwh["start"])
            if gap > PAIR_GAP_MAX:
                continue
            if mwh["val"] <= mw["val"]:
                # Not a plausible storage duration >= 1 hr
                continue
            lo = max(0, min(mw["start"], mwh["start"]) - 60)
            hi = max(mw["end"], mwh["end"]) + 60
            ctx = desc[lo:hi]
            if not BATTERY_RE.search(ctx):
                continue
            # Must NOT be dominated by solar wording -- if solar term is
            # between MW and MWh, likely a mixed-mention
            inner_lo = min(mw["end"], mwh["end"])
            inner_hi = max(mw["start"], mwh["start"])
            inner = desc[inner_lo:inner_hi] if inner_hi > inner_lo else ""
            if SOLAR_RE.search(inner):
                continue
            pairs.append({"mw": mw, "mwh": mwh, "gap": gap, "lo": lo, "hi": hi})

    if pairs:
        pairs.sort(key=lambda p: (p["gap"], p["mw"]["val"]))
        best = pairs[0]
        ctx = desc[best["lo"]:best["hi"]]
        evidence = _trim_evidence(ctx)
        coupling = _detect_coupling(ctx, best["mw"]["ac_dc"])
        return {
            "storage_mw": round(best["mw"]["val"], 3),
            "storage_mwh": round(best["mwh"]["val"], 2),
            "coupling": coupling,
            "evidence": evidence,
            "strategy": "A_pair",
        }

    # Strategy B: lone MW with battery term within LONE_MW_WIN chars AND
    # closer than any solar term
    scored = []
    for mw in mw_toks:
        best_bat_dist = _nearest_dist(mw["start"], battery_hits)
        best_sol_dist = _nearest_dist(mw["start"], list(SOLAR_RE.finditer(desc)))
        if best_bat_dist <= LONE_MW_WIN and best_bat_dist < best_sol_dist:
            scored.append({"mw": mw, "bdist": best_bat_dist})
    if scored:
        scored.sort(key=lambda s: s["bdist"])
        best = scored[0]
        mw = best["mw"]
        lo = max(0, mw["start"] - 80)
        hi = mw["end"] + 80
        ctx = desc[lo:hi]
        return {
            "storage_mw": round(mw["val"], 3),
            "storage_mwh": None,
            "coupling": _detect_coupling(ctx, mw["ac_dc"]),
            "evidence": _trim_evidence(ctx),
            "strategy": "B_lone_mw",
        }

    return {**blank, "strategy": "no_bess_sizing"}


def _nearest_dist(pos, matches):
    best = float("inf")
    for m in matches:
        d = min(abs(m.start() - pos), abs(m.end() - pos))
        if d < best:
            best = d
    return best


def _trim_evidence(ctx):
    ctx = re.sub(r"\s+", " ", ctx).strip()
    # Drop leading partial word (char-window cut mid-word). A real sentence
    # start would begin with uppercase, a digit, or a quote/paren.
    space_idx = ctx.find(" ")
    if space_idx > 0 and ctx and not re.match(r"[A-Z0-9\"'(\[]", ctx[0]):
        ctx = ctx[space_idx + 1:].strip()
    words = ctx.split()
    if len(words) <= EVIDENCE_WORDS:
        return " ".join(words)
    return " ".join(words[:EVIDENCE_WORDS])


def clean_display_name(s):
    """Strip trailing LLC/Inc/Corp/etc. from a facility name for display."""
    s = title_case_name(s)
    if not s:
        return s
    prev = None
    while prev != s:
        prev = s
        s = LEGAL_SUFFIX_RE.sub("", s).strip()
    return s


def infer_mwh_from_duration(desc, storage_mw):
    """If capacity_mwh is blank but the full description explicitly states an
    N-hour duration within battery context, return (mwh, hours, evidence).
    Otherwise return (None, None, '')."""
    desc = str(desc or "")
    if not desc or storage_mw is None or storage_mw <= 0:
        return None, None, ""
    battery_hits = list(BATTERY_RE.finditer(desc))
    if not battery_hits:
        return None, None, ""
    best = None
    for m in DURATION_RE.finditer(desc):
        try:
            hours = float(m.group(1))
        except ValueError:
            continue
        if hours < 0.25 or hours > 12:
            continue
        dist = min(
            min(abs(m.start() - b.start()), abs(m.end() - b.start()))
            for b in battery_hits
        )
        if dist > DURATION_MAX_DIST:
            continue
        if best is None or dist < best["dist"]:
            best = {"m": m, "hours": hours, "dist": dist}
    if best is None:
        return None, None, ""
    m = best["m"]
    lo = max(0, m.start() - 50)
    hi = m.end() + 50
    return round(storage_mw * best["hours"], 2), best["hours"], _trim_evidence(desc[lo:hi])


def _detect_coupling(ctx, token_ac_dc):
    if token_ac_dc in ("AC", "DC"):
        return token_ac_dc
    m = COUPLING_RE.search(ctx)
    if not m:
        return "unknown"
    s = m.group(1).upper()
    if s.startswith("AC"):
        return "AC"
    if s.startswith("DC"):
        return "DC"
    return "unknown"


def docket_year(docket):
    m = DOCKET_YEAR_RE.match(str(docket or ""))
    if not m:
        return None
    return 2000 + int(m.group(1))


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
    return {
        t for t in s.split()
        if len(t) >= 3 and t not in {
            "the", "and", "llc", "inc", "corp", "holdings", "project",
            "facility", "energy", "power", "solar", "storage", "farm",
            "farms", "site", "plant", "hybrid",
        }
    }


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main():
    counts = {}

    df = pd.read_csv(HYBRID_DEFER, low_memory=False)
    df["cap"] = pd.to_numeric(df["capacity_mw"], errors="coerce")
    counts["hybrid_defer_rows"] = len(df)

    # Replace truncated facility_description with full text from raw FERC CSV
    # (hybrid_defer.csv clipped to 500 chars in Phase 1; some BESS specs land past that)
    # Also pull Facility_City for merge into projects.json.
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
    desc_by_docket = dict(zip(raw_latest["_key"].astype(str), raw_latest["Facility_Description"].astype(str)))
    city_by_docket = dict(zip(raw_latest["_key"].astype(str), raw_latest["Facility_City"].astype(str)))
    df["facility_description"] = df["docket_number"].astype(str).map(desc_by_docket).fillna(df["facility_description"])
    df["facility_city"] = df["docket_number"].astype(str).map(city_by_docket).fillna("")

    band = df[(df["cap"] >= CAP_BAND[0]) & (df["cap"] <= CAP_BAND[1])].copy()
    counts["in_band_20_1000_mw"] = len(band)

    # Docket-prefix collapse: QF{yy}-{serial}-{seq} -- the -NNN suffix is the
    # submission sequence within one docket, so QF23-1291-000 and QF23-1291-001
    # are recerts/amendments of the same project. Phase 1's coord-bucket
    # collapse misses these when the applicant or coords shift between filings.
    band["_prefix"] = band["docket_number"].astype(str).str.replace(r"-\d+$", "", regex=True)
    band["_dt"] = pd.to_datetime(band["submission_date"], errors="coerce")
    band_sorted = band.sort_values("_dt", ascending=False, na_position="last")
    keep_mask = ~band_sorted["_prefix"].duplicated(keep="first")
    band = band_sorted[keep_mask].copy()
    counts["after_docket_prefix_collapse"] = len(band)

    drops = []

    # Battery term + non-battery storage filter
    desc_series = band["facility_description"].fillna("").astype(str)
    has_battery = desc_series.apply(lambda s: bool(BATTERY_RE.search(s)))
    negated = desc_series.apply(
        lambda s: bool(NEGATION_RE.search(s)) and len(BATTERY_RE.findall(s)) <= len(NEGATION_RE.findall(s))
    )
    non_batt_storage_only = desc_series.apply(
        lambda s: bool(NON_BATTERY_STORAGE_RE.search(s)) and not BATTERY_RE.search(s)
    )

    keep = has_battery & ~negated & ~non_batt_storage_only
    for _, r in band[~has_battery].iterrows():
        drops.append(_drop(r, "no_battery_term"))
    for _, r in band[has_battery & negated].iterrows():
        drops.append(_drop(r, "battery_negated"))
    for _, r in band[non_batt_storage_only].iterrows():
        drops.append(_drop(r, "non_battery_storage_only"))

    pool = band[keep].copy()
    counts["after_battery_filter"] = len(pool)

    # Parse each description
    pool["_parsed"] = pool["facility_description"].apply(extract_bess)
    pool["_storage_mw"] = pool["_parsed"].apply(lambda d: d["storage_mw"])
    pool["_storage_mwh"] = pool["_parsed"].apply(lambda d: d["storage_mwh"])
    pool["_coupling"] = pool["_parsed"].apply(lambda d: d["coupling"])
    pool["_evidence"] = pool["_parsed"].apply(lambda d: d["evidence"])
    pool["_strategy"] = pool["_parsed"].apply(lambda d: d["strategy"])

    # Strategy split
    strat_counts = pool["_strategy"].value_counts().to_dict()
    counts.update({f"strategy_{k}": v for k, v in strat_counts.items()})

    # Drop rows with no BESS sizing
    no_size = pool["_storage_mw"].isna()
    for _, r in pool[no_size].iterrows():
        drops.append(_drop(r, f"no_bess_sizing:{r['_strategy']}"))
    pool = pool[~no_size].copy()
    counts["after_bess_sizing_extracted"] = len(pool)

    # MW floor
    mw_ok = pool["_storage_mw"] >= MW_FLOOR
    for _, r in pool[~mw_ok].iterrows():
        drops.append(_drop(r, "below_storage_mw_floor"))
    pool = pool[mw_ok].copy()
    counts["after_storage_mw_floor_10"] = len(pool)

    # Pull lat/lng from original FERC data (hybrid_defer rows have lat/lng too)
    pool["lat"] = pd.to_numeric(pool["lat"], errors="coerce")
    pool["lng"] = pd.to_numeric(pool["lng"], errors="coerce")
    coords_ok = pool["lat"].notna() & pool["lng"].notna() & (pool["lat"] != 0) & (pool["lng"] != 0)
    for _, r in pool[~coords_ok].iterrows():
        drops.append(_drop(r, "missing_coords"))
    pool = pool[coords_ok].copy()
    counts["after_coords"] = len(pool)

    # Collision detection
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

    collision_flags = {}
    collisions_rows = []
    lat_arr = proj_df["lat"].to_numpy(float)
    lng_arr = proj_df["lng"].to_numpy(float)
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

    # Build candidate rows + status split
    candidates = []
    status_counts = {"planned": 0, "needs_review": 0}
    mwh_inferred_rows = []  # audit list
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

        # Duration-based MWh inference when MWh blank
        if pd.isna(storage_mwh):
            inferred_mwh, hours, dur_ev = infer_mwh_from_duration(
                row.get("facility_description"), storage_mw,
            )
            if inferred_mwh is not None:
                storage_mwh = inferred_mwh
                mwh_strategy = f"duration_{hours}h"
                mwh_inferred_rows.append({
                    "docket": docket,
                    "storage_mw": storage_mw,
                    "hours": hours,
                    "inferred_mwh": inferred_mwh,
                    "duration_evidence": dur_ev,
                })

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
            "collision_with": "; ".join(f"{pid} ({reason})" for pid, reason in collision_flags.get(idx, [])),
        })

    counts["status_planned"] = status_counts["planned"]
    counts["status_needs_review"] = status_counts["needs_review"]

    candidates.sort(key=lambda r: r["capacity_mw"], reverse=True)

    cand_cols = list(candidates[0].keys()) if candidates else [
        "id", "type", "name", "operator", "city", "state", "county_name",
        "lat", "lng", "capacity_mw", "capacity_mwh", "coupling",
        "co_located", "status", "docket_year", "source", "source_label",
        "docket_number", "submission_date", "energy_input",
        "facility_mw_reported", "extraction_strategy", "evidence",
        "collision_flag", "collision_with",
    ]
    _write_csv(OUT_CANDIDATES, cand_cols, candidates)

    top25 = [c for c in candidates if c["status"] == "planned" and not c["collision_flag"]][:25]
    counts["top25_rows"] = len(top25)
    counts["mwh_inferred_from_duration"] = len(mwh_inferred_rows)
    _write_csv(OUT_TOP25, cand_cols, top25)

    drop_cols = list(drops[0].keys()) if drops else [
        "docket_number", "submission_date", "applicant_name", "facility_name",
        "state", "capacity_mw", "energy_input", "drop_reason",
    ]
    _write_csv(OUT_DROPS, drop_cols, drops)

    if collisions_rows:
        _write_csv(OUT_COLLISIONS, list(collisions_rows[0].keys()), collisions_rows)
    else:
        with open(OUT_COLLISIONS, "w") as f:
            f.write("ferc_docket,ferc_facility,ferc_applicant,ferc_state,ferc_storage_mw,project_id,reason\n")

    _report(counts, candidates, top25, collisions_rows, mwh_inferred_rows)


def _drop(r, reason):
    return {
        "docket_number": r.get("docket_number"),
        "submission_date": r.get("submission_date"),
        "applicant_name": r.get("applicant_name"),
        "facility_name": r.get("facility_name"),
        "state": r.get("state"),
        "capacity_mw": r.get("capacity_mw"),
        "energy_input": r.get("energy_input"),
        "drop_reason": reason,
    }


def _write_csv(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _report(counts, candidates, top25, collisions, mwh_inferred_rows=None):
    print()
    print("=" * 72)
    print("FUNNEL")
    print("=" * 72)
    for k, v in counts.items():
        print(f"  {k:40s} : {v:>6}")

    print()
    print("=" * 72)
    print(f"TOP {len(top25)} CANDIDATES (planned, no collision, by storage_mw)")
    print("=" * 72)
    for c in top25:
        mwh = f"{c['capacity_mwh']:>7} MWh" if c['capacity_mwh'] != "" else "   -    MWh"
        print(f"  {c['capacity_mw']:7.2f} MW / {mwh}  [{c['coupling']:7s}]  {c['state']}  "
              f"{c['docket_number']:14s}  {c['name'][:55]}")
        print(f"    url: {c['source']}")
        print(f"    evidence: {c['evidence']}")

    print()
    print("=" * 72)
    print("COUNTS")
    print("=" * 72)
    print(f"  Top-25 output rows      : {len(top25)}")
    print(f"  Collisions pairs        : {len(collisions)}")
    needs_review = sum(1 for c in candidates if c["status"] == "needs_review")
    print(f"  needs_review (pre-2022) : {needs_review}")

    if mwh_inferred_rows:
        print()
        print("=" * 72)
        print(f"MWH INFERRED FROM N-HOUR DURATION ({len(mwh_inferred_rows)} rows)")
        print("=" * 72)
        for r in mwh_inferred_rows:
            print(f"  {r['docket']:16s}  {r['storage_mw']:.2f} MW x {r['hours']}h = {r['inferred_mwh']:.2f} MWh")
            print(f"    evidence: {r['duration_evidence']}")

    print()
    print("Wrote:")
    for p in (OUT_CANDIDATES, OUT_TOP25, OUT_DROPS, OUT_COLLISIONS):
        print(f"  {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
