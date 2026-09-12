// Offline stand-in for `exceljs`, used only by scripts/test-entitlement.js so that
// lib/dataset.js loads without node_modules. The xlsx bytes are not under test here;
// the authorization decision in front of them is.
class Workbook {
  constructor() { this.xlsx = { writeBuffer: async () => Buffer.from("xlsx-stub") }; this.creator = ""; }
  addWorksheet() {
    return {
      columns: [], addRow() { return { eachCell() {}, getCell: () => ({}) }; },
      getRow: () => ({ eachCell() {}, font: {}, fill: {} }),
      eachRow() {}, autoFilter: null, views: [],
    };
  }
}
module.exports = { Workbook };
