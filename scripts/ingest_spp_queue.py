#!/usr/bin/env python3
"""Ingest the full SPP active interconnection queue for standalone BESS >= 20 MW
with an IA pending/executed status, resolve a county-level location for each row,
and emit projects.json-ready records with the new "queued" status.

Replaces the LBNL "Queued Up" + top-25-across-ISOs pipeline for SPP so projects
added to the queue after the 2025 LBNL cutoff show up as well.

Inputs:
  data/queues/raw/current/spp_active_queue.csv     — fresh SPP opsportal CSV
                                                     (header on line 2)
  data/queues/iso_candidates.csv                   — LBNL FIPS mappings (fallback)
  .cache/census/cb_2024_us_county_500k.{shp,dbf}   — county polygons + centroids
  .cache/census/2024_Gaz_place_national.txt        — Census Gazetteer places

Outputs (under data/queues/):
  spp_candidates.csv       — every SPP survivor with its resolution method
  spp_unresolved.csv       — rows we couldn't map to a county (none expected)
  spp_dedupe_matches.csv   — rows dropped because an eia- record covers them
  spp_batch.json           — projects.json-ready records (non-colliding, resolved)
"""

import csv
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_CSV = ROOT / "data/queues/raw/current/spp_active_queue.csv"
LBNL_CANDIDATES = ROOT / "data/queues/iso_candidates.csv"
OUT_DIR = ROOT / "data/queues"
PROJECTS = ROOT / "projects.json"
CACHE = ROOT / ".cache/census"
COUNTY_SHP = CACHE / "cb_2024_us_county_500k.shp"
COUNTY_DBF = CACHE / "cb_2024_us_county_500k.dbf"
PLACES_TXT = CACHE / "2024_Gaz_place_national.txt"

SOURCE_URL = "https://opsportal.spp.org/Studies/GenerateActiveCSV"
SOURCE_LABEL = "SPP interconnection queue (current)"

# Status buckets we keep. COMMERCIAL OPERATION is dropped — those projects have
# left the queue and are tracked via EIA-860M. "ON SUSPENSION" is retained per
# instruction; the raw text is carried in queue_status so the suspension signal
# is visible to anyone reading the record.
IA_KEEP = {
    "IA FULLY EXECUTED/ON SCHEDULE",
    "IA PENDING",
    "IA FULLY EXECUTED/ON SUSPENSION",
}

# Minor typo fixes seen in the SPP "Nearest Town or County" column.
TYPO_FIXES = {"Cimmaron": "Cimarron", "Greely": "Greeley", "Sedwick": "Sedgwick"}

# Dedupe thresholds per instruction: same county + capacity within 10% + similar COD.
MW_TOLERANCE = 0.10
YEAR_TOLERANCE = 1


# ===== Minimal shapefile readers (shared with add-county.mjs / ingest_iso_queue_batch1.py)

def _read_shp(buf):
    shapes = []
    off = 100
    while off < len(buf):
        rec_num, rec_len = struct.unpack(">ii", buf[off:off + 8])
        rec = off + 8
        (shape_type,) = struct.unpack("<i", buf[rec:rec + 4])
        if shape_type == 5:
            n_parts, n_pts = struct.unpack("<ii", buf[rec + 36:rec + 44])
            parts = struct.unpack(f"<{n_parts}i", buf[rec + 44:rec + 44 + 4 * n_parts])
            pt_base = rec + 44 + 4 * n_parts
            rings = []
            for i, start in enumerate(parts):
                end = parts[i + 1] if i + 1 < n_parts else n_pts
                pts = struct.unpack(f"<{(end - start) * 2}d",
                                    buf[pt_base + start * 16:pt_base + end * 16])
                rings.append(pts)
            shapes.append({"rings": rings})
        else:
            shapes.append(None)
        off = rec + rec_len * 2
    return shapes


