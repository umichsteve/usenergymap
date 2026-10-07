#!/usr/bin/env node
// Adds announcement_date + announcement_date_precision to data_center records in
// projects.json from the hand-researched source of truth, data/research/dc_announcement_dates.csv.
//
//     node scripts/add-announcement-dates.mjs            # write projects.json + research/record-review.md
//     node scripts/add-announcement-dates.mjs --check    # report only, write nothing
//
// Only rows with publish=yes are written. evidence_url / confidence / note stay in the CSV.
// Data center records aren't rebuilt by ingest_eia.py (it keeps every non-eia- record verbatim),
// so this is a one-time enrichment — but it's idempotent: prior announcement lines are dropped
// and re-inserted from the CSV on every run, so re-running yields identical output.
// publish=no rows are listed in research/record-review.md for manual follow-up; their
// records are left untouched.

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PROJECTS = join(ROOT, "projects.json");
const CSV = join(ROOT, "data", "research", "dc_announcement_dates.csv");
const REVIEW = join(ROOT, "research", "record-review.md");
const CHECK = process.argv.includes("--check");
const PRECISIONS = new Set(["day", "month", "year"]);

// RFC 4180 parser: quoted fields, "" escapes, embedded commas/newlines.
function parseCSV(text) {
  const rows = [];
  let row = [], field = "", q = false;
  text = text.replace(/^﻿/, "");
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') q = false;
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  const [head, ...body] = rows.filter(r => r.some(v => v.trim() !== ""));
  const cols = head.map(h => h.trim());
  return body.map(r => Object.fromEntries(cols.map((c, i) => [c, (r[i] ?? "").trim()])));
}

// Normalise to YYYY-MM-DD; month/year precision pads to the first of the period.
function normDate(raw, precision) {
  const m = raw.match(/^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$/);
  if (!m) return null;
  const [, y, mo = "01", d = "01"] = m;
  if (precision === "day" && !m[3]) return null;
  if (precision === "month" && !m[2]) return null;
  const iso = `${y}-${mo}-${d}`;
  const dt = new Date(iso + "T00:00:00Z");
  return !isNaN(dt) && dt.toISOString().slice(0, 10) === iso ? iso : null;
}

// Edit projects.json as text so Python's formatting survives byte-for-byte (same approach as
// add-county.mjs): drop any prior announcement lines, then insert fresh ones after the
// record's "year_online" line (or "status" when a record has no year_online). If the anchor
// is the record's last field, it gains a comma and the inserted precision line goes without one.
function writeDates(text, byId) {
  const lines = text.split("\n").filter(l => !/^      "announcement_date(_precision)?": /.test(l));
  // Strip any trailing comma we added to an anchor line on a previous run.
  for (let i = 0; i < lines.length - 1; i++) {
    if (/^      "(year_online|status)": .*,$/.test(lines[i]) && /^    }/.test(lines[i + 1])) {
      lines[i] = lines[i].slice(0, -1);
    }
  }
  const out = [];
  let id = null, hasYear = false, pending = null;
  const flush = () => { if (pending) { out.splice(pending.at, 0, ...pending.lines); pending = null; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^    {$/.test(line)) { id = null; hasYear = false; pending = null; }
    const m = line.match(/^      "id": ("(?:[^"\\]|\\.)*"),?$/);
    if (m) id = JSON.parse(m[1]);
    const anchor = line.match(/^      "(year_online|status)": /);
    if (anchor && byId.has(id) && (anchor[1] === "year_online" || !hasYear)) {
      if (anchor[1] === "year_online") hasYear = true;
      const last = !line.endsWith(",");
      const d = byId.get(id);
      out.push(last ? line + "," : line);
      pending = {
        at: out.length,
        lines: [
          `      "announcement_date": "${d.date}",`,
          `      "announcement_date_precision": "${d.precision}"${last ? "" : ","}`,
        ],
      };
      if (anchor[1] === "year_online") flush();
      continue;
    }
    if (/^    }/.test(line)) flush();
    out.push(line);
  }
  return out.join("\n");
}

