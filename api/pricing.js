// GET /api/pricing
//
// The self-serve prices /data displays, resolved by lib/pricing.js from the same env
// vars checkout charges from. Public: these are list prices on a public page.
//
// `source` per plan says where the number came from — "stripe" (a configured Price id),
// "env" (PRICE_*_CENTS), or "default" — so a deploy can be checked without the
// dashboard. amount_cents is null if a configured Price id could not be read; the page
// then keeps its static fallback text rather than showing a guess.

const Stripe = require("stripe");
const { resolveAll } = require("../lib/pricing");

module.exports = async (req, res) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "Method not allowed" });
  }
  const stripe = process.env.STRIPE_SECRET_KEY ? new Stripe(process.env.STRIPE_SECRET_KEY) : null;
  const prices = await resolveAll(stripe);
  const complete = prices.snapshot.amount_cents != null && prices.annual.amount_cents != null;
  // Short edge cache: a price change is a redeploy anyway, and a Stripe read failure
  // should not be pinned for long.
  res.setHeader("Cache-Control", complete ? "public, max-age=60, s-maxage=300" : "no-store");
  return res.status(200).json(prices);
};
