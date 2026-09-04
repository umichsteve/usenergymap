#!/usr/bin/env python3
"""
EIA-860M ingest for usenergymap.com.

Downloads the latest EIA-860M monthly inventory, filters battery storage
projects, normalizes to our schema, and merges with projects.json.

Hand-curated entries (those without "eia-" id prefix) are preserved.
EIA entries are fully replaced on each run (idempotent).

Run: python3 scripts/ingest_eia.py
Env: EIA_MONTH=december_generator2025 (optional override; defaults to latest available file)
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_FILE = ROOT / "projects.json"
CACHE_DIR = ROOT / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

ARCHIVE_URL = "https://www.eia.gov/electricity/data/eia860m/archive/xls/{slug}.xlsx"
CURRENT_URL = "https://www.eia.gov/electricity/data/eia860m/xls/{slug}.xlsx"
SOURCE_URL = "https://www.eia.gov/electricity/data/eia860m/"

# Map EIA "Status" codes to our status taxonomy.
PLANNED_STATUS_MAP = {
    "OP": "operating",
    "P": "planned",
    "L": "planned",
    "T": "planned",
    "U": "under_construction",
    "V": "under_construction",
    "TS": "under_construction",
}


def is_workbook_url(url: str, timeout: int = 6) -> bool:
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200 and int(r.headers.get("Content-Length", 0)) > 1_000_000
    except Exception:
        pass

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Range": "bytes=0-1023",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            chunk = r.read(4)
            return r.status in (200, 206) and chunk.startswith(b"PK")
    except Exception:
        return False


def workbook_url_for_slug(slug: str) -> str | None:
    """Return a downloadable EIA workbook URL for a slug, if one exists."""
    url = CURRENT_URL.format(slug=slug)
    if is_workbook_url(url, timeout=20):
        return url
    url = ARCHIVE_URL.format(slug=slug)
    if is_workbook_url(url, timeout=8):
        return url
    return None


def previous_month_slug(dt: datetime, months: list[str]) -> str:
    m = dt.month - 2
    y = dt.year
    while m < 0:
        m += 12
        y -= 1
    return f"{months[m]}_generator{y}"


def latest_slug_from_release_page(months: list[str]) -> str | None:
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    m = re.search(r"Release Date:</span>\s*<span class=\"date\">([^<]+)</span>", html)
    if not m:
        return None
    try:
        release_date = datetime.strptime(m.group(1).strip(), "%B %d, %Y")
    except ValueError:
        return None
    return previous_month_slug(release_date, months)


def latest_workbook() -> tuple[str, str]:
    """Return the most recent EIA-860M slug and downloadable workbook URL."""
    if os.environ.get("EIA_MONTH"):
        slug = os.environ["EIA_MONTH"]
        url = workbook_url_for_slug(slug)
        if not url:
            raise RuntimeError(f"Could not download EIA-860M workbook for {slug}")
        return slug, url
    months = ["january", "february", "march", "april", "may", "june",
              "july", "august", "september", "october", "november", "december"]
    page_slug = latest_slug_from_release_page(months)
    if page_slug:
        url = workbook_url_for_slug(page_slug)
        if url:
            return page_slug, url

    today = datetime.now(timezone.utc)
    slugs = []
    for offset in range(0, 18):
        m = today.month - 2 - offset
        y = today.year
        while m < 0:
            m += 12
            y -= 1
        slugs.append(f"{months[m]}_generator{y}")

    # EIA leaves future-month archive links on the page that redirect slowly.
    # Current files in /xls/ are the first place a new public release appears.
    for slug in slugs:
        url = CURRENT_URL.format(slug=slug)
        if is_workbook_url(url, timeout=20):
            return slug, url
    for slug in slugs:
        url = ARCHIVE_URL.format(slug=slug)
        if is_workbook_url(url, timeout=8):
            return slug, url
    raise RuntimeError("Could not locate any EIA-860M workbook within 18 months")


def download(slug: str, url: str) -> Path:
    out = CACHE_DIR / f"{slug}.xlsx"
    if out.exists() and out.stat().st_size > 1_000_000:
        return out
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    print(f"[ingest] downloading {url}")
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    if len(data) < 1_000_000:
        raise RuntimeError(f"Suspiciously small file ({len(data)} bytes) from {url}")
    out.write_bytes(data)
    return out


def safe_num(x):
    try:
        v = float(x)
        if pd.isna(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return s[:60] or "unknown"


def parse_status(raw: str) -> str:
    if not isinstance(raw, str):
        return "planned"
    m = re.match(r"\(([A-Z]+)\)", raw.strip())
    code = m.group(1) if m else raw.strip().upper()
    return PLANNED_STATUS_MAP.get(code, "planned")


def aggregate_plants(df: pd.DataFrame, status_override: str | None) -> list[dict]:
    """Aggregate generator-level rows into plant-level entries.

    Handles missing optional columns (e.g. Planned sheet lacks MWh).
    """
    df = df.copy()
    # Year column differs between sheets
    if "Operating Year" not in df.columns and "Planned Operation Year" in df.columns:
        df["Operating Year"] = df["Planned Operation Year"]
    for col in ("Nameplate Capacity (MW)", "Operating Year", "Latitude", "Longitude"):
        df[col] = df[col].apply(safe_num)
    if "Nameplate Energy Capacity (MWh)" in df.columns:
        df["Nameplate Energy Capacity (MWh)"] = df["Nameplate Energy Capacity (MWh)"].apply(safe_num)
    else:
        df["Nameplate Energy Capacity (MWh)"] = None

    out = []
    for (plant_id, plant_name, state), grp in df.groupby(["Plant ID", "Plant Name", "Plant State"]):
        mw = grp["Nameplate Capacity (MW)"].sum(min_count=1)
        mwh = grp["Nameplate Energy Capacity (MWh)"].sum(min_count=1)
        lats = grp["Latitude"].dropna()
        lngs = grp["Longitude"].dropna()
        if lats.empty or lngs.empty:
            continue
        lat = float(lats.iloc[0])
        lng = float(lngs.iloc[0])
        # US continental + AK/HI/PR sanity bounds
        if not (-180 <= lng <= -60) or not (15 <= lat <= 72):
            continue
        operator = str(grp["Entity Name"].iloc[0]).strip()
        county = str(grp["County"].iloc[0]).strip() if pd.notna(grp["County"].iloc[0]) else ""
        op_years = grp["Operating Year"].dropna()
        year = int(op_years.min()) if not op_years.empty else None
        if status_override:
            status = status_override
        else:
            status = parse_status(grp["Status"].iloc[0])

        entry = {
            "id": f"eia-{int(plant_id)}",
            "type": "bess",
            "name": str(plant_name).strip(),
            "operator": operator,
            "city": county or str(state),
            "state": str(state),
            "lat": round(lat, 4),
            "lng": round(lng, 4),
            "capacity_mw": int(round(mw)) if mw and mw > 0 else None,
            "status": status,
            "source": SOURCE_URL,
        }
        if mwh and mwh > 0:
            entry["capacity_mwh"] = int(round(mwh))
        if year and 1990 <= year <= 2035:
            entry["year_online"] = year
        # Drop entries with no MW (data quality)
        if entry["capacity_mw"] is None or entry["capacity_mw"] <= 0:
            continue
        out.append(entry)
    return out


def merge_duplicate_eia_plants(entries: list[dict]) -> list[dict]:
    """Merge plants that have both operating and planned generator rows.

    EIA can list the same Plant ID in both sheets when a facility has an
    operating phase and a future battery expansion. The site schema uses one
    row per plant, so keep one stable eia-{Plant ID} row with total MW.
    """
    by_id = {}
    for entry in entries:
        by_id.setdefault(entry["id"], []).append(entry)

    merged = []
    status_rank = {"operating": 0, "planned": 1, "under_construction": 2}
    for plant_id, rows in by_id.items():
        if len(rows) == 1:
            merged.append(rows[0])
            continue

        rows = sorted(rows, key=lambda r: status_rank.get(r.get("status"), 0))
        statuses = {r.get("status") for r in rows}
        active_rows = [r for r in rows if r.get("status") != "operating"]
        base = dict(active_rows[0] if active_rows else rows[0])
        base["id"] = plant_id
        base["capacity_mw"] = int(round(sum(r.get("capacity_mw") or 0 for r in rows)))
        mwh = sum(r.get("capacity_mwh") or 0 for r in rows)
        if mwh > 0:
            base["capacity_mwh"] = int(round(mwh))
        elif "capacity_mwh" in base:
            del base["capacity_mwh"]

        if "under_construction" in statuses:
            base["status"] = "under_construction"
        elif "planned" in statuses:
            base["status"] = "planned"
        else:
            base["status"] = "operating"

        years = [r.get("year_online") for r in (active_rows or rows) if r.get("year_online")]
        if years:
            base["year_online"] = max(years)
        elif "year_online" in base:
            del base["year_online"]
        merged.append(base)

    return merged


def main():
    slug, url = latest_workbook()
    print(f"[ingest] using EIA-860M slug: {slug}")
    xlsx = download(slug, url)

    # Operating BESS
    df_op = pd.read_excel(xlsx, sheet_name="Operating", header=2)
    op_bess = df_op[df_op["Technology"] == "Batteries"]
    print(f"[ingest] Operating BESS rows: {len(op_bess)}")
    operating = aggregate_plants(op_bess, status_override="operating")
    print(f"[ingest]   -> {len(operating)} unique operating plants")

    # Planned BESS
    df_pl = pd.read_excel(xlsx, sheet_name="Planned", header=2)
    pl_bess = df_pl[df_pl["Technology"] == "Batteries"]
    print(f"[ingest] Planned BESS rows: {len(pl_bess)}")
    planned = aggregate_plants(pl_bess, status_override=None)
    print(f"[ingest]   -> {len(planned)} unique planned plants")

    eia_entries = merge_duplicate_eia_plants(operating + planned)
    print(f"[ingest]   -> {len(eia_entries)} unique EIA plant entries after cross-sheet merge")

    # Merge with existing projects.json — preserve hand-curated, replace EIA-sourced
    with open(PROJECTS_FILE) as f:
        data = json.load(f)
    existing = data.get("projects", [])
    kept = [p for p in existing if not p.get("id", "").startswith("eia-")]
    print(f"[ingest] kept {len(kept)} hand-curated entries (dropped {len(existing) - len(kept)} prior EIA entries)")

    # De-dup against hand-curated:
    #  (a) proximity within ~50km in the same state, OR
    #  (b) close-enough name match in the same state (substring after stripping noise)
    def norm_name(s):
        s = re.sub(r"\b(bess|energy storage|storage|solar\s*\+?\s*storage|project|center|facility|phase\s*\w+)\b",
                   "", s.lower())
        return re.sub(r"[^a-z0-9]+", "", s)

    kept_bess = [p for p in kept if p.get("type") == "bess"]
    kept_keys_by_state = {}
    for p in kept_bess:
        kept_keys_by_state.setdefault(p["state"], []).append({
            "lat": p["lat"], "lng": p["lng"], "name_norm": norm_name(p["name"])
        })

    deduped = []
    skipped = 0
    for e in eia_entries:
        peers = kept_keys_by_state.get(e["state"], [])
        en = norm_name(e["name"])
        collision = False
        for kp in peers:
            # Proximity dedup: ~10km tolerance (~0.1° lat). Tight enough to avoid
            # cross-project false positives in BESS-dense states like CA/TX.
            if abs(kp["lat"] - e["lat"]) < 0.1 and abs(kp["lng"] - e["lng"]) < 0.1:
                collision = True
                break
            # Name dedup: require strong overlap (both names >= 8 chars after norm,
            # and one fully contains the other). Prevents "Vega" matching "Vega II".
            if en and kp["name_norm"] and min(len(en), len(kp["name_norm"])) >= 8 \
               and (en in kp["name_norm"] or kp["name_norm"] in en):
                collision = True
                break
        if collision:
            skipped += 1
        else:
            deduped.append(e)
    print(f"[ingest] de-duped {skipped} EIA entries that collided with hand-curated; {len(deduped)} added")

    merged = kept + deduped
    data["projects"] = merged
    # Provenance: which federal file this build came from. Lets a no-op month (same file
    # re-ingested because EIA published late) be detected instead of silently bumping the date.
    prev_slug = data.get("eia_source_month")
    data["eia_source_month"] = slug
    data["eia_source_url"] = url
    data["eia_ingested_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if prev_slug == slug:
        print(f"[ingest] WARNING: EIA file {slug} is the same as the last build — EIA may not have published yet.")
    data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data.setdefault("notes", "")
    if "EIA-860M" not in data.get("notes", ""):
        data["notes"] += " | BESS entries with id prefix 'eia-' are auto-ingested monthly from EIA-860M; others are hand-curated."

    PROJECTS_FILE.write_text(json.dumps(data, indent=2))
    print(f"[ingest] wrote {PROJECTS_FILE} with {len(merged)} total projects")
    from collections import Counter
    by_type = Counter(p["type"] for p in merged)
    by_status = Counter(p["status"] for p in merged)
    by_state = Counter(p["state"] for p in merged)
    total_mw = sum(p.get("capacity_mw") or 0 for p in merged)
    print(f"[ingest] by type: {dict(by_type)}")
    print(f"[ingest] by status: {dict(by_status)}")
    print(f"[ingest] states: {len(by_state)} unique")
    print(f"[ingest] total MW: {total_mw:,}")


if __name__ == "__main__":
    main()
