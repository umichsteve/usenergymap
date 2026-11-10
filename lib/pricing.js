// Self-serve prices, from env, in one place.
//
// Checkout (api/create-checkout.js), the price display on /data (via api/pricing.js) and
// the sample email copy all read from here, so what the page says is what Stripe charges.
//
// Two ways to set a price, per plan:
//
//   STRIPE_PRICE_SNAPSHOT / STRIPE_PRICE_ANNUAL   price_... ids from the Stripe Dashboard.
//       When set, checkout charges that Price, and the displayed amount is READ from that
//       Price — the env var cannot say one number while Stripe charges another.
//
//   PRICE_SNAPSHOT_CENTS / PRICE_ANNUAL_CENTS     integer cents, e.g. 19900.
//       Used when no Price id is set: checkout sends inline price_data at this amount
//       (the pre-Nov-2026 behaviour). Unset or invalid → the default below.
//
// Monthly ("current") is deliberately not configurable here; it stays $49/mo inline.

const DEFAULT_CENTS = { snapshot: 19900, annual: 49000 };
const MONTHLY_CENTS = 4900;

const ENV = {
  snapshot: { cents: "PRICE_SNAPSHOT_CENTS", priceId: "STRIPE_PRICE_SNAPSHOT" },
  annual: { cents: "PRICE_ANNUAL_CENTS", priceId: "STRIPE_PRICE_ANNUAL" },
};

// What each Stripe Price must look like for its plan. A mismatched id (a monthly price
// pasted into the annual slot) is refused rather than silently sold at the wrong cadence.
const EXPECT = {
  snapshot: (p) => p.type === "one_time",
  annual: (p) => p.type === "recurring" && p.recurring && p.recurring.interval === "year" && (p.recurring.interval_count || 1) === 1,
};

function envCents(plan) {
  const raw = (process.env[ENV[plan].cents] || "").trim();
  if (!/^\d+$/.test(raw)) {
    if (raw) console.warn(`pricing: ${ENV[plan].cents}=${JSON.stringify(raw)} is not integer cents; using default.`);
    return { cents: DEFAULT_CENTS[plan], source: "default" };
  }
  const n = parseInt(raw, 10);
  if (n < 50 || n > 10000000) {
    console.warn(`pricing: ${ENV[plan].cents}=${n} out of range; using default.`);
    return { cents: DEFAULT_CENTS[plan], source: "default" };
  }
  return { cents: n, source: "env" };
}

function priceIdFor(plan) {
  const id = (process.env[ENV[plan].priceId] || "").trim();
  return id.startsWith("price_") ? id : null;
}

// Synchronous: the amount checkout falls back to when no Price id is configured.
function inlineCents(plan) {
  return envCents(plan).cents;
}

// Resolve one plan to { cents, priceId, source }. With a Price id this reads Stripe;
// on any failure it returns cents:null so callers never display a guessed number.
async function resolvePlan(stripe, plan) {
  const priceId = priceIdFor(plan);
  if (!priceId) return { ...envCents(plan), priceId: null };
  if (!stripe) return { cents: null, priceId, source: "stripe_unavailable" };
  try {
    const p = await stripe.prices.retrieve(priceId);
    if (!p || !p.active || p.currency !== "usd" || typeof p.unit_amount !== "number" || !EXPECT[plan](p)) {
      console.error(`pricing: ${ENV[plan].priceId}=${priceId} is inactive, not USD, or the wrong type for ${plan}.`);
      return { cents: null, priceId, source: "stripe_invalid" };
    }
    return { cents: p.unit_amount, priceId, source: "stripe" };
  } catch (err) {
    console.error(`pricing: could not read ${priceId}:`, err.message);
    return { cents: null, priceId, source: "stripe_error" };
  }
}

function money(cents) {
  if (typeof cents !== "number") return null;
  const whole = cents % 100 === 0;
  return "$" + (cents / 100).toLocaleString("en-US", {
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  });
}

async function resolveAll(stripe) {
  const [snapshot, annual] = await Promise.all([resolvePlan(stripe, "snapshot"), resolvePlan(stripe, "annual")]);
  const yearlyAtMonthly = MONTHLY_CENTS * 12;
  const savings = typeof annual.cents === "number" ? yearlyAtMonthly - annual.cents : null;
  return {
    snapshot: { amount_cents: snapshot.cents, display: money(snapshot.cents), source: snapshot.source },
    annual: {
      amount_cents: annual.cents,
      display: money(annual.cents),
      per_month_display: typeof annual.cents === "number" ? money(Math.round(annual.cents / 12 / 100) * 100) : null,
      savings_display: savings > 0 ? money(savings) : null,
      source: annual.source,
    },
    monthly: { amount_cents: MONTHLY_CENTS, display: money(MONTHLY_CENTS), yearly_display: money(yearlyAtMonthly) },
  };
}

module.exports = { resolveAll, resolvePlan, inlineCents, priceIdFor, money, DEFAULT_CENTS, MONTHLY_CENTS };