def _read_dbf(buf):
    n = struct.unpack("<I", buf[4:8])[0]
    head_len, rec_len = struct.unpack("<HH", buf[8:12])
    fields = []
    o = 32
    while buf[o] != 0x0d:
        name = buf[o:o + 11].split(b"\x00", 1)[0].decode("latin-1")
        flen = buf[o + 16]
        fields.append((name, flen))
        o += 32
    rows = []
    for i in range(n):
        off = head_len + i * rec_len + 1
        rec = {}
        for name, flen in fields:
            rec[name] = buf[off:off + flen].decode("utf-8", errors="replace").strip()
            off += flen
        rows.append(rec)
    return rows


def _centroid(rings):
    def area(pts):
        a = 0.0
        for i in range(0, len(pts), 2):
            x1, y1 = pts[i], pts[i + 1]
            x2, y2 = pts[(i + 2) % len(pts)], pts[(i + 3) % len(pts)]
            a += x1 * y2 - x2 * y1
        return a / 2

    def c(pts):
        a = cx = cy = 0.0
        for i in range(0, len(pts), 2):
            x1, y1 = pts[i], pts[i + 1]
            x2, y2 = pts[(i + 2) % len(pts)], pts[(i + 3) % len(pts)]
            f = x1 * y2 - x2 * y1
            a += f
            cx += (x1 + x2) * f
            cy += (y1 + y2) * f
        a /= 2
        if a == 0:
            xs = pts[0::2]
            ys = pts[1::2]
            return sum(xs) / len(xs), sum(ys) / len(ys)
        return cx / (6 * a), cy / (6 * a)

    big = max(rings, key=lambda r: abs(area(r)))
    return c(big)


# ===== Lookups

def norm_county(s):
    s = re.sub(r"\s+(County|Co|Parish|City|Borough|Municipality|Census Area|Municipio)\.?\s*$",
               "", s or "", flags=re.IGNORECASE)
    return s.strip().lower()


def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def year_from(v):
    if not v:
        return None
    m = re.search(r"\b(20\d{2})\b", str(v))
    return int(m.group(1)) if m else None


def load_counties():
    """Return (county_by_sn, by_fips) with centroids and names.

    county_by_sn: {(STUSPS, lower_county_name_no_suffix): GEOID}
    by_fips: {GEOID: (lat, lng, STUSPS, NAMELSAD, NAME)}
    """
    shp = _read_shp(COUNTY_SHP.read_bytes())
    attrs = _read_dbf(COUNTY_DBF.read_bytes())
    county_by_sn = {}
    by_fips = {}
    for shape, a in zip(shp, attrs):
        if not shape:
            continue
        x, y = _centroid(shape["rings"])
        county_by_sn[(a["STUSPS"], norm_county(a["NAME"]))] = a["GEOID"]
        by_fips[a["GEOID"]] = (round(y, 4), round(x, 4),
                               a["STUSPS"], a["NAMELSAD"], a["NAME"])
    return county_by_sn, by_fips


def load_places():
    """Return {(STUSPS, lower_name_no_suffix): [(lat, lng, GEOID, full_name), ...]}."""
    suff_re = re.compile(
        r"\s+(city|town|village|CDP|borough|municipality|comunidad|zona urbana|urban zone)$",
        re.IGNORECASE)
    out = {}
    with PLACES_TXT.open() as fh:
        fh.readline()  # header
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 12:
                continue
            state, geoid, _, name, _lsad, _func, _, _, _, _, lat, lng = parts[:12]
            clean = suff_re.sub("", name).strip().lower()
            try:
                out.setdefault((state, clean), []).append(
                    (float(lat), float(lng.strip()), geoid, name))
            except ValueError:
                pass
    return out


def load_lbnl_fips():
    """Map SPP q_id -> fips_code using the LBNL candidates file as a fallback source."""
    out = {}
    if not LBNL_CANDIDATES.exists():
        return out
    with LBNL_CANDIDATES.open() as fh:
        for r in csv.DictReader(fh):
            if r["iso"] == "SPP" and r["county_fips"]:
                out[r["q_id"]] = r["county_fips"]
    return out


