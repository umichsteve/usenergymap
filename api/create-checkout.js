// POST /api/create-checkout   body: { plan: "snapshot" | "current", email?: string }
// Creates a Stripe Checkout Session and returns { url }. The client redirects there.
//
//   snapshot  → one-time payment, current dataset build, single-user license
//   current   → monthly subscription, refreshed dataset + change log, single-user license
//
// Region / Full / Embedded tiers are quoted by hand (contact link on /data), so they
// have no checkout path here on purpose.
//
// Env required (Vercel → Project → Settings → Environment Variables):
//   STRIPE_SECRET_KEY   (sk_test_... while testing, sk_live_... in production)

const Stripe = require("stripe");

const PLANS = {
  snapshot: {
    mode: "payment",
    unit_amount: 19900, // $199.00 one-time
    name: "US Energy Map — Dataset Snapshot",
    description: "Current build of every tracked U.S. data center & battery storage project (Excel BD list + CSV + GeoJSON). Single-user license, no updates.",
  },
  current: {
    mode: "subscription",
    unit_amount: 4900, // $49.00 / month
    recurring: { interval: "month" },
    name: "US Energy Map — Always Current",
    description: "Monthly-refreshed dataset (Excel + CSV + GeoJSON) plus the row-level change log. Single-user license. Cancel anytime.",
  },
};

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "Method not allowed" });
  }
  if (!process.env.STRIPE_SECRET_KEY) {
    return res.status(500).json({ error: "Stripe is not configured yet." });
  }

  const body = req.body || {};
  const planKey = typeof body.plan === "string" && PLANS[body.plan] ? body.plan : "snapshot";
  const plan = PLANS[planKey];
  const email = typeof body.email === "string" ? body.email.trim() : "";

  try {
    const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
    const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
    const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
    const origin = `${proto}://${host}`;

    const priceData = {
      currency: "usd",
      unit_amount: plan.unit_amount,
      product_data: { name: plan.name, description: plan.description },
      ...(plan.recurring ? { recurring: plan.recurring } : {}),
    };

    const params = {
      mode: plan.mode,
      payment_method_types: ["card"],
      ...(email ? { customer_email: email } : {}),
      line_items: [{ quantity: 1, price_data: priceData }],
      // Both tiers land on the same success page; it reads the session and adapts.
      success_url: `${origin}/data/success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${origin}/data`,
      metadata: { plan: planKey },
      allow_promotion_codes: true,
    };

    if (plan.mode === "payment") {
      // Shows on the card statement; otherwise it bills as the account default.
      params.payment_intent_data = { statement_descriptor_suffix: "ENERGYMAP" };
      params.customer_creation = "always";
    } else {
      params.subscription_data = { metadata: { plan: planKey } };
    }

    const session = await stripe.checkout.sessions.create(params);

    // A payment response must never be reused. launchdmanager.app/api/checkout was
    // silently cached for 80 days in 2026 after a refactor left a GET path open.
    res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
    res.setHeader("CDN-Cache-Control", "no-store");
    res.setHeader("Vercel-CDN-Cache-Control", "no-store");
    return res.status(200).json({ url: session.url });
  } catch (err) {
    console.error("create-checkout error:", err);
    return res.status(500).json({ error: "Could not start checkout." });
  }
};
