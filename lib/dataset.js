// Shared dataset helpers for the paid export functions.
// Fetches the (public) projects.json and converts it to enriched CSV / GeoJSON.
// The value of the paid file is convenience + enrichment + a license, not secrecy.

const STATE_NAMES = {
  AL:"Alabama",AK:"Alaska",AZ:"Arizona",AR:"Arkansas",CA:"California",CO:"Colorado",
  CT:"Connecticut",DE:"Delaware",FL:"Florida",GA:"Georgia",HI:"Hawaii",ID:"Idaho",
  IL:"Illinois",IN:"Indiana",IA:"Iowa",KS:"Kansas",KY:"Kentucky",LA:"Louisiana",
  ME:"Maine",MD:"Maryland",MA:"Massachusetts",MI:"Michigan",MN:"Minnesota",
  MS:"Mississippi",MO:"Missouri",MT:"Montana",NE:"Nebraska",NV:"Nevada",
  NH:"New Hampshire",NJ:"New Jersey",NM:"New Mexico",NY:"New York",NC:"North Carolina",
  ND:"North Dakota",OH:"Ohio",OK:"Oklahoma",OR:"Oregon",PA:"Pennsylvania",
  RI:"Rhode Island",SC:"South Carolina",SD:"South Dakota",TN:"Tennessee",TX:"Texas",
  UT:"Utah",VT:"Vermont",VA:"Virginia",WA:"Washington",WV:"West Virginia",
  WI:"Wisconsin",WY:"Wyoming",DC:"District of Columbia",PR:"Puerto Rico",
};

const TYPE_LABEL = { data_center: "Data center", bess: "Battery storage (BESS)" };
const STATUS_LABEL = {
  operating: "Operating", under_construction: "Under construction",
  planned: "Planned", announced: "Announced",
};

async function fetchProjects(origin) {
  const base = origin || "https://usenergymap.com";
  const res = await fetch(base + "/projects.json", { headers: { "cache-control": "no-cache" } });
  if (!res.ok) throw new Error("Could not load projects.json (" + res.status + ")");
  const data = await res.json();
  return { projects: data.projects || [], last_updated: data.last_updated || null };
}

// Enriched, flat row for each project (adds derived columns the raw JSON doesn't have).
function enrich(p) {
  const duration = (p.type === "bess" && p.capacity_mw && p.capacity_mwh)
    ? +(p.capacity_mwh / p.capacity_mw).toFixed(2) : "";
  return {
    id: p.id,
    name: p.name,
    type: p.type,
    type_label: TYPE_LABEL[p.type] || p.type,
    operator: p.operator || "",
    city: p.city || "",
    state: p.state || "",
    state_name: STATE_NAMES[p.state] || p.state || "",
    latitude: p.lat,
    longitude: p.lng,
    capacity_mw: p.capacity_mw != null ? p.capacity_mw : "",
    capacity_mwh: p.capacity_mwh != null ? p.capacity_mwh : "",
    duration_hours: duration,
    status: p.status,
    status_label: STATUS_LABEL[p.status] || p.status,
    year_online: p.year_online != null ? p.year_online : "",
    source: p.source || "",
  };
}

function csvCell(v) {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function toCSV(projects) {
  const rows = projects.map(enrich);
  const cols = Object.keys(rows[0]);
  const head = cols.join(",");
  const body = rows.map(r => cols.map(c => csvCell(r[c])).join(",")).join("\n");
  return head + "\n" + body + "\n";
}

function toGeoJSON(projects, last_updated) {
  return JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      source: "US Energy Map (usenergymap.com)",
      generated: new Date().toISOString(),
      dataset_last_updated: last_updated,
      count: projects.length,
      license: "Single-user license. Redistribution prohibited. See usenergymap.com/data.",
    },
    features: projects.map(p => {
      const e = enrich(p);
      return {
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lng, p.lat] },
        properties: e,
      };
    }),
  }, null, 2);
}

// ---- Business-development "hunting list" aggregation ----

