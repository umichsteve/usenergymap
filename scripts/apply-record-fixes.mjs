#!/usr/bin/env node
// Applies data/research/record_fixes.json to projects.json.
//
//   node scripts/apply-record-fixes.mjs            # apply and write
//   node scripts/apply-record-fixes.mjs --check    # dry run — print the before/after diff only
//
// Same text-edit approach as add-county.mjs so projects.json keeps Python's json.dumps byte
// layout (ASCII \uXXXX escapes, trailing-newline-free, exact float repr) — the ingest and
// changelog pipelines compare the file as bytes elsewhere.
//
// Shape of each fix:
//   { "id": "<existing id>", "action": "FIX"|"REMOVE", "new_id": "<new>"|null,
//     "fields": { "<key>": <value>|null, ... }, "sources": [...], "rationale": "..." }
//
// - REMOVE: drops the record.
// - FIX: for each field, non-null writes/overwrites, null deletes it. `new_id` renames the id.
// Script fails on an unknown id or a field name no record currently carries.

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { stampLastUpdated, todayUtc } from "./_last-updated.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PROJECTS = join(ROOT, "projects.json");
const FIXES = join(ROOT, "data", "research", "record_fixes.json");
const CHECK = process.argv.includes("--check");

// Python json.dumps(indent=2) ASCII-safe string encoding, matching ingest_eia.py.
function pyString(s) {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const code = s.charCodeAt(i);
    if (code === 0x22) out += '\\"';
    else if (code === 0x5c) out += "\\\\";
    else if (code === 0x08) out += "\\b";
    else if (code === 0x09) out += "\\t";
    else if (code === 0x0a) out += "\\n";
    else if (code === 0x0c) out += "\\f";
    else if (code === 0x0d) out += "\\r";
    else if (code < 0x20 || code > 0x7e) out += `\\u${code.toString(16).padStart(4, "0")}`;
    else out += s[i];
  }
  return out + '"';
}

function pyValue(v) {
  if (v === null) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return pyString(v);
  throw new Error(`Cannot serialize ${typeof v}: ${JSON.stringify(v)}`);
}

// Splits projects.json text into { prologue, records, epilogue } where each record is the
// raw text of one `{ ... }` block (no trailing comma). Nested structures aren't supported —
// the schema here is flat scalars only.
function splitRecords(text) {
  const marker = '  "projects": [\n';
  const bodyStart = text.indexOf(marker);
  if (bodyStart < 0) throw new Error('Could not find "projects": [');
  const after = bodyStart + marker.length;
  const endMarker = "\n  ]";
  const bodyEnd = text.lastIndexOf(endMarker);
  if (bodyEnd < 0 || bodyEnd < after) throw new Error("Could not find end of projects array");
  const prologue = text.slice(0, after);
  const epilogue = text.slice(bodyEnd);
  const body = text.slice(after, bodyEnd);

  const records = [];
  let i = 0;
  while (i < body.length) {
    const open = body.indexOf("    {\n", i);
    if (open < 0) break;
    // Find the first "    }" that starts at a line boundary after `open`.
    let scan = open + 6;
    let closeIdx = -1;
    while (scan < body.length) {
      const c = body.indexOf("    }", scan);
      if (c < 0) break;
      if (c === 0 || body[c - 1] === "\n") { closeIdx = c; break; }
      scan = c + 1;
    }
    if (closeIdx < 0) throw new Error(`Unclosed record starting at offset ${open}`);
    const recText = body.slice(open, closeIdx + 5);
    const m = recText.match(/^      "id": "([^"\\]+)",/m);
    if (!m) throw new Error(`Record has no simple id: ${recText.slice(0, 80)}`);
    records.push({ id: m[1], text: recText });
    i = closeIdx + 5;
    if (body[i] === ",") i++;
    if (body[i] === "\n") i++;
  }
  return { prologue, records, epilogue };
}

const FIELD_LINE = /^      "([^"]+)": (.+?),?$/;

function parseFields(recText) {
  const lines = recText.split("\n");
  if (!/^    \{$/.test(lines[0]) || !/^    \}$/.test(lines[lines.length - 1])) {
    throw new Error(`Record is not formatted as expected`);
  }
  const ordered = [];
  for (let i = 1; i < lines.length - 1; i++) {
    const m = lines[i].match(FIELD_LINE);
    if (!m) throw new Error(`Unexpected line in record: ${lines[i]}`);
    ordered.push({ key: m[1], raw: m[2] });
  }
  return ordered;
}

function serializeRecord(ordered) {
  const out = ["    {"];
  for (let i = 0; i < ordered.length; i++) {
    const comma = i < ordered.length - 1 ? "," : "";
    out.push(`      ${pyString(ordered[i].key)}: ${ordered[i].raw}${comma}`);
  }
  out.push("    }");
  return out.join("\n");
}

