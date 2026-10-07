#!/usr/bin/env node
// Adds county_name + county_fips to every record in projects.json by point-in-polygon
// against the US Census cartographic boundary county file (cb_2024_us_county_500k).
//
//     node scripts/add-county.mjs            # enrich projects.json in place, print report
//     node scripts/add-county.mjs --check    # report only, don't write
//
// Runs offline in the monthly refresh workflow right after ingest_eia.py (which rebuilds
// every eia- record and so drops these fields). Records whose lat/lng falls in no county
// polygon are listed in the report and left without county fields — no guesses.
// Zero dependencies: a minimal .shp/.dbf reader lives below; needs `unzip` on PATH.

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PROJECTS = join(ROOT, "projects.json");
const CACHE = join(ROOT, ".cache", "census");
const NAME = "cb_2024_us_county_500k";
const URL = `https://www2.census.gov/geo/tiger/GENZ2024/shp/${NAME}.zip`;
const CHECK = process.argv.includes("--check");

// ---- Fetch + unpack the Census shapefile (cached under .cache/, gitignored) ----
async function ensureShapefile() {
  const shp = join(CACHE, NAME + ".shp");
  if (existsSync(shp)) return;
  mkdirSync(CACHE, { recursive: true });
  const zip = join(CACHE, NAME + ".zip");
  console.error(`[county] downloading ${URL}`);
  const res = await fetch(URL);
  if (!res.ok) throw new Error(`Census download failed (${res.status})`);
  writeFileSync(zip, Buffer.from(await res.arrayBuffer()));
  execFileSync("unzip", ["-o", "-q", zip, `${NAME}.shp`, `${NAME}.dbf`, "-d", CACHE]);
}

// ---- Minimal shapefile readers (Polygon shapes + DBF attributes) ----
function readShp(buf) {
  const shapes = [];
  let off = 100;
  while (off < buf.length) {
    const len = buf.readInt32BE(off + 4) * 2;
    const rec = off + 8;
    const type = buf.readInt32LE(rec);
    if (type === 5) {
      const bbox = [buf.readDoubleLE(rec + 4), buf.readDoubleLE(rec + 12), buf.readDoubleLE(rec + 20), buf.readDoubleLE(rec + 28)];
      const nParts = buf.readInt32LE(rec + 36), nPts = buf.readInt32LE(rec + 40);
      const parts = [];
      for (let i = 0; i < nParts; i++) parts.push(buf.readInt32LE(rec + 44 + i * 4));
      const ptBase = rec + 44 + nParts * 4;
      const rings = parts.map((start, i) => {
        const end = i + 1 < nParts ? parts[i + 1] : nPts;
        const ring = new Float64Array((end - start) * 2);
        for (let j = start; j < end; j++) {
          ring[(j - start) * 2] = buf.readDoubleLE(ptBase + j * 16);
          ring[(j - start) * 2 + 1] = buf.readDoubleLE(ptBase + j * 16 + 8);
        }
        return ring;
      });
      shapes.push({ bbox, rings });
    } else {
      shapes.push(null);
    }
    off = rec + len;
  }
  return shapes;
}

function readDbf(buf) {
  const n = buf.readUInt32LE(4), headLen = buf.readUInt16LE(8), recLen = buf.readUInt16LE(10);
  const fields = [];
  for (let o = 32; buf[o] !== 0x0d; o += 32) {
    fields.push({ name: buf.toString("latin1", o, o + 11).replace(/\0.*$/, ""), len: buf[o + 16] });
  }
  const rows = [];
  for (let i = 0; i < n; i++) {
    let o = headLen + i * recLen + 1;
    const r = {};
    for (const f of fields) { r[f.name] = buf.toString("utf8", o, o + f.len).trim(); o += f.len; }
    rows.push(r);
  }
  return rows;
}

