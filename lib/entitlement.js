// Shared purchase verification for the paid endpoints.
// One-time buyers are entitled forever to the build they bought (served live, so in
// practice they get whatever is current when they click — fine, the data is public).
// Subscribers are entitled while the Stripe subscription is active.
//
// No database: the Checkout Session id in the buyer's success-page URL is the bearer
// token, and Stripe is the source of truth on every request.
//
// That holds ONLY because of the ownership gate below. This Stripe account carries
// seven properties, so "Stripe knows this session and it is paid" answers a question
// nobody asked: any session id minted by any sibling property is a valid, paid session
// on this account. Ownership (lib/ownership.js) is therefore decided first, and nothing
// — not payment_status, not mode, not the customer, and above all not the email — is
// read off a session that has not passed it. Delete that check and a $1.99 city-guide
// unlock buys the $199 dataset.
//
// Every rejection returns ONE shape. A caller must not be able to tell "not ours" from
// "not paid" from "no such session": that distinction is an oracle for which ids on the
// account are live, and there is no rate limiting in front of these routes.

const { isOurSession } = require("./ownership");

const ACTIVE_SUB = new Set(["active", "trialing", "past_due"]); // past_due: grace period while Stripe retries

// The single negative. No plan, no email, no customer, no subscription status: a
// rejected caller has not proved they are entitled to learn anything at all.
const DENIED = Object.freeze({ ok: false, status: 402, error: "Could not verify purchase." });
function denied() {
  return { ...DENIED };
}

// "No such session" is Stripe's final answer and means exactly what "not ours" means:
// nothing. Every other failure (connection, 5xx, bad key) is rethrown — api/stripe/
// webhook.js distinguishes a redeliverable fault from a verdict by catching the throw,
// and swallowing one here would turn an outage into a silent non-delivery.
function isNoSuchSession(err) {
  if (!err) return false;
  if (err.code === "resource_missing") return true;
  return err.type === "StripeInvalidRequestError" && err.statusCode === 404;
}

async function verifySession(stripe, sessionId) {
  if (!sessionId || typeof sessionId !== "string" || !sessionId.startsWith("cs_")) {
    return denied();
  }

  let session;
  try {
    session = await stripe.checkout.sessions.retrieve(sessionId);
  } catch (err) {
    if (isNoSuchSession(err)) return denied();
    throw err;
  }

  // ---- ownership ---------------------------------------------------------
  // Nothing below this line may be read off `session` until it passes.
  if (!isOurSession(session)) return denied();

  const plan = (session.metadata && session.metadata.plan) || (session.mode === "subscription" ? "current" : "snapshot");
  const email = (session.customer_details && session.customer_details.email) || session.customer_email || null;
  const customer = typeof session.customer === "string" ? session.customer : (session.customer && session.customer.id) || null;

  // What Stripe actually charged, in major units. The success page reports this as the
  // Google Ads conversion value, so the number in Ads is the number that was billed —
  // promotion codes included, and with no price list duplicated in the front end to
  // drift out of sync. For a subscription this is the first invoice, which is the
  // right figure for a conversion: it is what the click produced today.
  const amount = typeof session.amount_total === "number" ? session.amount_total / 100 : null;
  const currency = session.currency ? String(session.currency).toUpperCase() : null;

  if (session.mode === "subscription") {
    if (!session.subscription) return denied();
    const subId = typeof session.subscription === "string" ? session.subscription : session.subscription.id;
    const sub = await stripe.subscriptions.retrieve(subId);
    if (!ACTIVE_SUB.has(sub.status)) return denied();
    return {
      ok: true, plan, mode: "subscription", email, customer, amount, currency,
      subscription_status: sub.status,
      current_period_end: sub.current_period_end ? new Date(sub.current_period_end * 1000).toISOString() : null,
      cancel_at_period_end: !!sub.cancel_at_period_end,
    };
  }

  if (session.payment_status !== "paid") return denied();

  // mode is set here and on the subscription branch only — i.e. only on a session that
  // passed ownership. Callers gating on ent.mode (api/download.js, format=changelog)
  // are therefore gating on "OUR subscription", not merely "a subscription".
  return { ok: true, plan, mode: "payment", email, customer, amount, currency };
}

module.exports = { verifySession, DENIED };