function applyFix(recText, fields, newId, knownKeys) {
  const ordered = parseFields(recText);
  const idx = new Map(ordered.map((o, i) => [o.key, i]));

  for (const [key, value] of Object.entries(fields)) {
    if (!knownKeys.has(key)) throw new Error(`field "${key}" is not used by any record in projects.json`);
    if (value === null) {
      if (idx.has(key)) ordered[idx.get(key)] = null;
      continue;
    }
    const raw = pyValue(value);
    if (idx.has(key)) {
      ordered[idx.get(key)].raw = raw;
    } else {
      ordered.push({ key, raw });
      idx.set(key, ordered.length - 1);
    }
  }
  if (newId) {
    const idEntry = ordered.find(o => o && o.key === "id");
    if (!idEntry) throw new Error('record has no "id" field');
    idEntry.raw = pyValue(newId);
  }

  const kept = ordered.filter(Boolean);
  return serializeRecord(kept);
}

function unifiedDiff(before, after) {
  const bl = before.split("\n"), al = after.split("\n");
  const out = [];
  let i = 0, j = 0;
  while (i < bl.length || j < al.length) {
    if (i < bl.length && j < al.length && bl[i] === al[j]) { i++; j++; continue; }
    // scan ahead for next match (small, flat records — linear is fine)
    let foundA = -1;
    for (let a = j; a < al.length && a < j + 50; a++) {
      if (bl[i] !== undefined && bl[i] === al[a]) { foundA = a; break; }
    }
    if (foundA >= 0) {
      while (j < foundA) out.push(`+ ${al[j++]}`);
      continue;
    }
    let foundB = -1;
    for (let b = i; b < bl.length && b < i + 50; b++) {
      if (al[j] !== undefined && al[j] === bl[b]) { foundB = b; break; }
    }
    if (foundB >= 0) {
      while (i < foundB) out.push(`- ${bl[i++]}`);
      continue;
    }
    if (i < bl.length) out.push(`- ${bl[i++]}`);
    if (j < al.length) out.push(`+ ${al[j++]}`);
  }
  return out.join("\n");
}

function main() {
  const fixes = JSON.parse(readFileSync(FIXES, "utf8"));
  const text = readFileSync(PROJECTS, "utf8");
  const { prologue, records, epilogue } = splitRecords(text);

  const knownKeys = new Set();
  for (const r of records) {
    for (const line of r.text.split("\n")) {
      const m = line.match(FIELD_LINE);
      if (m) knownKeys.add(m[1]);
    }
  }

  const byId = new Map(records.map((r, i) => [r.id, i]));

  const errors = [];
  const seen = new Set();
  let nFix = 0, nRemove = 0;

  for (const fix of fixes) {
    if (!fix.id) { errors.push(`entry has no id`); continue; }
    if (seen.has(fix.id)) { errors.push(`${fix.id}: duplicate in record_fixes.json`); continue; }
    seen.add(fix.id);
    if (!byId.has(fix.id)) { errors.push(`${fix.id}: no such record in projects.json`); continue; }

    const i = byId.get(fix.id);
    const before = records[i].text;

    if (fix.action === "REMOVE") {
      console.log(`\n=== REMOVE ${fix.id} ===`);
      console.log(before.split("\n").map(l => `- ${l}`).join("\n"));
      records[i] = null;
      nRemove++;
    } else if (fix.action === "FIX") {
      try {
        const after = applyFix(before, fix.fields || {}, fix.new_id || null, knownKeys);
        const label = fix.new_id ? `${fix.id} -> ${fix.new_id}` : fix.id;
        console.log(`\n=== FIX ${label} ===`);
        console.log(unifiedDiff(before, after));
        records[i] = { id: fix.new_id || fix.id, text: after };
        if (fix.new_id) byId.set(fix.new_id, i);
        nFix++;
      } catch (e) {
        errors.push(`${fix.id}: ${e.message}`);
      }
    } else {
      errors.push(`${fix.id}: unknown action "${fix.action}"`);
    }
  }

  if (errors.length) {
    console.error("\nErrors:");
    for (const e of errors) console.error(`  - ${e}`);
    process.exit(1);
  }

  const keptRecords = records.filter(Boolean);
  let outText = prologue + keptRecords.map(r => r.text).join(",\n") + epilogue;
  if (outText !== text) outText = stampLastUpdated(outText, todayUtc());

  // Parse-check + id-uniqueness: fail loudly if the serializer or the fixes produced duplicates.
  const parsed = JSON.parse(outText);
  if (parsed.projects.length !== keptRecords.length) {
    throw new Error(`parsed count ${parsed.projects.length} != kept ${keptRecords.length}`);
  }
  const ids = parsed.projects.map(p => p.id);
  const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
  if (dupes.length) throw new Error(`duplicate ids after fixes: ${[...new Set(dupes)].join(", ")}`);

  console.log(`\n${nFix} fixed · ${nRemove} removed · ${parsed.projects.length} total records.`);

  if (CHECK) {
    console.log("--check: projects.json not written");
    return;
  }
  writeFileSync(PROJECTS, outText);
  console.log("wrote projects.json");
}

main();
