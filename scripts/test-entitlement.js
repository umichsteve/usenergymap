#!/usr/bin/env node
// Ownership / entitlement tests. No framework, no new dependencies, no network, and
// no contact with any Stripe account: `stripe` and `exceljs` are resolved to stubs
// below, and the fixtures are hand-built session objects shaped like the real ones.
//
//   node scripts/test-entitlement.js
//
// The case that matters most is LEGACY_ONE_TIME: the shape of the only external
// one-time purchase this property has ever taken. That session predates metadata.app
// and carries metadata {}, so the success_url host arm is the ONLY thing that
// recognises it. If that test goes red, the change that turned it red has revoked a
// paying customer — fix the change, never the test.
//
// The fixtures below are synthetic. A live cs_live_... id is the bearer token for the
// $199 dataset and this repository is public, so real ids and real buyer addresses
// stay out of it; only the SHAPE (mode, payment_status, empty metadata, success_url
// host) is reproduced, because the shape is what the code reads.

const assert = require("assert");
const path = require("path");
const Module = require("module");

// ---- resolve `stripe` / `exceljs` to stubs so the real routes load ---------
const STUBS = { stripe: path.join(__dirname, "..", "scripts", "stub-stripe.js") };
const realResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request === "stripe") return STUBS.stripe;
  if (request === "exceljs") return path.join(__dirname, "stub-exceljs.js");
  return realResolve.call(this, request, ...rest);
};

const OUR_SUCCESS = "https://usenergymap.com/data/success?session_id={CHECKOUT_SESSION_ID}";

// ---- fixtures --------------------------------------------------------------
// Keyed by session id, exactly as stripe.checkout.sessions.retrieve would return them.
const SESSIONS = {
  // The legacy one-time buyer's shape: mode payment, paid, metadata {} (predates the
  // metadata.app tag), recognised by the success_url host arm alone.
  "cs_live_LEGACYONETIMEFIXTUREnotarealsessionid00000000000000000": {
    id: "cs_live_LEGACYONETIMEFIXTURE", mode: "payment", payment_status: "paid", metadata: {},
    customer: "cus_legacyOneTimeBuyer",
    customer_details: { email: "legacy-buyer@example.com" },
    amount_total: 14999, currency: "usd", success_url: OUR_SUCCESS,
  },
  // $1.99 GPS City Guide city unlock — sibling property, paid, customer attached.
  cs_test_gpscityguide: {
    id: "cs_test_gpscityguide", mode: "payment", payment_status: "paid",
    metadata: { product: "city.baltimore" }, customer: "cus_gpsBuyer",
    customer_details: { email: "someone-else@example.com" },
    amount_total: 199, currency: "usd",
    success_url: "https://gpscityguide.com/unlocked?session_id={CHECKOUT_SESSION_ID}",
  },
  // $4.99/mo Audible Report subscription — sibling property, active.
  cs_test_audible: {
    id: "cs_test_audible", mode: "subscription", payment_status: "paid",
    metadata: { app: "audible-report", plan: "pro" }, subscription: "sub_audible",
    customer: "cus_audibleBuyer", customer_details: { email: "reader@example.com" },
    amount_total: 499, currency: "usd",
    success_url: "https://theaudiblereport.com/welcome?session_id={CHECKOUT_SESSION_ID}",
  },
  // launchd — sibling property, customer_creation "always", so a customer is attached.
  cs_test_launchd: {
    id: "cs_test_launchd", mode: "payment", payment_status: "paid",
    metadata: { app: "launchd" }, customer: "cus_launchdBuyer",
    customer_details: { email: "founder@example.com" }, amount_total: 2900, currency: "usd",
    success_url: "https://launchdmanager.app/thanks?session_id={CHECKOUT_SESSION_ID}",
  },
  // Ours, abandoned at the card form.
  cs_test_ours_unpaid: {
    id: "cs_test_ours_unpaid", mode: "payment", payment_status: "unpaid",
    metadata: { app: "usenergymap", plan: "snapshot" }, customer: "cus_ourUnpaid",
    customer_details: { email: "buyer@example.com" }, amount_total: 19900, currency: "usd",
    success_url: OUR_SUCCESS,
  },
  // Ours, live Always Current subscription.
  cs_test_ours_sub: {
    id: "cs_test_ours_sub", mode: "subscription", payment_status: "paid",
    metadata: { app: "usenergymap", plan: "current" }, subscription: "sub_ours",
    customer: "cus_ourSub", customer_details: { email: "sub@example.com" },
    amount_total: 4900, currency: "usd", success_url: OUR_SUCCESS,
  },
  // Ours, subscription since cancelled.
  cs_test_ours_sub_dead: {
    id: "cs_test_ours_sub_dead", mode: "subscription", payment_status: "paid",
    metadata: { app: "usenergymap", plan: "current" }, subscription: "sub_ours_dead",
    customer: "cus_ourDeadSub", customer_details: { email: "lapsed@example.com" },
    amount_total: 4900, currency: "usd", success_url: OUR_SUCCESS,
  },
  // No metadata.app and no usable success_url: no signal either way, so not ours.
  cs_test_null_url: {
    id: "cs_test_null_url", mode: "payment", payment_status: "paid", metadata: {},
    customer: "cus_mystery", customer_details: { email: "mystery@example.com" },
    amount_total: 9900, currency: "usd", success_url: null,
  },
  cs_test_bad_url: {
    id: "cs_test_bad_url", mode: "payment", payment_status: "paid", metadata: {},
    customer: "cus_mystery2", customer_details: { email: "mystery2@example.com" },
    amount_total: 9900, currency: "usd", success_url: "usenergymap.com/data/success",
  },
};