function aggregateOperators(projects) {
  const map = new Map();
  for (const p of projects) {
    const op = p.operator || "(unknown)";
    let a = map.get(op);
    if (!a) { a = { operator: op, projects: 0, mw: 0, dc: 0, bess: 0, states: new Set(),
                    operating: 0, under_construction: 0, planned: 0, top_name: "", top_mw: -1 }; map.set(op, a); }
    a.projects += 1;
    a.mw += p.capacity_mw || 0;
    if (p.type === "data_center") a.dc += 1; else a.bess += 1;
    if (p.state) a.states.add(p.state);
    if (p.status && a[p.status] != null) a[p.status] += 1;
    if ((p.capacity_mw || 0) > a.top_mw) { a.top_mw = p.capacity_mw || 0; a.top_name = p.name; }
  }
  return [...map.values()]
    .map(a => ({ ...a, states: a.states.size }))
    .sort((x, y) => y.mw - x.mw);
}

function aggregateStates(projects) {
  const map = new Map();
  for (const p of projects) {
    const s = p.state || "(unknown)";
    let a = map.get(s);
    if (!a) { a = { state: s, state_name: STATE_NAMES[s] || s, projects: 0, mw: 0, dc: 0, bess: 0,
                    operating: 0, under_construction: 0, planned: 0 }; map.set(s, a); }
    a.projects += 1; a.mw += p.capacity_mw || 0;
    if (p.type === "data_center") a.dc += 1; else a.bess += 1;
    if (p.status && a[p.status] != null) a[p.status] += 1;
  }
  return [...map.values()].sort((x, y) => y.mw - x.mw);
}

