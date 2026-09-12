// Which Checkout Sessions belong to THIS property.
//
// The Stripe account is shared by seven properties, so "Stripe says this session is
// paid" is not the same claim as "this person bought US Energy Map". A session id is
// the bearer token for everything behind /data, so every path that trusts one —
// the read paths in lib/entitlement.js and the write path in api/stripe/webhook.js —
// decides ownership here, through one implementation that cannot drift.
//
// Either signal is sufficient, and BOTH are load-bearing:
//
//   * metadata.app — set by api/create-checkout.js from Sept 2026 forward.
//   * success_url hostname — recognises sessions created before that tag existed.
//     The oldest paying customer on this property has metadata {}, so this arm is
//     not a nicety and must never be made conditional on metadata being present:
//     dropping it revokes him.
//
// Stripe stores success_url with {CHECKOUT_SESSION_ID} unsubstituted — substitution
// happens at redirect time only — so the hostname is the only part worth reading and
// the query string cannot be assumed populated. new URL() throws on an absent or
// malformed success_url; that is not a signal either way, and no signal fails closed.

const OWNER_HOST = "usenergymap.com";

function isOurSession(session) {
  if (!session) return false;
  if (session.metadata && session.metadata.app === "usenergymap") return true;
  try {
    return new URL(session.success_url).hostname === OWNER_HOST;
  } catch (_) {
    return false; // absent or malformed success_url — not a signal either way
  }
}

module.exports = { isOurSession, OWNER_HOST };
