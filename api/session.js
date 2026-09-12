// GET /api/session?session_id=cs_...
// Read-only status for the success page: which plan, whether it's active, when it renews,
// and what Stripe charged (the success page reports that as the Google Ads conversion value).
//
// It returns the buyer's email address and what they paid, so "holding the link" has to
// mean "bought THIS product". On a Stripe account shared by seven properties that is not
// automatic: until Sept 2026 any sibling property's session id read out that property's
// buyer here. verifySession now settles ownership before anything is read off the
// session — this route is safe only for as long as that remains true.
//
// Every rejection — not ours, not paid, no such session, Stripe unreachable — returns
// one byte-identical body. Distinguishable failures would let an unauthenticated caller
// sort live session ids from dead ones, and nothing here is rate limited.

const Stripe = require("stripe");
const { verifySession, DENIED } = require("../lib/entitlement");

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (!process.env.STRIPE_SECRET_KEY) return res.status(500).json({ error: "Stripe is not configured yet." });
  try {
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
    const ent = await verifySession(stripe, req.query.session_id);
    if (!ent.ok) return res.status(DENIED.status).json({ ok: false, error: DENIED.error });
    const { customer, status, ...pub } = ent; // don't leak the customer id
    return res.status(200).json(pub);
  } catch (err) {
    console.error("session error:", err);
    return res.status(DENIED.status).json({ ok: false, error: DENIED.error });
  }
};