function main() {
  const rows = parseCSV(readFileSync(CSV, "utf8"));
  const text = readFileSync(PROJECTS, "utf8");
  const data = JSON.parse(text);
  const projects = new Map(data.projects.map(p => [p.id, p]));

  const byId = new Map(), unpublished = [], errors = [];
  const seen = new Set();
  for (const r of rows) {
    const p = projects.get(r.id);
    if (!p) { errors.push(`${r.id}: no such record in projects.json`); continue; }
    if (p.type !== "data_center") { errors.push(`${r.id}: not a data_center record`); continue; }
    if (seen.has(r.id)) { errors.push(`${r.id}: duplicate row in CSV`); continue; }
    seen.add(r.id);
    const publish = r.publish.toLowerCase();
    if (publish !== "yes") { unpublished.push({ r, p }); continue; }
    const precision = r.date_precision.toLowerCase();
    if (!PRECISIONS.has(precision)) { errors.push(`${r.id}: bad date_precision "${r.date_precision}"`); continue; }
    const date = normDate(r.announcement_date, precision);
    if (!date) { errors.push(`${r.id}: bad announcement_date "${r.announcement_date}" for precision ${precision}`); continue; }
    byId.set(r.id, { date, precision });
  }
  const dcIds = data.projects.filter(p => p.type === "data_center").map(p => p.id);
  const missing = dcIds.filter(id => !seen.has(id));

  const precCount = {};
  for (const d of byId.values()) precCount[d.precision] = (precCount[d.precision] || 0) + 1;
  console.log(`[announce] ${rows.length} CSV rows, ${dcIds.length} data_center records`);
  console.log(`[announce] publish=yes: ${byId.size} to write (${Object.entries(precCount).map(([k, v]) => `${k} ${v}`).join(", ")})`);
  console.log(`[announce] publish=no:  ${unpublished.length} (listed in research/record-review.md)`);
  if (missing.length) console.log(`[announce] ${missing.length} data_center records not in CSV: ${missing.join(", ")}`);
  if (errors.length) {
    console.log(`[announce] ${errors.length} errors:`);
    for (const e of errors) console.log(`  - ${e}`);
    process.exit(1);
  }

  const review = buildReview(unpublished);
  if (CHECK) { console.log("[announce] --check: nothing written"); return; }

  const next = writeDates(text, byId);
  // Safety net: parsed result must equal the original plus exactly the two fields we computed.
  const after = JSON.parse(next).projects;
  if (after.length !== data.projects.length) throw new Error("record count changed");
  after.forEach((p, i) => {
    const { announcement_date, announcement_date_precision, ...rest } = p;
    const { announcement_date: _a, announcement_date_precision: _b, ...orig } = data.projects[i];
    const want = byId.get(p.id);
    if (JSON.stringify(rest) !== JSON.stringify(orig) ||
        announcement_date !== want?.date || announcement_date_precision !== want?.precision) {
      throw new Error(`announcement write check failed on ${p.id}`);
    }
  });
  writeFileSync(PROJECTS, next);
  writeFileSync(REVIEW, review);
  console.log(`[announce] wrote ${byId.size} records to projects.json and research/record-review.md`);
}

function buildReview(unpublished) {
  const cell = s => String(s ?? "").replace(/\|/g, "\\|").replace(/\s*\n\s*/g, " ");
  const isRecordCheck = ({ r }) => /record check/i.test(`${r.review} ${r.note}`);
  const isAfterOnline = ({ r, p }) =>
    !isRecordCheck({ r }) && p.year_online != null && /^\d{4}/.test(r.announcement_date) &&
    +r.announcement_date.slice(0, 4) > p.year_online;
  const groups = [
    ["RECORD CHECK — the record itself may be wrong (name, site, status or existence)", unpublished.filter(isRecordCheck)],
    ["Announcement date after year_online — date or year_online needs reconciling", unpublished.filter(isAfterOnline)],
    ["Other publish=no rows", unpublished.filter(u => !isRecordCheck(u) && !isAfterOnline(u))],
  ];
  const out = [
    "# Data center record review",
    "",
    "Generated by `scripts/add-announcement-dates.mjs` from `data/research/dc_announcement_dates.csv`.",
    `Every row with \`publish=no\` (${unpublished.length} total) is listed here. None of these records were changed —`,
    "fix the record or the CSV row, flip `publish` to `yes`, and re-run the script.",
    "",
  ];
  for (const [title, list] of groups) {
    if (!list.length) continue;
    out.push(`## ${title} (${list.length})`, "",
      "| Done | id | name | year_online | CSV date | precision | confidence | review | note | evidence |",
      "|---|---|---|---|---|---|---|---|---|---|");
    for (const { r, p } of list) {
      out.push(`| [ ] | \`${p.id}\` | ${cell(p.name)} | ${p.year_online ?? ""} | ${cell(r.announcement_date)} | ${cell(r.date_precision)} | ${cell(r.confidence)} | ${cell(r.review)} | ${cell(r.note)} | ${r.evidence_url ? `[link](${r.evidence_url})` : ""} |`);
    }
    out.push("");
  }
  return out.join("\n");
}

main();
