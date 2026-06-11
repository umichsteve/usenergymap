// GET /api/download?session_id=cs_...&format=csv|geojson
// Verifies the Stripe Checkout session was actually paid, then streams the
// enriched dataset. The file is generated server-side and never exposed as a
// public URL, so it is genuinely gated behind payment.
//
// Env required: STRIPE_SECRET_KEY

const Stripe = require("stripe");
const { fetchProjects, toCSV, toGeoJSON, buildWorkbookBuffer } = require("../lib/dataset");

module.exports = async (req, res) => {
  const sessionId = req.query.session_id;
  const format = (req.query.format || "csv").toLowerCase();

  if (!process.env.STRIPE_SECRET_KEY) {
    return res.status(500).json({ error: "Stripe is not configured yet." });
  }
  if (!sessionId) {
    return res.status(400).json({ error: "Missing session_id." });
  }

  try {
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
    const session = await stripe.checkout.sessions.retrieve(sessionId);

    if (!session || session.payment_status !== "paid") {
      return res.status(402).json({ error: "Payment not found or not completed." });
    }

    const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
    const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
    const origin = `${proto}://${host}`;

    const { projects, last_updated } = await fetchProjects(origin);

    if (format === "xlsx") {
      const buf = await buildWorkbookBuffer(projects, last_updated);
      res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
      res.setHeader("Content-Disposition", 'attachment; filename="usenergymap-bd-hunting-list.xlsx"');
      return res.status(200).send(Buffer.from(buf));
    }

    if (format === "geojson") {
      res.setHeader("Content-Type", "application/geo+json; charset=utf-8");
      res.setHeader("Content-Disposition", 'attachment; filename="usenergymap-dataset.geojson"');
      return res.status(200).send(toGeoJSON(projects, last_updated));
    }

    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", 'attachment; filename="usenergymap-dataset.csv"');
    return res.status(200).send(toCSV(projects));
  } catch (err) {
    console.error("download error:", err);
    // Stripe throws if the session id is malformed/unknown — treat as unauthorized.
    return res.status(402).json({ error: "Could not verify purchase." });
  }
};