// Even-odd across all rings handles holes and multipart counties in one pass.
function contains(shape, x, y) {
  const [x0, y0, x1, y1] = shape.bbox;
  if (x < x0 || x > x1 || y < y0 || y > y1) return false;
  let inside = false;
  for (const r of shape.rings) {
    for (let i = 0, j = r.length - 2; i < r.length; j = i, i += 2) {
      const xi = r[i], yi = r[i + 1], xj = r[j], yj = r[j + 1];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}

// Edit projects.json as text so Python's formatting (e.g. `45.0`, \u escapes, no trailing
// newline — see ingest_eia.py) survives byte-for-byte: drop any prior county lines, then
// insert fresh ones right after each record's `"state"` line. Nothing else is touched.
function writeCounties(text, byId) {
  const out = [];
  let id = null;
  for (const line of text.split("\n")) {
    if (/^      "county_(name|fips)": /.test(line)) continue;
    out.push(line);
    const m = line.match(/^      "id": ("(?:[^"\\]|\\.)*"),$/);
    if (m) id = JSON.parse(m[1]);
    if (/^      "state": .*,$/.test(line) && byId.has(id)) {
      const c = byId.get(id);
      out.push(`      "county_name": ${JSON.stringify(c.name)},`, `      "county_fips": "${c.fips}",`);
    }
  }
  return out.join("\n");
}

async function main() {
  await ensureShapefile();
  const shapes = readShp(readFileSync(join(CACHE, NAME + ".shp")));
  const attrs = readDbf(readFileSync(join(CACHE, NAME + ".dbf")));
  const counties = shapes.map((s, i) => s && { ...s, ...attrs[i] }).filter(Boolean);

  const text = readFileSync(PROJECTS, "utf8");
  const data = JSON.parse(text);
  const stats = {}, misses = [], stateMismatch = [], byId = new Map();
  for (const p of data.projects) {
    const s = (stats[p.type] ||= { total: 0, matched: 0 });
    s.total++;
    const hit = (typeof p.lat === "number" && typeof p.lng === "number")
      ? counties.find(c => contains(c, p.lng, p.lat)) : null;
    if (!hit) { misses.push(p); continue; }
    s.matched++;
    if (p.state && hit.STUSPS !== p.state) stateMismatch.push({ p, c: hit });
    byId.set(p.id, { name: hit.NAMELSAD, fips: hit.GEOID });
  }

  console.log(`[county] ${counties.length} county polygons loaded`);
  for (const [type, s] of Object.entries(stats)) {
    console.log(`[county] ${type.padEnd(12)} ${s.matched}/${s.total} matched (${(100 * s.matched / s.total).toFixed(2)}%)`);
  }
  if (misses.length) {
    console.log(`[county] ${misses.length} unmatched (left without county fields):`);
    for (const p of misses) console.log(`  - ${p.id}  ${p.name}  ${p.city || ""}, ${p.state || ""}  (${p.lat}, ${p.lng})`);
  }
  if (stateMismatch.length) {
    console.log(`[county] ${stateMismatch.length} records whose coordinates fall in a different state than their state code:`);
    for (const { p, c } of stateMismatch) console.log(`  - ${p.id}  state=${p.state}  point in ${c.NAMELSAD}, ${c.STUSPS} (${c.GEOID})`);
  }

  if (CHECK) { console.log("[county] --check: projects.json not written"); return; }
  const next = writeCounties(text, byId);
  // Safety net: parsed result must equal the original plus exactly the county fields we computed.
  const after = JSON.parse(next).projects;
  after.forEach((p, i) => {
    const { county_name, county_fips, ...rest } = p;
    const { county_name: _n, county_fips: _f, ...orig } = data.projects[i];
    const want = byId.get(p.id);
    if (JSON.stringify(rest) !== JSON.stringify(orig) || county_name !== want?.name || county_fips !== want?.fips) {
      throw new Error(`county write check failed on ${p.id}`);
    }
  });
  writeFileSync(PROJECTS, next);
  console.log("[county] wrote projects.json");
}

main().catch(e => { console.error(e); process.exit(1); });
