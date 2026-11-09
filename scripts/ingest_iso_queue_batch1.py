#!/usr/bin/env python3
"""Filter LBNL Queued Up data for standalone BESS >= 20 MW in IA-stage buckets,
dedupe against projects.json, and prepare batch 1 (25 largest).

Outputs:
  data/queues/iso_candidates.csv  — all survivors after filtering (pre-dedupe)
  data/queues/iso_collisions.csv  — rows with likely existing-project match
  data/queues/iso_batch1.csv      — 25 largest non-colliding rows (final schema)
  data/queues/iso_batch1.json     — same 25 rows as projects.json-ready objects
"""

import csv
import json
import re
import sys
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/queues/raw/LBNL_Ix_Queue_Data_File_thru2025.xlsx"
OUT_DIR = ROOT / "data/queues"
PROJECTS = ROOT / "projects.json"
CACHE = ROOT / ".cache/census"
COUNTY_NAME = "cb_2024_us_county_500k"
COUNTY_URL = f"https://www2.census.gov/geo/tiger/GENZ2024/shp/{COUNTY_NAME}.zip"

IA_PHASES = {"IA Executed", "IA Pending", "Construction"}
STORAGE_T1 = {"Battery", "Other Storage"}

# One canonical public queue page per ISO/region (LBNL region labels)
ISO_SOURCE = {
    "CAISO":   "https://www.caiso.com/planning/queue-management",
    "ERCOT":   "https://www.ercot.com/gridinfo/resource",
    "ISO-NE":  "https://www.iso-ne.com/system-planning/system-plans-studies/interconnection-request-queue",
    "MISO":    "https://www.misoenergy.org/planning/resource-utilization/GIQ/",
    "NYISO":   "https://www.nyiso.com/interconnections",
    "PJM":     "https://www.pjm.com/planning/services-requests/interconnection-queues",
    "SPP":     "https://www.spp.org/engineering/generator-interconnection/",
    "Southeast": "https://emp.lbl.gov/queues",
    "West":    "https://emp.lbl.gov/queues",
}

STATUS_MAP = {
    "IA Executed": "planned",
    "IA Pending":  "planned",
    "Construction": "under_construction",
}


def read_queue():
    wb = openpyxl.load_workbook(RAW, read_only=True, data_only=True)
    ws = wb["03. Complete Queue Data"]
    it = ws.iter_rows(values_only=True)
    next(it)  # top banner
    headers = list(next(it))
    rows = []
    for r in it:
        if r is None or all(v is None for v in r):
            continue
        rows.append(dict(zip(headers, r)))
    return rows


def norm_county(s):
    if not s:
        return ""
    s = str(s).strip()
    # Strip trailing descriptors used by Census vs LBNL (e.g. "Garfield County", "Orleans Parish", "Richmond City")
    s = re.sub(r"\s+(County|Parish|City|Borough|Municipality|Census Area|Municipio)$", "", s, flags=re.IGNORECASE)
    return s.lower()


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s


def _fmt_mw(v):
    f = float(v)
    r = round(f, 1)
    return int(r) if r == int(r) else r


def title_case_name(s):
    if not s:
        return ""
    s = str(s).strip()
    # Title-case but keep stop words / acronyms reasonable; simple approach.
    t = re.sub(r"\s+", " ", s)
    # If clearly all caps or all lowercase, title-case it
    if t.isupper() or t.islower():
        t = t.title()
    return t


# ---- County centroid via Census shapefile (zero-dep, mirrors add-county.mjs) ----

