#!/usr/bin/env node
// Pricing (env-driven checkout + display) and the /data free-sample endpoint.
// No network, no Stripe account: `stripe` resolves to a fake below and global.fetch is
// replaced, so nothing here can charge a card or send a real email.
//
//   node scripts/test-pricing-sample.js

const assert = require("assert");
const path = require("path");
const Module = require("module");

// ---- fake stripe: records checkout params, serves Prices from a table -------
const STATE = { created: [], prices: {} };
function FakeStripe() {
  return {
    prices: {
      retrieve: async (id) => {
        if (!STATE.prices[id]) {
          const e = new Error(`No such price: '${id}'`);
          e.type = "StripeInvalidRequestError"; e.statusCode = 404;
          throw e;
        }
        return STATE.prices[id];
      },
    },
    checkout: { sessions: { create: async (p) => { STATE.created.push(p); return { url: "https://checkout.stripe.test/s" }; } } },
  };
}
const realResolve = Module._resolveFilename;
const FAKE = path.join(__dirname, "__fake_stripe__.js");
require.cache[FAKE] = { id: FAKE, filename: FAKE, loaded: true, exports: FakeStripe };
Module._resolveFilename = function (request, ...rest) {
  if (request === "stripe") return FAKE;
  return realResolve.call(this, request, ...rest);
};

const ENV_KEYS = ["PRICE_SNAPSHOT_CENTS", "PRICE_ANNUAL_CENTS", "STRIPE_PRICE_SNAPSHOT", "STRIPE_PRICE_ANNUAL", "RESEND_API_KEY", "DELIVERY_BCC", "RESEND_SEGMENT_ID"];
function resetEnv(extra = {}) {
  for (const k of ENV_KEYS) delete process.env[k];
  process.env.STRIPE_SECRET_KEY = "sk_test_fake";
  Object.assign(process.env, extra);
  STATE.created = [];
}

const { resolveAll } = require("../lib/pricing");
const checkout = require("../api/create-checkout");
const pricingRoute = require("../api/pricing");
const sampleRequest = require("../api/sample-request");

function mockRes() {
  return {
    statusCode: 200, body: undefined, headers: {},
    setHeader(k, v) { this.headers[k.toLowerCase()] = v; return this; },
    status(c) { this.statusCode = c; return this; },
    json(b) { this.body = b; return this; },
    send(b) { this.body = b; return this; },
  };
}
async function call(handler, { method = "POST", body = {} } = {}) {
  const res = mockRes();
  await handler({ method, body, query: {}, headers: { host: "usenergymap.com" } }, res);
  return res;
}

let failures = 0;
const results = [];
async function check(name, fn) {
  try { await fn(); results.push(["PASS", name]); }
  catch (err) { failures++; results.push(["FAIL", name, err && err.message]); }
}

