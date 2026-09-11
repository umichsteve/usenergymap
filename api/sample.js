// GET /api/sample?format=csv
//
// The free sample: the 100 largest tracked projects, as CSV. Public and unauthenticated
// on purpose — every row is already visible on the map and the state pages, so this
// gives away nothing new. What it buys is a file the visitor can open in Excel, which
// is the thing the paid tiers actually sell.
//
// Deliberately not the full dataset and deliberately not the change log: those are
// /api/download, which verifies a Stripe session first.

const fs = require("fs");
const path = require("path");
const { fetchProjects, toCSV } = require("../lib/dataset");

const SAMPLE_SIZE = 100;

// projects.json ships in the same deployment as this function, so read it off disk
// rather than over HTTP to our own origin. The HTTP path (lib/dataset.fetchProjects,
// which the paid /api/download uses) fails on any deployment with Vercel Authentication
// on: the function's own unauthenticated request gets the protection login page and
// JSON.parse sees "<!DOCTYPE". Disk also drops a round trip and cannot drift from the
// build being served. vercel.json pins the file into the bundle via includeFiles.
// fetchProjects stays as the fallback so an unexpected bundling change degrades to
// the old behaviour instead of a 500.
function loadFromDisk() {
  const file = path.join(__dirname, "..", "projects.json");
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  return { projects: data.projects || [], last_updated: data.last_updated || null };
}

module.exports = async (req, res) => {
  try {
    let loaded;
    try {
      loaded = loadFromDisk();
    } catch (diskErr) {
      console.warn("sample: projects.json not readable from disk, falling back to HTTP:", diskErr.message);
      const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
      const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
      loaded = await fetchProjects(`${proto}://${host}`);
    }
    const { projects, last_updated } = loaded;

    const top = projects
      .slice()
      .sort((a, b) => (b.capacity_mw || 0) - (a.capacity_mw || 0))
      .slice(0, SAMPLE_SIZE);

    const stamp = (last_updated || new Date().toISOString()).slice(0, 10);
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="usenergymap-sample-${stamp}.csv"`);
    res.setHeader("Cache-Control", "public, max-age=300, s-maxage=3600");
    return res.status(200).send(toCSV(top));
  } catch (err) {
    console.error("sample error:", err);
    return res.status(500).json({ error: "Could not build the sample right now." });
  }
};
