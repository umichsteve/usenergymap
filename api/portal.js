// POST /api/portal   body: { session_id }
// Opens Stripe's hosted billing portal for a subscriber (update card, cancel, invoices).
// Portal must be enabled once in the Stripe Dashboard → Settings → Billing → Customer portal.
//
// Requires a session that verifySession ACCEPTED and that carries a customer. On a
// shared Stripe account "has a customer" is not an entitlement — see the guard below.

const Stripe = require("stripe");
const { verifySession, DENIED } = require("../lib/entitlement");

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") { res.setHeader("Allow", "POST"); return res.status(405).json({ error: "Method not allowed" }); }
  if (!process.env.STRIPE_SECRET_KEY) return res.status(500).json({ error: "Stripe is not configured yet." });
  try {
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
    const sessionId = req.body && req.body.session_id;
    const ent = await verifySession(stripe, sessionId);
    // ent.ok is the whole point: a Stripe customer is attached by several sibling
    // properties on this account (one-time checkouts with customer_creation "always",
    // and every subscription), so "a customer exists" was never evidence of anything.
    // Checking only ent.customer opened a live billing portal for sessions
    // verifySession had explicitly REJECTED — unpaid, cancelled, or another
    // property's entirely. Both conditions, or no portal.
    if (!ent.ok || !ent.customer) {
      return res.status(ent.status || DENIED.status).json({ error: ent.error || DENIED.error });
    }

    const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
    const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
    const portal = await stripe.billingPortal.sessions.create({
      customer: ent.customer,
      return_url: `${proto}://${host}/data/success?session_id=${encodeURIComponent(sessionId)}`,
    });
    return res.status(200).json({ url: portal.url });
  } catch (err) {
    // Stripe unreachable, or an id it choked on. Same answer as every other rejection:
    // the caller learns whether they are entitled, and nothing else.
    console.error("portal error:", err);
    return res.status(DENIED.status).json({ error: DENIED.error });
  }
};
