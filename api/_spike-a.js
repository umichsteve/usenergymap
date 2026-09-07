// TEMPORARY SPIKE — raw body handling on Vercel Node functions. Deleted before merge.
// Variant A: no `config` export. Buffer the request stream and see what arrives.
const crypto = require("crypto");
const Stripe = require("stripe");

const SPIKE_SECRET = "whsec_spike_notarealsecret_0123456789abcdef";

function buffer(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

module.exports = async (req, res) => {
  const out = {
    variant: "A (no config export)",
    node: process.version,
    reqBodyType: typeof req.body,
    reqBodyIsBuffer: Buffer.isBuffer(req.body),
    reqBodyCtor: req.body && req.body.constructor ? req.body.constructor.name : null,
    readableEnded: req.readableEnded,
    complete: req.complete,
    env_present: {
      STRIPE_SECRET_KEY: !!process.env.STRIPE_SECRET_KEY,
      STRIPE_SECRET_KEY_mode: process.env.STRIPE_SECRET_KEY
        ? process.env.STRIPE_SECRET_KEY.slice(0, 7)
        : null,
      STRIPE_WEBHOOK_SECRET: !!process.env.STRIPE_WEBHOOK_SECRET,
      RESEND_API_KEY: !!process.env.RESEND_API_KEY,
      DELIVERY_BCC: !!process.env.DELIVERY_BCC,
    },
  };

  try {
    const buf = await buffer(req);
    out.streamBytes = buf.length;
    out.streamSha256 = crypto.createHash("sha256").update(buf).digest("hex");
    out.streamFirst40 = buf.slice(0, 40).toString("utf8");

    // Does constructEvent accept what we buffered? Sign it here with a throwaway secret
    // (the caller sends the same signature in stripe-signature; see the test script).
    const stripe = new Stripe("sk_test_spike_placeholder");
    const sig = req.headers["stripe-signature"];
    if (sig) {
      try {
        const ev = stripe.webhooks.constructEvent(buf, sig, SPIKE_SECRET);
        out.constructEvent = { ok: true, id: ev.id, type: ev.type };
      } catch (e) {
        out.constructEvent = { ok: false, error: e.message };
      }
      // Control: what happens if we hand it the re-serialized parsed body instead?
      if (req.body !== undefined) {
        try {
          const reser = Buffer.from(
            typeof req.body === "string" ? req.body : JSON.stringify(req.body)
          );
          out.reserializedSha256 = crypto.createHash("sha256").update(reser).digest("hex");
          stripe.webhooks.constructEvent(reser, sig, SPIKE_SECRET);
          out.constructEventFromParsed = { ok: true };
        } catch (e) {
          out.constructEventFromParsed = { ok: false, error: e.message };
        }
      }
    }
  } catch (e) {
    out.streamError = e.message;
  }

  res.setHeader("Cache-Control", "no-store");
  return res.status(200).json(out);
};