const SUBSCRIPTIONS = {
  sub_audible: { id: "sub_audible", status: "active", current_period_end: 1789000000, cancel_at_period_end: false },
  sub_ours: { id: "sub_ours", status: "active", current_period_end: 1789000000, cancel_at_period_end: false },
  sub_ours_dead: { id: "sub_ours_dead", status: "canceled", current_period_end: 1780000000, cancel_at_period_end: true },
};

// The stub reads these two tables plus a flag for the "Stripe is down" case.
global.__STRIPE_STUB__ = { SESSIONS, SUBSCRIPTIONS, portalCalls: [], down: false };

process.env.STRIPE_SECRET_KEY = "sk_test_stub";

const { verifySession } = require("../lib/entitlement");
const Stripe = require("stripe");
const stripe = new Stripe("sk_test_stub");

const sessionRoute = require("../api/session.js");
const downloadRoute = require("../api/download.js");
const portalRoute = require("../api/portal.js");

// ---- harness ---------------------------------------------------------------
let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { results.push(["PASS", name]); })
    .catch((err) => { failures++; results.push(["FAIL", name, err && err.message]); });
}

function mockRes() {
  const res = {
    statusCode: 200, body: undefined, headers: {}, sent: false,
    setHeader(k, v) { this.headers[k.toLowerCase()] = v; return this; },
    status(c) { this.statusCode = c; return this; },
    json(b) { this.body = b; this.sent = true; return this; },
    send(b) { this.body = b; this.sent = true; return this; },
  };
  return res;
}

async function callRoute(handler, { query = {}, body = {}, method = "GET" } = {}) {
  const req = { method, query, body, headers: { host: "usenergymap.com" }, on() {} };
  const res = mockRes();
  await handler(req, res);
  return res;
}

// A response's full observable identity: status code plus the exact JSON bytes.
function fingerprint(res) {
  return res.statusCode + " " + JSON.stringify(res.body);
}

