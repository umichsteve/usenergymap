// GET /api/download?session_id=cs_...&format=csv|geojson|xlsx|changelog
// Verifies the purchase with Stripe on every call, then streams the file. Files are
// generated server-side and never exposed at a public URL.
//
//   csv / geojson / xlsx  → any paid session (snapshot or active subscription)
//   changelog             → active "Always Current" subscribers only
//
// Env required: STRIPE_SECRET_KEY

const Stripe = require("stripe");
const { fetchProjects, toCSV, toGeoJSON, buildWorkbookBuffer } = require("../lib/dataset");
const { verifySession } = require("../lib/entitlement");
const { fetchChangelog, changelogToCSV } = require("../lib/changelog");

module.exports = async (req, res) => {
  const sessionId = req.query.session_id;
  const format = (req.query.format || "csv").toLowerCase();

  if (!process.env.STRIPE_SECRET_KEY) {
    return res.status(500).json({ error: "Stripe is not configured yet." });
  }

  res.setHeader("Cache-Control", "no-store");

  try {
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
    const ent = await verifySession(stripe, sessionId);
    if (!ent.ok) return res.status(ent.status || 402).json({ error: ent.error });

    const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
    const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
    const origin = `${proto}://${host}`;

    if (format === "changelog") {
      if (ent.mode !== "subscription") {
        return res.status(403).json({ error: "The change log is part of the Always Current subscription." });
      }
      const log = await fetchChangelog(origin);
      res.setHeader("Content-Type", "text/csv; charset=utf-8");
      res.setHeader("Content-Disposition", `attachment; filename="usenergymap-changelog-${log.to || "latest"}.csv"`);
      return res.status(200).send(changelogToCSV(log));
    }

    const { projects, last_updated } = await fetchProjects(origin);
    const stamp = last_updated || "latest";

    if (format === "xlsx") {
      const buf = await buildWorkbookBuffer(projects, last_updated);
      res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
      res.setHeader("Content-Disposition", `attachment; filename="usenergymap-bd-hunting-list-${stamp}.xlsx"`);
      return res.status(200).send(Buffer.from(buf));
    }

    if (format === "geojson") {
      res.setHeader("Content-Type", "application/geo+json; charset=utf-8");
      res.setHeader("Content-Disposition", `attachment; filename="usenergymap-dataset-${stamp}.geojson"`);
      return res.status(200).send(toGeoJSON(projects, last_updated));
    }

    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="usenergymap-dataset-${stamp}.csv"`);
    return res.status(200).send(toCSV(projects));
  } catch (err) {
    console.error("download error:", err);
    // Stripe throws on malformed/unknown ids — treat as unauthorized.
    return res.status(402).json({ error: "Could not verify purchase." });
  }
};
