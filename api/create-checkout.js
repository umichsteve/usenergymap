// POST /api/create-checkout
// Creates a Stripe Checkout Session for the one-time dataset purchase and
// returns { url }. The client redirects the browser to that URL.
//
// Env required (set in Vercel → Project → Settings → Environment Variables):
//   STRIPE_SECRET_KEY   (sk_test_... while testing, sk_live_... in production)

const Stripe = require("stripe");

const PRICE_CENTS = 14999; // $149.99 one-time

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "Method not allowed" });
  }
  if (!process.env.STRIPE_SECRET_KEY) {
    return res.status(500).json({ error: "Stripe is not configured yet." });
  }

  try {
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
    const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
    const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
    const origin = `${proto}://${host}`;

    const email = typeof (req.body && req.body.email) === "string" ? req.body.email.trim() : "";

    const session = await stripe.checkout.sessions.create({
      mode: "payment",
      payment_method_types: ["card"],
      // Buyers see this on their card statement. Without it the charge bills as the
      // account default, which is another product's domain entirely.
      payment_intent_data: { statement_descriptor_suffix: "ENERGYMAP" },
      // Known before the redirect, so an abandoned cart is still a lead.
      ...(email ? { customer_email: email } : {}),
      line_items: [{
        quantity: 1,
        price_data: {
          currency: "usd",
          unit_amount: PRICE_CENTS,
          product_data: {
            name: "US Energy Map — Full Dataset",
            description: "All tracked U.S. data center & battery storage projects (CSV + GeoJSON, enriched).",
          },
        },
      }],
      success_url: `${origin}/data/success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${origin}/data`,
      // Lets us email a receipt + (later) build a customer list for the subscription tier.
      customer_creation: "always",
    });

    // A payment response must never be reused. This endpoint is POST-only today
    // (so no CDN would cache it anyway), but launchdmanager.app/api/checkout was
    // silently cached for 80 days in 2026 after a refactor left a GET path open,
    // sending every buyer to one dead Stripe session. Set the header regardless.
    res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
    res.setHeader("CDN-Cache-Control", "no-store");
    res.setHeader("Vercel-CDN-Cache-Control", "no-store");
    return res.status(200).json({ url: session.url });
  } catch (err) {
    console.error("create-checkout error:", err);
    return res.status(500).json({ error: "Could not start checkout." });
  }
};
