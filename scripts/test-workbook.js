#!/usr/bin/env node
// Verifies the paid XLSX ships with the Data Center Pipeline and By County tabs,
// both at the expected row counts for the current projects.json. Uses the real
// exceljs library (unlike test-entitlement.js, which stubs it) because the thing
// under test here is the workbook itself.
//
//   node scripts/test-workbook.js

const assert = require("assert");
const path = require("path");
const ExcelJS = require("exceljs");

const { buildWorkbookBuffer, loadProjectsFromDisk } = require("../lib/dataset");

(async () => {
  const { projects, last_updated } = loadProjectsFromDisk();
  const buf = await buildWorkbookBuffer(projects, last_updated);
  const wb = new ExcelJS.Workbook();
  await wb.xlsx.load(buf);

  const names = wb.worksheets.map(ws => ws.name);
  const expected = ["Read me", "Hunting List", "Data Center Pipeline", "All Projects", "By State", "By County"];
  for (const name of expected) {
    assert.ok(names.includes(name), `missing tab "${name}" (have ${JSON.stringify(names)})`);
  }
  // Order check: Pipeline sits right after Hunting List; By County is the last tab.
  assert.strictEqual(names[names.indexOf("Hunting List") + 1], "Data Center Pipeline",
    "Data Center Pipeline must follow Hunting List");
  assert.strictEqual(names[names.length - 1], "By County", "By County must be the last tab");

  const dcCount = projects.filter(p => p.type === "data_center").length;
  const pipe = wb.getWorksheet("Data Center Pipeline");
  assert.strictEqual(pipe.rowCount, dcCount + 1,
    `Data Center Pipeline rows: expected header + ${dcCount}, got ${pipe.rowCount}`);
  // Pipeline statuses rank above operating: the first data row's status is not 'Operating'
  // unless every DC record is operating (currently 63 are pipeline).
  const firstStatus = pipe.getRow(2).getCell("H").value; // Status column
  const pipelineDc = projects.filter(p => p.type === "data_center"
    && (p.status === "under_construction" || p.status === "planned" || p.status === "announced"));
  if (pipelineDc.length > 0) {
    assert.notStrictEqual(firstStatus, "Operating",
      `expected a pipeline status at row 2, got "${firstStatus}"`);
  }

  const fips = new Set(projects.map(p => p.county_fips).filter(Boolean));
  const byc = wb.getWorksheet("By County");
  assert.strictEqual(byc.rowCount, fips.size + 1,
    `By County rows: expected header + ${fips.size}, got ${byc.rowCount}`);
  // Sorted by Total MW descending. Column keys don't survive a load, so address by index
  // (By County column order: State=1, County=2, FIPS=3, Projects=4, Total MW=5).
  assert.strictEqual(byc.getRow(1).getCell(5).value, "Total MW", "By County column 5 is not Total MW");
  const mws = [];
  byc.eachRow((row, n) => { if (n > 1) mws.push(row.getCell(5).value || 0); });
  for (let i = 1; i < mws.length; i++) {
    assert.ok(mws[i - 1] >= mws[i], `By County not MW-desc at row ${i + 2}: ${mws[i - 1]} < ${mws[i]}`);
  }

  // Both new tabs carry a freeze on the header and an autofilter.
  for (const ws of [pipe, byc]) {
    assert.ok(ws.views && ws.views[0] && ws.views[0].ySplit === 1, `${ws.name}: header not frozen`);
    assert.ok(ws.autoFilter, `${ws.name}: autofilter not set`);
  }

  // Announced column formatter: one known-day row should render "Mon D, YYYY".
  const dayDc = projects.find(p => p.type === "data_center"
    && p.announcement_date_precision === "day" && p.announcement_date);
  if (dayDc) {
    const match = projects.filter(p => p.type === "data_center").indexOf(dayDc); // not the right row
    // Find by scanning: Announced column is I (9); match by Campus name in column A.
    let hit = null;
    pipe.eachRow((row, n) => { if (n > 1 && row.getCell(1).value === dayDc.name) hit = row; });
    assert.ok(hit, `could not find row for ${dayDc.name} in Data Center Pipeline`);
    const announced = hit.getCell(9).value;
    assert.ok(/^[A-Z][a-z]{2} \d{1,2}, \d{4}$/.test(String(announced)),
      `Announced cell for ${dayDc.name} not formatted as "Mon D, YYYY": ${announced}`);
  }

  // Nothing in projects.json should still reference ids that record_fixes.json removed.
  // If the fix pipeline is skipped, or rerun with a stale ingest, this guard catches it.
  const REMOVED_IDS = [
    "dc-crusoe-tulsa", "dc-aws-pendleton", "dc-aws-marion", "dc-aws-pflugerville",
    "dc-aws-surprise", "dc-crusoe-anson",
    // Renamed (old id must not come back either):
    "dc-coreweave-kenosha", "dc-equinix-ny9",
  ];
  const ids = new Set(projects.map(p => p.id));
  for (const id of REMOVED_IDS) {
    assert.ok(!ids.has(id), `removed id "${id}" is still in projects.json`);
  }

  console.log(`  ✓ XLSX has ${expected.length} tabs in the right order`);
  console.log(`  ✓ Data Center Pipeline: ${dcCount} rows`);
  console.log(`  ✓ By County: ${fips.size} rows, sorted by MW desc`);
  console.log(`  ✓ no removed ids present in projects.json`);
  console.log(`\n  4/4 passed\n`);
})().catch(err => { console.error(err); process.exit(1); });
