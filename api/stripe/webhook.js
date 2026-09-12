// POST /api/stripe/webhook
//
// Stripe tells us a Checkout Session was paid; we email the buyer their permanent
// access link. That link is the success-page URL, which is the bearer token for
// /api/download — see lib/entitlement.js. Without this handler a buyer who loses
// the redirect (tab closed, phone died, 3-D Secure bounce) has no way back in.
//
// Design notes, in the order they bite:
//
//   * Signature verification is mandatory and needs the UNPARSED body. Vercel's
//     Node runtime leaves the request stream intact for us here, so we buffer it
//     ourselves — see readRawBody(). Never hand constructEvent() a re-serialised
//     object; key order and whitespace change and every signature fails.
//
//   * The account carries other products' webhooks, so a delivery may not be ours.
//     Ownership is decided BEFORE anything sends, calls out, or logs an address.
//     The test itself lives in lib/ownership.js, shared with lib/entitlement.js so
//     the read paths (/api/session, /api/download, /api/portal) reject a sibling
//     property's session by exactly the same rule this handler ignores it by.
//
//   * The access link is CONSTRUCTED, never read from the payload. Stripe stores
//     success_url with {CHECKOUT_SESSION_ID} unsubstituted — substitution happens
//     at redirect time only — so the API object and the webhook payload both carry
//     the literal placeholder. Emailing session.success_url sends a dead link.
//     success_url is used for the ownership host check and nothing else.
//
//   * Entitlement is decided by verifySession(), the same function session.js,
//     download.js and portal.js use. It re-reads from Stripe rather than trusting
//     the payload, and keeps one definition of "entitled" in the codebase.
//
//   * Subscription lifecycle events are deliberately NOT handled. entitlement.js
//     re-checks subscription status live on every request, so a handler here would
//     be a no-op that looks like a second source of truth.
//
// Env required (Vercel → Project → Settings → Environment Variables):
//   STRIPE_SECRET_KEY       already set — used to re-read the session
//   STRIPE_WEBHOOK_SECRET   whsec_... for THIS endpoint (per-endpoint, not per-account)
//   RESEND_API_KEY          re_...
//   DELIVERY_BCC            address that gets a copy of every send

const Stripe = require("stripe");
const { verifySession } = require("../../lib/entitlement");
const { isOurSession } = require("../../lib/ownership");

// The buyer's link always points at the canonical domain, never at whatever host
// happened to serve the checkout (a preview deployment must not mint preview links).
const ACCESS_LINK_BASE = "https://usenergymap.com/data/success";
const FROM = "US Energy Map <data@usenergymap.com>";
const RESEND_ENDPOINT = "https://api.resend.com/emails";

// async_payment_succeeded covers the delayed methods (ACH, some wallets) where
// checkout.session.completed fires before the money lands.
const HANDLED_EVENTS = new Set([
  "checkout.session.completed",
  "checkout.session.async_payment_succeeded",
]);

const BODY_READ_TIMEOUT_MS = 10000;

function readRawBody(req) {
  // If the platform ever starts pre-reading for us, take what it left behind
  // verbatim; anything already parsed into an object is unusable for signing.
  if (Buffer.isBuffer(req.body)) return Promise.resolve(req.body);
  if (typeof req.body === "string") return Promise.resolve(Buffer.from(req.body, "utf8"));

  return new Promise((resolve, reject) => {
    const chunks = [];
    let settled = false;
    const settle = (fn, arg) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      fn(arg);
    };
    const timer = setTimeout(
      () => settle(reject, new Error("Timed out reading the request body.")),
      BODY_READ_TIMEOUT_MS
    );
    req.on("data", (c) => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)));
    req.on("end", () => settle(resolve, Buffer.concat(chunks)));
    req.on("error", (err) => settle(reject, err));
  });
}

// A transport-level fault: Stripe is down or unreachable, so the event is worth
// redelivering. A 4xx from Stripe (no such session, bad key) is understood and final.
function isRetryableStripeFailure(err) {
  if (!err) return false;
  if (err.type === "StripeConnectionError" || err.type === "StripeAPIError") return true;
  return typeof err.statusCode === "number" && err.statusCode >= 500;
}

function planCopy(plan) {
  return plan === "current"
    ? {
        product: "Always Current",
        blurb:
          "Your subscription includes the monthly-refreshed dataset (Excel, CSV and GeoJSON) plus the row-level change log.",
      }
    : {
        product: "Dataset Snapshot",
        blurb:
          "Your purchase includes the current build of the dataset as Excel, CSV and GeoJSON.",
      };
}

