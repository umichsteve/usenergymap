// Change-log helpers for the subscriber download.
// changelog.json is committed by the monthly workflow (scripts/build_changelog.py).
// The public /changelog page reads the summary client-side; the row-level CSV is gated.

async function fetchChangelog(origin) {
  const base = origin || "https://usenergymap.com";
  const res = await fetch(base + "/changelog.json", { headers: { "cache-control": "no-cache" } });
  if (!res.ok) throw new Error("Could not load changelog.json (" + res.status + ")");
  const data = await res.json();
  return { ...data, from: data.summary && data.summary.from, to: data.summary && data.summary.to };
}

function esc(v) {
  if (v === null || v === undefined) return "";
  const s = String(v);
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

// One row per (project, field) for changed rows; one row for added/removed.
function changelogToCSV(log) {
  const head = ["period_from", "period_to", "kind", "id", "name", "type", "operator", "city", "state",
                "status", "capacity_mw", "capacity_mwh", "year_online", "field", "from", "to"];
  const rows = [head.join(",")];
  const s = log.summary || {};
  for (const c of log.changes || []) {
    const base = [s.from, s.to, c.kind, c.id, c.name, c.type, c.operator, c.city, c.state,
                  c.status, c.capacity_mw, c.capacity_mwh, c.year_online];
    if (c.kind === "changed" && Array.isArray(c.fields) && c.fields.length) {
      for (const f of c.fields) rows.push([...base, f.field, f.from, f.to].map(esc).join(","));
    } else {
      rows.push([...base, "", "", ""].map(esc).join(","));
    }
  }
  return rows.join("\n") + "\n";
}

module.exports = { fetchChangelog, changelogToCSV };