def ensure_county_shapefile():
    shp = CACHE / f"{COUNTY_NAME}.shp"
    if shp.exists():
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    zip_path = CACHE / f"{COUNTY_NAME}.zip"
    print(f"[county] downloading {COUNTY_URL}", file=sys.stderr)
    urlretrieve(COUNTY_URL, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith((".shp", ".dbf", ".shx")):
                zf.extract(name, CACHE)


def read_shp(buf):
    import struct
    shapes = []
    off = 100
    while off < len(buf):
        rec_num, rec_len = struct.unpack(">ii", buf[off:off+8])
        rec = off + 8
        (shape_type,) = struct.unpack("<i", buf[rec:rec+4])
        if shape_type == 5:
            bbox = struct.unpack("<dddd", buf[rec+4:rec+36])
            n_parts, n_pts = struct.unpack("<ii", buf[rec+36:rec+44])
            parts = struct.unpack(f"<{n_parts}i", buf[rec+44:rec+44+4*n_parts])
            pt_base = rec + 44 + 4*n_parts
            rings = []
            for i, start in enumerate(parts):
                end = parts[i+1] if i+1 < n_parts else n_pts
                pts = struct.unpack(f"<{(end-start)*2}d", buf[pt_base + start*16: pt_base + end*16])
                rings.append(pts)
            shapes.append({"bbox": bbox, "rings": rings})
        else:
            shapes.append(None)
        off = rec + rec_len*2
    return shapes


def read_dbf(buf):
    import struct
    n = struct.unpack("<I", buf[4:8])[0]
    head_len, rec_len = struct.unpack("<HH", buf[8:12])
    fields = []
    o = 32
    while buf[o] != 0x0d:
        name = buf[o:o+11].split(b"\x00", 1)[0].decode("latin-1")
        flen = buf[o+16]
        fields.append((name, flen))
        o += 32
    rows = []
    for i in range(n):
        off = head_len + i*rec_len + 1
        rec = {}
        for name, flen in fields:
            rec[name] = buf[off:off+flen].decode("utf-8", errors="replace").strip()
            off += flen
        rows.append(rec)
    return rows


def ring_centroid(rings):
    # Centroid of the largest ring by polygon area (handles multipart counties fine for city/county centroid).
    def area(pts):
        a = 0.0
        for i in range(0, len(pts), 2):
            x1, y1 = pts[i], pts[i+1]
            x2, y2 = pts[(i+2) % len(pts)], pts[(i+3) % len(pts)]
            a += x1*y2 - x2*y1
        return a/2
    def centroid(pts):
        a = 0.0; cx = 0.0; cy = 0.0
        for i in range(0, len(pts), 2):
            x1, y1 = pts[i], pts[i+1]
            x2, y2 = pts[(i+2) % len(pts)], pts[(i+3) % len(pts)]
            f = x1*y2 - x2*y1
            a += f
            cx += (x1 + x2) * f
            cy += (y1 + y2) * f
        a /= 2
        if a == 0:
            # Degenerate — average vertices
            xs = pts[0::2]; ys = pts[1::2]
            return sum(xs)/len(xs), sum(ys)/len(ys)
        return cx/(6*a), cy/(6*a)
    big = max(rings, key=lambda r: abs(area(r)))
    return centroid(big)


def load_counties():
    ensure_county_shapefile()
    shp = read_shp((CACHE / f"{COUNTY_NAME}.shp").read_bytes())
    attrs = read_dbf((CACHE / f"{COUNTY_NAME}.dbf").read_bytes())
    by_fips = {}
    for shape, a in zip(shp, attrs):
        if not shape:
            continue
        geoid = a.get("GEOID")
        if not geoid:
            continue
        x, y = ring_centroid(shape["rings"])
        by_fips[geoid] = {
            "lat": round(y, 4),
            "lng": round(x, 4),
            "name_lsad": a.get("NAMELSAD"),
            "state_fp": a.get("STATEFP"),
            "state_usps": a.get("STUSPS"),
            "county_name": a.get("NAME"),
        }
    return by_fips


def fips5(code):
    if code is None:
        return None
    s = str(code).strip()
    if not s:
        return None
    # LBNL stores fips_code as int (e.g. 4005 = 04005). Zero-pad to 5.
    if s.isdigit():
        return s.zfill(5)
    return s


def main():
    print("[queue] reading xlsx…", file=sys.stderr)
    rows = read_queue()
    total = len(rows)

    # Filter funnel
    f1 = [r for r in rows if (r.get("type_1") in STORAGE_T1)
                           and not r.get("type_2") and not r.get("type_3")]
    f2 = [r for r in f1 if r.get("q_status") == "active"]
    f3 = [r for r in f2 if r.get("IA_phase_clean") in IA_PHASES]
    f4 = [r for r in f3 if (r.get("mw_1") or 0) >= 20]
    missing_year = [r for r in f4 if not r.get("prop_year")]
    f5 = [r for r in f4 if (not r.get("prop_year")) or int(r["prop_year"]) >= 2025]

    print(f"[funnel] total rows:                           {total}", file=sys.stderr)
    print(f"[funnel] standalone storage (t1 in {{Battery, Other Storage}} & t2/t3 empty): {len(f1)}", file=sys.stderr)
    print(f"[funnel]   + q_status=active:                  {len(f2)}", file=sys.stderr)
    print(f"[funnel]   + IA_phase in IA-stage buckets:     {len(f3)}", file=sys.stderr)
    print(f"[funnel]   + mw_1 >= 20:                       {len(f4)}", file=sys.stderr)
    print(f"[funnel]   + prop_year >= 2025 or missing:     {len(f5)}    (of which missing prop_year: {len(missing_year)})", file=sys.stderr)

    # Load counties & projects for mapping/dedupe
    counties = load_counties()
    projects_data = json.loads(PROJECTS.read_text())
    projects = projects_data["projects"]

    # Index projects by (state, norm_county)
    proj_by_sc = {}
    for p in projects:
        key = (p.get("state"), norm_county(p.get("county_name") or p.get("city") or ""))
        proj_by_sc.setdefault(key, []).append(p)

    # Build survivor candidates with mapping
    survivors = []
    skipped_no_fips = 0
    for r in f5:
        fips = fips5(r.get("fips_code"))
        if not fips or fips not in counties:
            skipped_no_fips += 1
            continue
        c = counties[fips]
        q_id = str(r.get("q_id") or "").strip()
        region = str(r.get("region") or "").strip()
        pname = r.get("project_name")
        name = title_case_name(pname) if pname else f"{region} queue {q_id} ({c['county_name']}, {r.get('state')})"
        rec = {
            "iso": region,
            "region": region,
            "q_id": q_id,
            "project_name": pname or "",
            "operator_dev": str(r.get("developer") or "").strip(),
            "operator_entity": str(r.get("entity") or "").strip(),
            "state": r.get("state"),
            "county_lbnl": r.get("county"),
            "county_name": c["name_lsad"],
            "county_fips": fips,
            "lat": c["lat"],
            "lng": c["lng"],
            "mw_1": r.get("mw_1"),
            "ia_phase": r.get("IA_phase_clean"),
            "prop_year": r.get("prop_year"),
            "name": name,
            "status": STATUS_MAP[r.get("IA_phase_clean")],
        }
        # Build id
        slug = slugify(q_id) or slugify(name)
        rec["id"] = f"iso-{region.lower().replace(' ', '-')}-{slug}"
        rec["source"] = ISO_SOURCE.get(region, "https://emp.lbl.gov/queues")
        rec["source_label"] = "LBNL Queued Up (ISO queue)"
        survivors.append(rec)

    # Dedupe against existing projects
    collisions = []
    for s in survivors:
        matches = []
        key = (s["state"], norm_county(s["county_name"]))
        for p in proj_by_sc.get(key, []):
            pmw = p.get("capacity_mw")
            if not pmw:
                continue
            mw = float(s["mw_1"])
            if abs(pmw - mw) / max(pmw, mw) <= 0.25:
                matches.append(p)
        if matches:
            for m in matches:
                collisions.append({
                    "queue_id": s["q_id"],
                    "iso": s["iso"],
                    "queue_name": s["name"],
                    "queue_state": s["state"],
                    "queue_county": s["county_name"],
                    "queue_mw": s["mw_1"],
                    "queue_status": s["status"],
                    "queue_prop_year": s["prop_year"],
                    "existing_id": m["id"],
                    "existing_name": m.get("name"),
                    "existing_state": m.get("state"),
                    "existing_county": m.get("county_name"),
                    "existing_mw": m.get("capacity_mw"),
                    "existing_status": m.get("status"),
                })
            s["_collision"] = True

    # Write iso_collisions.csv
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    coll_path = OUT_DIR / "iso_collisions.csv"
    with coll_path.open("w", newline="") as fh:
        if collisions:
            w = csv.DictWriter(fh, fieldnames=list(collisions[0].keys()))
            w.writeheader()
            w.writerows(collisions)
        else:
            fh.write("# no collisions\n")
    print(f"[dedupe] collisions: {len(collisions)} (unique survivors with any match: {sum(1 for s in survivors if s.get('_collision'))})", file=sys.stderr)

    # Write iso_candidates.csv (all survivors)
    cand_fields = ["id", "iso", "q_id", "name", "state", "county_name", "county_fips",
                   "lat", "lng", "mw_1", "status", "ia_phase", "prop_year",
                   "project_name", "operator_dev", "operator_entity", "source", "source_label",
                   "_collision"]
    cand_path = OUT_DIR / "iso_candidates.csv"
    with cand_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cand_fields, extrasaction="ignore")
        w.writeheader()
        for s in survivors:
            row = dict(s)
            row["_collision"] = "yes" if row.get("_collision") else ""
            w.writerow(row)

    # Pick batch 1: 25 largest by mw among NON-colliding
    non_coll = [s for s in survivors if not s.get("_collision")]
    non_coll.sort(key=lambda s: (float(s["mw_1"]), s["q_id"]), reverse=True)

    # Guard against id collisions within the batch (LBNL q_id can be non-unique across ISOs)
    seen_ids = set()
    batch = []
    for s in non_coll:
        base = s["id"]
        i = 2
        while s["id"] in seen_ids:
            s["id"] = f"{base}-{i}"
            i += 1
        seen_ids.add(s["id"])
        batch.append(s)
        if len(batch) >= 25:
            break

    # Build final project objects
    ISO_LIKE = {"CAISO", "ERCOT", "ISO-NE", "ISONE", "MISO", "NYISO", "PJM", "SPP", "WECC", "BPA"}
    final_objs = []
    for s in batch:
        entity = s["operator_entity"]
        if entity.upper() in ISO_LIKE or entity == s["iso"]:
            entity = ""
        operator = s["operator_dev"] or entity
        obj = {
            "id": s["id"],
            "type": "bess",
            "name": s["name"],
        }
        if operator:
            obj["operator"] = operator
        # Use bare county short name as "city" for display parity with EIA rows (e.g. "Garfield").
        county_short = re.sub(r"\s+(County|Parish|City|Borough|Municipality|Census Area|Municipio)$", "", s["county_name"], flags=re.IGNORECASE)
        obj.update({
            "city": county_short,
            "state": s["state"],
            "county_name": s["county_name"],
            "county_fips": s["county_fips"],
            "lat": s["lat"],
            "lng": s["lng"],
            "capacity_mw": _fmt_mw(s["mw_1"]),
            "status": s["status"],
        })
        if s["prop_year"]:
            obj["year_online"] = int(s["prop_year"])
        obj["source"] = s["source"]
        obj["source_label"] = s["source_label"]
        obj["queue_id"] = s["q_id"]
        obj["iso"] = s["iso"]
        obj["location_approximate"] = True
        final_objs.append(obj)

    # Write iso_batch1.csv
    batch_fields = ["id", "name", "iso", "q_id", "state", "county_name", "county_fips",
                    "lat", "lng", "capacity_mw", "status", "year_online",
                    "operator", "source", "source_label"]
    batch_path = OUT_DIR / "iso_batch1.csv"
    with batch_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=batch_fields, extrasaction="ignore")
        w.writeheader()
        for o in final_objs:
            w.writerow({**o, "q_id": o.get("queue_id")})

    # Write JSON-ready
    (OUT_DIR / "iso_batch1.json").write_text(json.dumps(final_objs, indent=2) + "\n")

    print(f"[batch] wrote {len(final_objs)} rows to {batch_path}", file=sys.stderr)
    print(f"[batch] wrote candidates: {cand_path} ({len(survivors)} rows)", file=sys.stderr)
    print(f"[batch] wrote collisions: {coll_path} ({len(collisions)} rows)", file=sys.stderr)
    print(f"[batch] fips-skipped (county not resolvable): {skipped_no_fips}", file=sys.stderr)

    # Print the 25-row summary for the handoff
    print("\n-- batch 1 (25 rows) --", file=sys.stderr)
    for o in final_objs:
        print(f"  {o['id']}  | {o['name'][:48]:<48} | {o.get('iso','?'):<8} | {o.get('queue_id',''):<14} | {o['state']} | {o['county_name']} | {o['capacity_mw']} MW | {o['status']:<17} | {o.get('year_online','-')}", file=sys.stderr)


if __name__ == "__main__":
    main()