// Builds the BD hunting-list workbook and returns an .xlsx Buffer.
async function buildWorkbookBuffer(projects, last_updated) {
  const ExcelJS = require("exceljs");
  const wb = new ExcelJS.Workbook();
  wb.creator = "US Energy Map";
  wb.created = new Date();

  const BLUE = "FF2563EB", DARK = "FF0B1224", LIGHT = "FFF1F5F9";
  const headerStyle = (row) => {
    row.font = { bold: true, color: { argb: "FFFFFFFF" }, size: 11 };
    row.fill = { type: "pattern", pattern: "solid", fgColor: { argb: BLUE } };
    row.alignment = { vertical: "middle" };
    row.height = 20;
  };
  const num = "#,##0";

  // ---- Cover / Read me ----
  const cover = wb.addWorksheet("Read me", { properties: { tabColor: { argb: BLUE } } });
  cover.columns = [{ width: 22 }, { width: 80 }];
  cover.addRow(["US Energy Map", "Business-Development Hunting List"]);
  cover.getRow(1).font = { bold: true, size: 16, color: { argb: DARK } };
  cover.addRow([]);
  cover.addRow(["Generated", new Date().toISOString().slice(0, 10)]);
  cover.addRow(["Dataset updated", last_updated || "—"]);
  cover.addRow(["Total projects", projects.length]);
  cover.addRow(["Total capacity (MW)", Math.round(projects.reduce((s, p) => s + (p.capacity_mw || 0), 0))]);
  cover.addRow(["Operators (leads)", new Set(projects.map(p => p.operator || "(unknown)")).size]);
  cover.addRow([]);
  cover.addRow(["How to use", "‘Hunting List’ ranks operators by tracked capacity — your prospect list. Fill the blank BD columns (Website, Contact, Priority, Status, Notes) as you work it."]);
  cover.addRow(["", "‘All Projects’ is the full record. ‘By State’ helps you territory-plan."]);
  cover.addRow(["Sources", "Battery data: U.S. EIA-860M (monthly). Data centers: hand-curated from operator filings. See usenergymap.com/methodology."]);
  cover.addRow(["License", "Single-user. Don’t redistribute or resell the file. Team/commercial license: hello@grandroma.com"]);
  [9, 11, 12].forEach(r => { cover.getRow(r).getCell(1).font = { bold: true }; });

  // ---- Hunting List (operators) ----
  const hl = wb.addWorksheet("Hunting List", { properties: { tabColor: { argb: BLUE } }, views: [{ state: "frozen", ySplit: 1 }] });
  const hlCols = [
    { header: "Rank", key: "rank", width: 6 },
    { header: "Operator", key: "operator", width: 34 },
    { header: "Projects", key: "projects", width: 10 },
    { header: "Total MW", key: "mw", width: 12 },
    { header: "Data Centers", key: "dc", width: 13 },
    { header: "BESS", key: "bess", width: 8 },
    { header: "States", key: "states", width: 8 },
    { header: "Largest Project", key: "top_name", width: 30 },
    { header: "Largest MW", key: "top_mw", width: 12 },
    { header: "Operating", key: "operating", width: 11 },
    { header: "Under Constr.", key: "under_construction", width: 13 },
    { header: "Planned", key: "planned", width: 9 },
    { header: "Website", key: "c_web", width: 22 },
    { header: "HQ / Location", key: "c_hq", width: 18 },
    { header: "Key Contact", key: "c_contact", width: 20 },
    { header: "Title", key: "c_title", width: 18 },
    { header: "Email / LinkedIn", key: "c_email", width: 26 },
    { header: "Priority", key: "c_priority", width: 10 },
    { header: "Outreach Status", key: "c_status", width: 15 },
    { header: "Notes", key: "c_notes", width: 40 },
  ];
  hl.columns = hlCols;
  headerStyle(hl.getRow(1));
  aggregateOperators(projects).forEach((o, i) => {
    hl.addRow({ rank: i + 1, operator: o.operator, projects: o.projects, mw: Math.round(o.mw),
      dc: o.dc, bess: o.bess, states: o.states, top_name: o.top_name, top_mw: Math.round(o.top_mw),
      operating: o.operating, under_construction: o.under_construction, planned: o.planned });
  });
  ["mw", "top_mw"].forEach(k => { hl.getColumn(k).numFmt = num; });
  hl.autoFilter = { from: "A1", to: "L1" };
  hl.eachRow((row, n) => { if (n > 1 && n % 2 === 0) row.eachCell(c => { if (!c.fill || !c.fill.fgColor) c.fill = { type: "pattern", pattern: "solid", fgColor: { argb: LIGHT } }; }); });

  // ---- All Projects ----
  const ap = wb.addWorksheet("All Projects", { views: [{ state: "frozen", ySplit: 1 }] });
  const rows = projects.map(enrich);
  const cols = Object.keys(rows[0]);
  ap.columns = cols.map(c => ({ header: c, key: c, width: c === "name" || c === "source" ? 32 : (c === "operator" ? 26 : 14) }));
  headerStyle(ap.getRow(1));
  rows.forEach(r => ap.addRow(r));
  ["capacity_mw", "capacity_mwh"].forEach(k => { ap.getColumn(k).numFmt = num; });
  ap.autoFilter = { from: "A1", to: ap.getColumn(cols.length).letter + "1" };

  // ---- By State ----
  const st = wb.addWorksheet("By State", { views: [{ state: "frozen", ySplit: 1 }] });
  st.columns = [
    { header: "State", key: "state", width: 8 },
    { header: "State name", key: "state_name", width: 20 },
    { header: "Projects", key: "projects", width: 10 },
    { header: "Total MW", key: "mw", width: 12 },
    { header: "Data Centers", key: "dc", width: 13 },
    { header: "BESS", key: "bess", width: 8 },
    { header: "Operating", key: "operating", width: 11 },
    { header: "Under Constr.", key: "under_construction", width: 13 },
    { header: "Planned", key: "planned", width: 9 },
  ];
  headerStyle(st.getRow(1));
  aggregateStates(projects).forEach(s => st.addRow({ ...s, mw: Math.round(s.mw) }));
  st.getColumn("mw").numFmt = num;
  st.autoFilter = { from: "A1", to: "I1" };

  return wb.xlsx.writeBuffer();
}

module.exports = { fetchProjects, toCSV, toGeoJSON, buildWorkbookBuffer, aggregateOperators, STATE_NAMES };
