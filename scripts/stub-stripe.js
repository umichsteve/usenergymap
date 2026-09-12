// Offline stand-in for the `stripe` package, used only by scripts/test-entitlement.js
// (which resolves require("stripe") here). It never opens a socket, so the tests
// cannot touch the live account. Fixtures come from global.__STRIPE_STUB__.

function stripeError(type, message, extra) {
  const err = new Error(message);
  err.type = type;
  Object.assign(err, extra || {});
  return err;
}

module.exports = function Stripe() {
  const state = () => global.__STRIPE_STUB__;
  const guard = () => {
    if (state().down) throw stripeError("StripeConnectionError", "An error occurred with our connection to Stripe.");
  };
  return {
    checkout: {
      sessions: {
        retrieve: async (id) => {
          guard();
          const s = state().SESSIONS[id];
          if (!s) {
            throw stripeError("StripeInvalidRequestError", `No such checkout.session: '${id}'`, {
              code: "resource_missing", statusCode: 404,
            });
          }
          return s;
        },
      },
    },
    subscriptions: {
      retrieve: async (id) => {
        guard();
        const sub = state().SUBSCRIPTIONS[id];
        if (!sub) {
          throw stripeError("StripeInvalidRequestError", `No such subscription: '${id}'`, {
            code: "resource_missing", statusCode: 404,
          });
        }
        return sub;
      },
    },
    billingPortal: {
      sessions: {
        create: async ({ customer }) => {
          guard();
          state().portalCalls.push(customer);
          return { url: "https://billing.stripe.com/session/stub_" + customer };
        },
      },
    },
  };
};
