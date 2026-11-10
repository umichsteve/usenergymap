// POST /api/create-checkout   body: { plan: "snapshot" | "current" | "annual", email?: string }
// Creates a Stripe Checkout Session and returns { url }. The client redirects there.
//
//   snapshot  → one-time payment, current dataset build, single-user license
//   current   → monthly subscription, refreshed dataset + change log, single-user license
//   annual    → the SAME Always Current product billed yearly. Its session metadata says
//               plan "current" + billing "annual", so lib/entitlement.js, the change-log
//               gate and the access email in api/stripe/webhook.js treat it exactly like
//               a monthly subscriber with no changes to any of them.
//
// Region / Full / Embedded tiers are quoted by hand (contact link on /data), so they
// have no checkout path here on purpose.
//
// Env (Vercel → Project → Settings → Environment Variables):
//   STRIPE_SECRET_KEY        (sk_test_... while testing, sk_live_... in production)
//   STRIPE_PRICE_SNAPSHOT    optional price_... (one-time)      — see lib/pricing.js
//   STRIPE_PRICE_ANNUAL      optional price_... (recurring/yr)  — see lib/pricing.js
//   PRICE_SNAPSHOT_CENTS     optional, used when no Price id is set (default 19900)
//   PRICE_ANNUAL_CENTS       optional, used when no Price id is set (default 49000)

const Stripe = require("stripe");
const { resolvePlan, inlineCents, MONTHLY_CENTS } = require("../lib/pricing");

const PLANS = {
  snapshot: {
    mode: "payment",
    configurable: true, // amount / Price id from lib/pricing.js
    name: "US Energy Map — Dataset Snapshot",
    description: "Current build of every tracked U.S. data center & battery storage project (Excel BD list + CSV + GeoJSON). Single-user license, no updates.",
  },
  current: {
    mode: "subscription",
    unit_amount: MONTHLY_CENTS, // $49.00 / month
    recurring: { interval: "month" },
    name: "US Energy Map — Always Current",
    description: "Monthly-refreshed dataset (Excel + CSV + GeoJSON) plus the row-level change log. Single-user license. Cancel anytime.",
  },
  annual: {
    mode: "subscription",
    configurable: true,
    recurring: { interval: "year" },
    metaPlan: "current", // entitlement + webhook copy key off "current"; see header
    billing: "annual",
    name: "US Energy Map — Always Current (Annual)",
    description: "Monthly-refreshed dataset (Excel + CSV + GeoJSON) plus the row-level change log, billed yearly. Single-user license.",
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

    // A configured Stripe Price wins. It is re-validated here (active, USD, right type
    // for the plan) so a monthly Price pasted into the annual slot cannot be sold.
    let lineItem;
    if (plan.configurable) {
      const resolved = await resolvePlan(stripe, planKey);
      if (resolved.priceId) {
        if (resolved.source !== "stripe") {
          return res.status(500).json({ error: "Checkout is temporarily unavailable." });
        }
        lineItem = { quantity: 1, price: resolved.priceId };
      }
    }
    if (!lineItem) {
      const unitAmount = plan.configurable ? inlineCents(planKey) : plan.unit_amount;
      lineItem = {
        quantity: 1,
        price_data: {
          currency: "usd",
          unit_amount: unitAmount,
          product_data: { name: plan.name, description: plan.description },
          ...(plan.recurring ? { recurring: plan.recurring } : {}),
        },
      };
    }
    const metadata = { plan: plan.metaPlan || planKey, app: "usenergymap", ...(plan.billing ? { billing: plan.billing } : {}) };

    const params = {
      mode: plan.mode,
      payment_method_types: ["card"],
      ...(email ? { customer_email: email } : {}),
      line_items: [lineItem],
      // Both tiers land on the same success page; it reads the session and adapts.
      success_url: `${origin}/data/success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${origin}/data`,
      // `app` lets the webhook recognise its own sessions; `plan` is still what
      // lib/entitlement.js reads ("current" for annual too). `billing` is additive.
      metadata,
      allow_promotion_codes: true,
    };

    if (plan.mode === "payment") {
      // Shows on the card statement; otherwise it bills as the account default.
      params.payment_intent_data = { statement_descriptor_suffix: "USENERGYMAP" };
      params.customer_creation = "always";
    } else {
      params.subscription_data = { metadata };
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
