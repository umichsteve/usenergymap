// POST /api/portal   body: { session_id }
// Opens Stripe's hosted billing portal for a subscriber (update card, cancel, invoices).
// Portal must be enabled once in the Stripe Dashboard → Settings → Billing → Customer portal.

const Stripe = require("stripe");
const { verifySession } = require("../lib/entitlement");

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") { res.setHeader("Allow", "POST"); return res.status(405).json({ error: "Method not allowed" }); }
  if (!process.env.STRIPE_SECRET_KEY) return res.status(500).json({ error: "Stripe is not configured yet." });
  try {
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
    const sessionId = req.body && req.body.session_id;
    const ent = await verifySession(stripe, sessionId);
    if (!ent.customer) return res.status(402).json({ error: ent.error || "No customer on this purchase." });

    const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
    const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
    const portal = await stripe.billingPortal.sessions.create({
      customer: ent.customer,
      return_url: `${proto}://${host}/data/success?session_id=${encodeURIComponent(sessionId)}`,
    });
    return res.status(200).json({ url: portal.url });
  } catch (err) {
    console.error("portal error:", err);
    return res.status(500).json({ error: "Could not open billing portal." });
  }
};
