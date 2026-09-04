// Shared purchase verification for the paid endpoints.
// One-time buyers are entitled forever to the build they bought (served live, so in
// practice they get whatever is current when they click — fine, the data is public).
// Subscribers are entitled while the Stripe subscription is active.
//
// No database: the Checkout Session id in the buyer's success-page URL is the bearer
// token, and Stripe is the source of truth on every request.

const ACTIVE_SUB = new Set(["active", "trialing", "past_due"]); // past_due: grace period while Stripe retries

async function verifySession(stripe, sessionId) {
  if (!sessionId || typeof sessionId !== "string" || !sessionId.startsWith("cs_")) {
    return { ok: false, status: 400, error: "Missing or malformed session_id." };
  }

  const session = await stripe.checkout.sessions.retrieve(sessionId);
  const plan = (session.metadata && session.metadata.plan) || (session.mode === "subscription" ? "current" : "snapshot");
  const email = (session.customer_details && session.customer_details.email) || session.customer_email || null;
  const customer = typeof session.customer === "string" ? session.customer : (session.customer && session.customer.id) || null;

  if (session.mode === "subscription") {
    if (!session.subscription) {
      return { ok: false, status: 402, error: "Subscription not found on this session." };
    }
    const subId = typeof session.subscription === "string" ? session.subscription : session.subscription.id;
    const sub = await stripe.subscriptions.retrieve(subId);
    if (!ACTIVE_SUB.has(sub.status)) {
      return { ok: false, status: 402, error: `Subscription is ${sub.status}.`, plan, email, customer, subscription_status: sub.status };
    }
    return {
      ok: true, plan, mode: "subscription", email, customer,
      subscription_status: sub.status,
      current_period_end: sub.current_period_end ? new Date(sub.current_period_end * 1000).toISOString() : null,
      cancel_at_period_end: !!sub.cancel_at_period_end,
    };
  }

  if (session.payment_status !== "paid") {
    return { ok: false, status: 402, error: "Payment not found or not completed.", plan, email, customer };
  }
  return { ok: true, plan, mode: "payment", email, customer };
}

module.exports = { verifySession };