# ===== Row helpers

def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s


def _fmt_mw(v):
    f = float(v)
    r = round(f, 1)
    return int(r) if r == int(r) else r


def short_county(name_lsad):
    return re.sub(r"\s+(County|Parish|City|Borough|Municipality|Census Area|Municipio)$",
                  "", name_lsad or "", flags=re.IGNORECASE)


# ===== Resolution

COUNTY_IN_FIELD_RE = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+County\b")


def resolve(row, lbnl, county_by_sn, by_fips, place_by_sn):
    """Return (fips, lat, lng, method, detail) or (None, None, None, 'unresolved', why)."""
    qid = row["Generation Interconnection Number"].strip()
    state = row["State"].strip()
    tc = row[" Nearest Town or County"].strip()
    sub = (row.get("Substation or Line") or "").strip()

    # 1. LBNL fips_code (handles Garner, cross-state entries, and rows whose town
    #    name does not match any gazetteer entry).
    if qid in lbnl:
        fips = lbnl[qid]
        if fips in by_fips:
            lat, lng, *_ = by_fips[fips]
            return fips, lat, lng, "lbnl", f"LBNL fips_code={fips}"

    # Parse "Nearest Town or County" — take first region if multiple separated by
    # "," "/" or " and "; strip trailing state code like " KS".
    first = re.split(r"[,/]| and ", tc)[0].strip()
    first = re.sub(r"\s+[A-Z]{2}$", "", first).strip()
    candidates = [first]
    for k, v in TYPO_FIXES.items():
        if k in first:
            candidates.append(first.replace(k, v))

    # 2. County match
    for c in candidates:
        key = (state, norm_county(c))
        if key in county_by_sn:
            fips = county_by_sn[key]
            lat, lng, *_ = by_fips[fips]
            return fips, lat, lng, "county", c

    # 3. Place match
    for c in candidates:
        key = (state, c.lower())
        if key in place_by_sn:
            plat, plng, _geoid, pname = place_by_sn[key][0]
            # Place lat/lng won't land on a county centroid; add-county.mjs will
            # fill in county_name/county_fips later via point-in-polygon. For the
            # ingest we still want a FIPS so the dedupe can key by county: look
            # up the county whose centroid is closest. Simpler: find the county
            # whose polygon contains the place point. Fall back to nearest centroid.
            fips = _county_for_point(state, plat, plng, county_by_sn, by_fips)
            return fips, round(plat, 4), round(plng, 4), "place", pname

    # 4. Parse "<X> County" out of the Substation or Line field as a last-resort
    #    fallback (SPP sometimes names the county in the substation string even
    #    when the town is an unincorporated community).
    m = COUNTY_IN_FIELD_RE.search(sub)
    if m:
        cand = m.group(1)
        key = (state, norm_county(cand))
        if key in county_by_sn:
            fips = county_by_sn[key]
            lat, lng, *_ = by_fips[fips]
            return fips, lat, lng, "substation", f"{cand} County (from substation)"

    return None, None, None, "unresolved", f"town={tc!r} substation={sub!r}"


def _county_for_point(state, lat, lng, county_by_sn, by_fips):
    """Find the county GEOID for a (lat, lng) in a given state by nearest centroid."""
    best = None
    best_d = 1e18
    for fips, (clat, clng, stusps, _n, _s) in by_fips.items():
        if stusps != state:
            continue
        dx = clng - lng
        dy = clat - lat
        d = dx * dx + dy * dy
        if d < best_d:
            best_d = d
            best = fips
    return best


# ===== Dedupe against eia- rows

def load_eia_bess_index(projects):
    """Index eia-* BESS projects by county_fips so dedupe is O(1) per SPP row."""
    idx = {}
    for p in projects:
        if not p.get("id", "").startswith("eia-"):
            continue
        if p.get("type") != "bess":
            continue
        fips = p.get("county_fips")
        if not fips:
            continue
        idx.setdefault(fips, []).append(p)
    return idx