(async () => {
  console.warn = console.error = console.log = ((orig) => (...a) => { if (process.env.VERBOSE) orig(...a); })(console.log);

  // ===== pricing ===========================================================
  await check("defaults: $199 snapshot, $490 annual, source=default", async () => {
    resetEnv();
    const p = await resolveAll(null);
    assert.strictEqual(p.snapshot.display, "$199"); assert.strictEqual(p.snapshot.source, "default");
    assert.strictEqual(p.annual.display, "$490"); assert.strictEqual(p.annual.savings_display, "$98");
  });

  await check("PRICE_*_CENTS env vars drive the displayed price", async () => {
    resetEnv({ PRICE_SNAPSHOT_CENTS: "24900", PRICE_ANNUAL_CENTS: "52900" });
    const p = await resolveAll(null);
    assert.strictEqual(p.snapshot.display, "$249"); assert.strictEqual(p.snapshot.source, "env");
    assert.strictEqual(p.annual.display, "$529"); assert.strictEqual(p.annual.source, "env");
    assert.strictEqual(p.annual.savings_display, "$59");
  });

  await check("garbage env value falls back to default, never NaN", async () => {
    resetEnv({ PRICE_SNAPSHOT_CENTS: "199.00", PRICE_ANNUAL_CENTS: "-5" });
    const p = await resolveAll(null);
    assert.strictEqual(p.snapshot.amount_cents, 19900); assert.strictEqual(p.annual.amount_cents, 49000);
  });

  await check("a Stripe Price id overrides cents and its amount is what displays", async () => {
    resetEnv({ PRICE_SNAPSHOT_CENTS: "100", STRIPE_PRICE_SNAPSHOT: "price_snap", STRIPE_PRICE_ANNUAL: "price_year" });
    STATE.prices = {
      price_snap: { id: "price_snap", active: true, currency: "usd", unit_amount: 21900, type: "one_time" },
      price_year: { id: "price_year", active: true, currency: "usd", unit_amount: 47000, type: "recurring", recurring: { interval: "year", interval_count: 1 } },
    };
    const res = await call(pricingRoute, { method: "GET" });
    assert.strictEqual(res.body.snapshot.display, "$219"); assert.strictEqual(res.body.snapshot.source, "stripe");
    assert.strictEqual(res.body.annual.display, "$470");
  });

  await check("a monthly Price in the annual slot is refused (display null, checkout 500)", async () => {
    resetEnv({ STRIPE_PRICE_ANNUAL: "price_month" });
    STATE.prices = { price_month: { id: "price_month", active: true, currency: "usd", unit_amount: 4900, type: "recurring", recurring: { interval: "month" } } };
    const p = await resolveAll(FakeStripe());
    assert.strictEqual(p.annual.amount_cents, null);
    const res = await call(checkout, { body: { plan: "annual", email: "a@b.co" } });
    assert.strictEqual(res.statusCode, 500); assert.strictEqual(STATE.created.length, 0);
  });

  // ===== checkout ==========================================================
  await check("snapshot checkout charges PRICE_SNAPSHOT_CENTS inline", async () => {
    resetEnv({ PRICE_SNAPSHOT_CENTS: "24900" });
    const res = await call(checkout, { body: { plan: "snapshot", email: "a@b.co" } });
    assert.strictEqual(res.statusCode, 200);
    const p = STATE.created[0];
    assert.strictEqual(p.mode, "payment");
    assert.strictEqual(p.line_items[0].price_data.unit_amount, 24900);
    assert.deepStrictEqual(p.metadata, { plan: "snapshot", app: "usenergymap" });
  });

  await check("snapshot checkout uses STRIPE_PRICE_SNAPSHOT when set", async () => {
    resetEnv({ STRIPE_PRICE_SNAPSHOT: "price_snap" });
    STATE.prices = { price_snap: { id: "price_snap", active: true, currency: "usd", unit_amount: 21900, type: "one_time" } };
    await call(checkout, { body: { plan: "snapshot" } });
    assert.deepStrictEqual(STATE.created[0].line_items[0], { quantity: 1, price: "price_snap" });
  });

  await check("annual checkout: yearly subscription tagged plan=current, billing=annual", async () => {
    resetEnv({ PRICE_ANNUAL_CENTS: "49000" });
    await call(checkout, { body: { plan: "annual", email: "a@b.co" } });
    const p = STATE.created[0];
    assert.strictEqual(p.mode, "subscription");
    assert.deepStrictEqual(p.line_items[0].price_data.recurring, { interval: "year" });
    assert.strictEqual(p.line_items[0].price_data.unit_amount, 49000);
    // plan must stay "current": lib/entitlement.js and the webhook copy key off it.
    assert.deepStrictEqual(p.metadata, { plan: "current", app: "usenergymap", billing: "annual" });
    assert.deepStrictEqual(p.subscription_data.metadata, p.metadata);
  });

  await check("monthly checkout is unchanged ($49/mo inline, plan=current)", async () => {
    resetEnv({ PRICE_SNAPSHOT_CENTS: "1", PRICE_ANNUAL_CENTS: "1" });
    await call(checkout, { body: { plan: "current" } });
    const p = STATE.created[0];
    assert.strictEqual(p.line_items[0].price_data.unit_amount, 4900);
    assert.deepStrictEqual(p.line_items[0].price_data.recurring, { interval: "month" });
    assert.deepStrictEqual(p.metadata, { plan: "current", app: "usenergymap" });
  });

  // ===== sample request ====================================================
  const sent = [];
  global.fetch = async (url, opts) => {
    sent.push({ url, body: JSON.parse(opts.body), headers: opts.headers });
    if (url.endsWith("/contacts") && sent.filter((s) => s.url.endsWith("/contacts")).length > 1) {
      return { ok: false, status: 409, text: async () => "Contact already exists" };
    }
    return { ok: true, status: 200, json: async () => ({ id: "x" }), text: async () => "{}" };
  };

  await check("sample: stores contact (with segment) and emails a 25-row CSV attachment", async () => {
    resetEnv({ RESEND_API_KEY: "re_test", DELIVERY_BCC: "leads@example.com", RESEND_SEGMENT_ID: "seg_1", PRICE_SNAPSHOT_CENTS: "24900" });
    sent.length = 0;
    const res = await call(sampleRequest, { body: { email: "buyer@example.com", source: "data" } });
    assert.strictEqual(res.statusCode, 200, JSON.stringify(res.body));
    const contact = sent.find((s) => s.url.endsWith("/contacts"));
    assert.deepStrictEqual(contact.body, { email: "buyer@example.com", unsubscribed: false, segments: [{ id: "seg_1" }] });
    const mail = sent.find((s) => s.url.endsWith("/emails")).body;
    assert.deepStrictEqual(mail.to, ["buyer@example.com"]);
    assert.deepStrictEqual(mail.bcc, ["leads@example.com"]);
    const csv = Buffer.from(mail.attachments[0].content, "base64").toString("utf8");
    const lines = csv.trim().split("\n");
    assert.strictEqual(lines.length, 26, "header + 25 rows");
    assert.ok(lines[0].startsWith("id,name,type"));
    assert.ok(/data_center/.test(csv) && /,bess,/.test(csv), "sample has both layers");
    assert.ok(mail.text.includes("$249 once"), "email quotes env price");
    assert.ok(mail.text.includes("steve@usenergymap.com"));
  });

  await check("sample: an already-stored contact still gets the file", async () => {
    resetEnv({ RESEND_API_KEY: "re_test" });
    const res = await call(sampleRequest, { body: { email: "buyer@example.com" } });
    assert.strictEqual(res.statusCode, 200);
  });

  await check("sample: honeypot filled → 200, nothing sent", async () => {
    resetEnv({ RESEND_API_KEY: "re_test" });
    sent.length = 0;
    const res = await call(sampleRequest, { body: { email: "x@example.com", website: "spam" } });
    assert.strictEqual(res.statusCode, 200); assert.strictEqual(sent.length, 0);
  });

  await check("sample: invalid email → 400, nothing sent", async () => {
    resetEnv({ RESEND_API_KEY: "re_test" });
    sent.length = 0;
    const res = await call(sampleRequest, { body: { email: "nope" } });
    assert.strictEqual(res.statusCode, 400); assert.strictEqual(sent.length, 0);
  });

  await check("sample: GET is 405", async () => {
    const res = await call(sampleRequest, { method: "GET" });
    assert.strictEqual(res.statusCode, 405);
  });

  for (const [state, name, msg] of results) {
    process.stdout.write(`  ${state === "PASS" ? "✓" : "✗"} ${name}${msg ? "\n      " + msg : ""}\n`);
  }
  process.stdout.write(`\n  ${results.length - failures}/${results.length} passed\n\n`);
  process.exit(failures ? 1 : 0);
})();