function buildEmail(accessLink, plan) {
  const { product, blurb } = planCopy(plan);
  const text = [
    `Thanks for your purchase — US Energy Map ${product}.`,
    "",
    blurb,
    "",
    "Your access link (bookmark it — it is permanent and it is how you get back in):",
    accessLink,
    "",
    "Open the link and the download buttons are on the page. If you ever lose it,",
    "reply to this email and we will resend it.",
    "",
    "— US Energy Map",
    "https://usenergymap.com",
  ].join("\n");

  const html = [
    '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#111">',
    `<p>Thanks for your purchase — <strong>US Energy Map ${escapeHtml(product)}</strong>.</p>`,
    `<p>${escapeHtml(blurb)}</p>`,
    "<p>Your access link — bookmark it, it is permanent and it is how you get back in:</p>",
    `<p><a href="${escapeHtml(accessLink)}" style="color:#0b6">${escapeHtml(accessLink)}</a></p>`,
    "<p>Open the link and the download buttons are on the page. If you ever lose it, reply to this email and we will resend it.</p>",
    '<p style="color:#666;font-size:13px">— US Energy Map · <a href="https://usenergymap.com" style="color:#666">usenergymap.com</a></p>',
    "</div>",
  ].join("");

  return { subject: `Your US Energy Map data — ${product}`, text, html };
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// Resolves to { ok } or { retry: true } — retry means "a redelivery could still
// land this", which is the only thing a non-2xx to Stripe is allowed to mean.
async function sendAccessEmail({ to, sessionId, accessLink, plan }) {
  const { subject, text, html } = buildEmail(accessLink, plan);
  const payload = { from: FROM, to: [to], subject, text, html };
  const bcc = process.env.DELIVERY_BCC;
  if (bcc) payload.bcc = [bcc];

  const res = await fetch(RESEND_ENDPOINT, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
      // Keyed on the session, so the completed/async_payment_succeeded pair and any
      // Stripe redelivery collapse into one send at the provider.
      "Idempotency-Key": `usenergymap-access-${sessionId}`,
    },
    body: JSON.stringify(payload),
  });

  if (res.ok) return { ok: true };

  const detail = await res.text().catch(() => "");
  // 429 is a 4xx that a redelivery genuinely fixes; every other 4xx is a
  // configuration fault that retrying cannot change.
  const retry = res.status >= 500 || res.status === 429;
  console.error(
    `webhook: Resend ${res.status} for session ${sessionId} (retry=${retry}): ${detail.slice(0, 300)}`
  );
  return { retry };
}

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");

  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "Method not allowed" });
  }
  if (!process.env.STRIPE_SECRET_KEY || !process.env.STRIPE_WEBHOOK_SECRET) {
    console.error("webhook: STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET not configured.");
    return res.status(500).json({ error: "Webhook is not configured." });
  }

  const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);

  // ---- parse -------------------------------------------------------------
  let raw;
  try {
    raw = await readRawBody(req);
  } catch (err) {
    console.error("webhook: could not read the request body:", err.message);
    return res.status(400).json({ error: "Could not read the request body." });
  }

  // ---- verify signature --------------------------------------------------
  let event;
  try {
    event = stripe.webhooks.constructEvent(
      raw,
      req.headers["stripe-signature"],
      process.env.STRIPE_WEBHOOK_SECRET
    );
  } catch (err) {
    console.error("webhook: signature verification failed:", err.message);
    return res.status(400).json({ error: "Signature verification failed." });
  }

  if (!HANDLED_EVENTS.has(event.type)) {
    return res.status(200).json({ received: true, ignored: "event type" });
  }

  // ---- ownership ---------------------------------------------------------
  // Nothing above this line may send, call Resend, or log an address.
  const session = event.data && event.data.object;
  if (!isOurSession(session)) {
    return res.status(200).json({ received: true, ignored: "not our property" });
  }

  // ---- entitlement -------------------------------------------------------
  let ent;
  try {
    ent = await verifySession(stripe, session.id);
  } catch (err) {
    if (isRetryableStripeFailure(err)) {
      console.error(`webhook: Stripe unavailable verifying ${session.id}:`, err.message);
      return res.status(500).json({ error: "Could not reach Stripe." });
    }
    console.error(`webhook: could not verify ${session.id}:`, err.message);
    return res.status(200).json({ received: true, ignored: "unverifiable session" });
  }

  if (!ent.ok) {
    // Not entitled yet — an async payment still settling, or a subscription that
    // never activated. Understood, so 200; Stripe will send the follow-up event.
    return res.status(200).json({ received: true, ignored: "not entitled", reason: ent.error });
  }

  // verifySession resolves customer_details.email → customer_email → null.
  if (!ent.email) {
    console.error(`webhook: no email address on session ${session.id}; nothing sent.`);
    return res.status(200).json({ received: true, ignored: "no email address" });
  }

  if (!process.env.RESEND_API_KEY) {
    // A redelivery after the key is set will land, so ask Stripe to keep trying.
    console.error(`webhook: RESEND_API_KEY not configured; ${session.id} not delivered.`);
    return res.status(500).json({ error: "Email is not configured." });
  }

  // ---- deliver -----------------------------------------------------------
  const accessLink = `${ACCESS_LINK_BASE}?session_id=${encodeURIComponent(session.id)}`;
  try {
    const result = await sendAccessEmail({
      to: ent.email,
      sessionId: session.id,
      accessLink,
      plan: ent.plan,
    });
    if (result.retry) return res.status(500).json({ error: "Email provider failed." });
    if (!result.ok) return res.status(200).json({ received: true, ignored: "email rejected" });
  } catch (err) {
    console.error(`webhook: Resend request failed for ${session.id}:`, err.message);
    return res.status(500).json({ error: "Email provider unreachable." });
  }

  console.log(`webhook: access link sent for ${session.id} (${ent.plan}).`);
  return res.status(200).json({ received: true, sent: true });
};