def find_dupes(fips, mw, year, eia_by_fips):
    """Return list of eia- projects that match this SPP row by county + ~MW + ~COD."""
    hits = []
    for p in eia_by_fips.get(fips, []):
        pmw = p.get("capacity_mw")
        if not pmw or not mw:
            continue
        if abs(pmw - mw) / max(pmw, mw) > MW_TOLERANCE:
            continue
        # Year check only applies when both have a year — otherwise fall through.
        py = p.get("year_online")
        if year and py and abs(int(py) - int(year)) > YEAR_TOLERANCE:
            continue
        hits.append(p)
    return hits


# ===== Main

def main():
    county_by_sn, by_fips = load_counties()
    place_by_sn = load_places()
    lbnl = load_lbnl_fips()
    projects_data = json.loads(PROJECTS.read_text())
    eia_by_fips = load_eia_bess_index(projects_data["projects"])

    # Read the SPP CSV. Line 1 is the "Last Updated On,…" banner; line 2 is header.
    with RAW_CSV.open() as fh:
        banner = fh.readline().rstrip("\n")
        rows = list(csv.DictReader(fh))
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", banner)
    asof = _mdY_to_iso(m.group(1)) if m else None
    print(f"[spp] source as of {asof} (banner: {banner!r})", file=sys.stderr)

    # Filter: Battery/Storage, IA-stage status, capacity >= 20 MW.
    survivors = []
    for r in rows:
        if r["Generation Type"].strip() != "Battery/Storage":
            continue
        status = r["Status"].strip()
        if status not in IA_KEEP:
            continue
        cap = (to_float(r.get("Capacity"))
               or to_float(r.get("MAX Summer MW"))
               or to_float(r.get("Nameplate Capacity")))
        if not cap or cap < 20:
            continue
        r["_cap"] = cap
        survivors.append(r)
    print(f"[spp] survivors (BESS >=20 MW, IA pending/executed): {len(survivors)}",
          file=sys.stderr)

    # Resolve location for each survivor.
    candidates = []
    unresolved = []
    for r in survivors:
        qid = r["Generation Interconnection Number"].strip()
        state = r["State"].strip()
        tc = r[" Nearest Town or County"].strip()
        status = r["Status"].strip()
        cap = r["_cap"]
        # Prefer Commercial Operation Date — it's the forward-looking target. The
        # In-Service Date is a historical backfeed/energization date for interim
        # service and lags COD by years on most requests. Keep the raw ISD in
        # spp_in_service_date so the full queue record stays visible.
        cod = (r.get("Commercial Operation Date") or "").strip()
        isd = (r.get("In-Service Date") or "").strip()
        year = year_from(cod) or year_from(isd)
        fips, lat, lng, method, detail = resolve(r, lbnl, county_by_sn, by_fips, place_by_sn)
        if fips is None:
            unresolved.append({
                "q_id": qid, "state": state, "nearest": tc, "mw": cap,
                "status": status, "substation": r.get("Substation or Line"),
                "reason": detail,
            })
            continue
        county_name = by_fips[fips][3] if fips in by_fips else ""
        candidates.append({
            "q_id": qid, "state": state, "nearest": tc, "mw": cap, "status_raw": status,
            "year": year, "fips": fips, "lat": lat, "lng": lng,
            "county_name": county_name, "method": method, "detail": detail,
            "cod_raw": cod, "isd_raw": isd,
        })

    # Dedupe against eia- BESS records.
    dupes_rows = []
    kept = []
    for c in candidates:
        hits = find_dupes(c["fips"], c["mw"], c["year"], eia_by_fips)
        if hits:
            for h in hits:
                dupes_rows.append({
                    "q_id": c["q_id"], "iso": "SPP",
                    "state": c["state"], "county": c["county_name"],
                    "queue_mw": c["mw"], "queue_year": c["year"], "queue_status": c["status_raw"],
                    "existing_id": h["id"], "existing_name": h.get("name"),
                    "existing_mw": h.get("capacity_mw"),
                    "existing_year": h.get("year_online"),
                    "existing_status": h.get("status"),
                })
            c["_colliding"] = True
            continue
        kept.append(c)
    print(f"[spp] after dedupe vs eia-: {len(kept)} kept, "
          f"{len(dupes_rows)} match rows across "
          f"{sum(1 for c in candidates if c.get('_colliding'))} SPP projects",
          file=sys.stderr)

    # Build projects.json records for kept rows.
    final = []
    seen_ids = set()
    for c in kept:
        slug = slugify(c["q_id"])
        pid = f"iso-spp-{slug}"
        base = pid
        n = 2
        while pid in seen_ids:
            pid = f"{base}-{n}"
            n += 1
        seen_ids.add(pid)

        short = short_county(c["county_name"])
        name = f"SPP queue {c['q_id']} ({short}, {c['state']})"
        mw = _fmt_mw(c["mw"])
        obj = {
            "id": pid,
            "type": "bess",
            "name": name,
            "city": short,
            "state": c["state"],
            "county_name": c["county_name"],
            "county_fips": c["fips"],
            "lat": c["lat"],
            "lng": c["lng"],
            "capacity_mw": mw,
            "status": "queued",
        }
        if c["year"]:
            obj["year_online"] = int(c["year"])
        obj["source"] = SOURCE_URL
        obj["source_label"] = SOURCE_LABEL
        obj["queue_id"] = c["q_id"]
        obj["iso"] = "SPP"
        obj["queue_asof"] = asof
        obj["queue_status"] = c["status_raw"]
        # Keep the SPP "In-Service Date" verbatim (ISO yyyy-mm-dd) so a reader
        # can see it was separate from the authoritative COD used for year_online.
        isd_iso = _mdY_to_iso(c["isd_raw"]) if c.get("isd_raw") else None
        if isd_iso:
            obj["spp_in_service_date"] = isd_iso
        obj["location_approximate"] = True
        final.append(obj)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cand_fields = ["q_id", "state", "nearest", "mw", "status_raw", "year",
                   "fips", "county_name", "lat", "lng", "method", "detail"]
    with (OUT_DIR / "spp_candidates.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cand_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(candidates)

    with (OUT_DIR / "spp_unresolved.csv").open("w", newline="") as fh:
        fieldnames = ["q_id", "state", "nearest", "mw", "status", "substation", "reason"]
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(unresolved)

    with (OUT_DIR / "spp_dedupe_matches.csv").open("w", newline="") as fh:
        fieldnames = ["q_id", "iso", "state", "county", "queue_mw", "queue_year",
                      "queue_status", "existing_id", "existing_name",
                      "existing_mw", "existing_year", "existing_status"]
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(dupes_rows)

    (OUT_DIR / "spp_batch.json").write_text(json.dumps(final, indent=2) + "\n")

    # Per-state summary
    from collections import Counter
    per_state = Counter(c["state"] for c in kept)
    print(f"\n[spp] kept SPP rows by state: {dict(sorted(per_state.items()))}",
          file=sys.stderr)
    print(f"[spp] unresolved: {len(unresolved)}", file=sys.stderr)
    for u in unresolved:
        print(f"    - {u['q_id']} | {u['state']} | {u['nearest']} | "
              f"{u['mw']} MW | {u['reason']}", file=sys.stderr)
    print(f"\n[spp] wrote spp_batch.json ({len(final)} rows), "
          f"spp_candidates.csv, spp_unresolved.csv, spp_dedupe_matches.csv",
          file=sys.stderr)


def _mdY_to_iso(s):
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if not m:
        return None
    mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
    return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"


if __name__ == "__main__":
    main()
