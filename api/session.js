// GET /api/session?session_id=cs_...
// Read-only status for the success page: which plan, whether it's active, when it renews.
// Never returns anything a stranger couldn't already infer from holding the link.

const Stripe = require("stripe");
const { verifySession } = require("../lib/entitlement");

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (!process.env.STRIPE_SECRET_KEY) return res.status(500).json({ error: "Stripe is not configured yet." });
  try {
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
    const ent = await verifySession(stripe, req.query.session_id);
    const { customer, ...pub } = ent; // don't leak the customer id
    return res.status(ent.ok ? 200 : (ent.status || 402)).json(pub);
  } catch (err) {
    console.error("session error:", err);
    return res.status(402).json({ ok: false, error: "Could not verify purchase." });
  }
};