// projects.json / changelog.json come off disk instead of over the network.
const fs = require("fs");
global.fetch = async (url) => {
  const file = String(url).endsWith("/changelog.json") ? "changelog.json" : "projects.json";
  const text = fs.readFileSync(path.join(__dirname, "..", file), "utf8");
  return { ok: true, status: 200, json: async () => JSON.parse(text), text: async () => text };
};

const LEGACY_ONE_TIME = "cs_live_LEGACYONETIMEFIXTUREnotarealsessionid00000000000000000";

(async () => {
  // ===== THE CUSTOMER TEST ==================================================
  await check("REGRESSION: the legacy one-time buyer still verifies (metadata {}, host arm only)", async () => {
    const ent = await verifySession(stripe, LEGACY_ONE_TIME);
    assert.strictEqual(ent.ok, true, "must still be entitled");
    assert.strictEqual(ent.plan, "snapshot", "legacy fallback must still yield snapshot");
    assert.strictEqual(ent.mode, "payment");
    assert.strictEqual(ent.email, "legacy-buyer@example.com");
    assert.strictEqual(ent.amount, 149.99);
    assert.strictEqual(ent.currency, "USD");
    assert.strictEqual(SESSIONS[LEGACY_ONE_TIME].metadata.app, undefined, "fixture must keep metadata empty");
  });

  await check("REGRESSION: /api/session returns ok:true for the legacy access link", async () => {
    const res = await callRoute(sessionRoute, { query: { session_id: LEGACY_ONE_TIME } });
    assert.strictEqual(res.statusCode, 200);
    assert.strictEqual(res.body.ok, true);
    assert.strictEqual(res.body.plan, "snapshot");
    assert.strictEqual(res.body.customer, undefined, "customer id must never be returned");
    assert.strictEqual(res.body.status, undefined, "internal status must not be echoed");
  });

  await check("REGRESSION: /api/download serves him the dataset", async () => {
    const res = await callRoute(downloadRoute, { query: { session_id: LEGACY_ONE_TIME, format: "csv" } });
    assert.strictEqual(res.statusCode, 200);
    assert.ok(String(res.body).length > 100, "should be a real CSV body");
  });

  // ===== FINDING 3 — the dataset ============================================
  await check("GPS City Guide session is rejected by /api/download (xlsx, csv, geojson)", async () => {
    for (const format of ["xlsx", "csv", "geojson"]) {
      const res = await callRoute(downloadRoute, { query: { session_id: "cs_test_gpscityguide", format } });
      assert.strictEqual(res.statusCode, 402, `${format} must be refused`);
      assert.strictEqual(res.body.error, "Could not verify purchase.");
    }
  });

  await check("GPS City Guide session is rejected by /api/session", async () => {
    const res = await callRoute(sessionRoute, { query: { session_id: "cs_test_gpscityguide" } });
    assert.strictEqual(res.statusCode, 402);
    assert.strictEqual(res.body.ok, false);
    assert.ok(!JSON.stringify(res.body).includes("someone-else@example.com"), "no third-party email");
  });

  await check("GPS City Guide session is rejected by /api/portal", async () => {
    global.__STRIPE_STUB__.portalCalls = [];
    const res = await callRoute(portalRoute, { method: "POST", body: { session_id: "cs_test_gpscityguide" } });
    assert.strictEqual(res.statusCode, 402);
    assert.strictEqual(global.__STRIPE_STUB__.portalCalls.length, 0, "no portal may be minted");
  });

  await check("Audible Report subscription is rejected by /api/download?format=changelog", async () => {
    const res = await callRoute(downloadRoute, { query: { session_id: "cs_test_audible", format: "changelog" } });
    assert.strictEqual(res.statusCode, 402, "rejected at ownership, before the mode check");
    assert.strictEqual(res.body.error, "Could not verify purchase.");
  });

  await check("Audible Report subscription is rejected by /api/download?format=csv", async () => {
    const res = await callRoute(downloadRoute, { query: { session_id: "cs_test_audible", format: "csv" } });
    assert.strictEqual(res.statusCode, 402);
  });

  // ===== FINDING 1 — the portal =============================================
  await check("launchd session with a customer attached does NOT open the portal", async () => {
    global.__STRIPE_STUB__.portalCalls = [];
    const res = await callRoute(portalRoute, { method: "POST", body: { session_id: "cs_test_launchd" } });
    assert.strictEqual(res.statusCode, 402);
    assert.strictEqual(res.body.url, undefined);
    assert.strictEqual(global.__STRIPE_STUB__.portalCalls.length, 0, "no portal may be minted");
  });

  await check("a session verifySession rejects (unpaid, ours) does NOT open the portal", async () => {
    global.__STRIPE_STUB__.portalCalls = [];
    const ent = await verifySession(stripe, "cs_test_ours_unpaid");
    assert.strictEqual(ent.ok, false, "precondition: verifySession rejects it");
    const res = await callRoute(portalRoute, { method: "POST", body: { session_id: "cs_test_ours_unpaid" } });
    assert.strictEqual(res.statusCode, 402);
    assert.strictEqual(global.__STRIPE_STUB__.portalCalls.length, 0, "no portal may be minted");
  });

  await check("a cancelled subscription of OURS does NOT open the portal", async () => {
    global.__STRIPE_STUB__.portalCalls = [];
    const res = await callRoute(portalRoute, { method: "POST", body: { session_id: "cs_test_ours_sub_dead" } });
    assert.strictEqual(res.statusCode, 402);
    assert.strictEqual(global.__STRIPE_STUB__.portalCalls.length, 0);
  });

  await check("our own live subscriber still opens the portal", async () => {
    global.__STRIPE_STUB__.portalCalls = [];
    const res = await callRoute(portalRoute, { method: "POST", body: { session_id: "cs_test_ours_sub" } });
    assert.strictEqual(res.statusCode, 200);
    assert.ok(String(res.body.url).startsWith("https://billing.stripe.com/"), "portal url returned");
    assert.deepStrictEqual(global.__STRIPE_STUB__.portalCalls, ["cus_ourSub"]);
  });

  // ===== item 3 — the changelog gate reads correctly on an owned session ====
  await check("our own live subscriber still gets format=changelog", async () => {
    const res = await callRoute(downloadRoute, { query: { session_id: "cs_test_ours_sub", format: "changelog" } });
    assert.strictEqual(res.statusCode, 200);
    assert.ok(String(res.body).startsWith("period_from,period_to,kind"), "changelog CSV header");
  });

  await check("our own one-time buyer is still refused format=changelog (403, not 402)", async () => {
    const res = await callRoute(downloadRoute, { query: { session_id: LEGACY_ONE_TIME, format: "changelog" } });
    assert.strictEqual(res.statusCode, 403, "the mode gate, not the ownership gate");
    assert.ok(/Always Current/.test(res.body.error));
  });

  // ===== malformed / absent success_url =====================================
  await check("null success_url with no metadata.app is 'not ours', not a 500", async () => {
    for (const id of ["cs_test_null_url", "cs_test_bad_url"]) {
      const ent = await verifySession(stripe, id);
      assert.strictEqual(ent.ok, false);
      const res = await callRoute(sessionRoute, { query: { session_id: id } });
      assert.strictEqual(res.statusCode, 402, "402, never 500");
      const dl = await callRoute(downloadRoute, { query: { session_id: id, format: "csv" } });
      assert.strictEqual(dl.statusCode, 402);
    }
  });

  // ===== the oracle =========================================================
  await check("/api/session: 'not ours', 'not paid' and 'no such session' are indistinguishable", async () => {
    const cases = {
      "not ours (GPS City Guide)": "cs_test_gpscityguide",
      "not ours (Audible sub)": "cs_test_audible",
      "not ours (launchd)": "cs_test_launchd",
      "not ours (no signal)": "cs_test_null_url",
      "not paid (ours, abandoned)": "cs_test_ours_unpaid",
      "dead subscription (ours)": "cs_test_ours_sub_dead",
      "no such session": "cs_live_thisIdWasNeverIssuedAnywhere",
      "malformed id": "not_a_session_id",
      "empty id": "",
    };
    const prints = {};
    for (const [label, id] of Object.entries(cases)) {
      const res = await callRoute(sessionRoute, { query: { session_id: id } });
      prints[label] = fingerprint(res);
    }
    const distinct = new Set(Object.values(prints));
    assert.strictEqual(
      distinct.size, 1,
      "every negative must be byte-identical, got:\n" +
        Object.entries(prints).map(([k, v]) => `      ${k} -> ${v}`).join("\n")
    );
    assert.strictEqual([...distinct][0], '402 {"ok":false,"error":"Could not verify purchase."}');
  });

  await check("/api/download and /api/portal negatives are uniform too", async () => {
    const ids = ["cs_test_gpscityguide", "cs_test_ours_unpaid", "cs_live_neverIssued", "nope"];
    const dl = new Set();
    const pt = new Set();
    for (const id of ids) {
      dl.add(fingerprint(await callRoute(downloadRoute, { query: { session_id: id, format: "csv" } })));
      pt.add(fingerprint(await callRoute(portalRoute, { method: "POST", body: { session_id: id } })));
    }
    assert.strictEqual(dl.size, 1, "download negatives differ: " + [...dl].join(" | "));
    assert.strictEqual(pt.size, 1, "portal negatives differ: " + [...pt].join(" | "));
  });

  // ===== the webhook's retry contract =======================================
  await check("a Stripe outage still THROWS out of verifySession (webhook retry intact)", async () => {
    global.__STRIPE_STUB__.down = true;
    try {
      await verifySession(stripe, LEGACY_ONE_TIME);
      throw new Error("verifySession swallowed a connection error");
    } catch (err) {
      assert.strictEqual(err.type, "StripeConnectionError", "must surface for the webhook to retry on");
    } finally {
      global.__STRIPE_STUB__.down = false;
    }
  });

  await check("a Stripe outage is still a rejection, not a 500, at the routes", async () => {
    global.__STRIPE_STUB__.down = true;
    try {
      const res = await callRoute(sessionRoute, { query: { session_id: LEGACY_ONE_TIME } });
      assert.strictEqual(fingerprint(res), '402 {"ok":false,"error":"Could not verify purchase."}');
    } finally {
      global.__STRIPE_STUB__.down = false;
    }
  });

  // ===== ownership is decided before anything is read =======================
  await check("ownership is decided before payment_status / customer / email are read", async () => {
    const reads = [];
    const tripwire = new Proxy(
      { ...SESSIONS.cs_test_gpscityguide },
      {
        get(target, prop) {
          if (typeof prop === "string") reads.push(prop);
          return target[prop];
        },
      }
    );
    const spyStripe = {
      checkout: { sessions: { retrieve: async () => tripwire } },
      subscriptions: { retrieve: async () => { throw new Error("must not be reached"); } },
    };
    const ent = await verifySession(spyStripe, "cs_test_gpscityguide");
    assert.strictEqual(ent.ok, false);
    for (const forbidden of ["payment_status", "mode", "customer", "customer_details", "customer_email", "amount_total", "currency"]) {
      assert.ok(!reads.includes(forbidden), `read ${forbidden} off a session that is not ours (reads: ${reads.join(", ")})`);
    }
  });

  // ---- report ------------------------------------------------------------
  console.log("");
  for (const [state, name, msg] of results) {
    console.log(`  ${state === "PASS" ? "✓" : "✗"} ${name}${msg ? "\n      " + msg : ""}`);
  }
  console.log(`\n  ${results.length - failures}/${results.length} passed\n`);
  process.exit(failures ? 1 : 0);
})();
