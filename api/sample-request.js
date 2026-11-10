// POST /api/sample-request   body: { email, source?, website? }
//
// The "free 25-project sample CSV" form on /data. Unlike /api/lead (which emails a
// LINK to the 100-row sample from /bess and the state pages), this one:
//
//   1. stores the address as a Resend contact — the site has no database, and Resend is
//      already the email provider, so the list lives there (optionally in a segment);
//   2. emails the CSV itself as an attachment, so the file arrives even if links are
//      stripped or the visitor reads mail offline;
//   3. BCCs DELIVERY_BCC, tagged with the capturing page, same as /api/lead.
//
// Storing the contact is best-effort and never blocks delivery: a visitor who asked for
// a file gets the file even if the contacts call fails (the BCC is still a record).
//
// Env:
//   RESEND_API_KEY      re_...  (already set — shared with the webhook and /api/lead)
//   DELIVERY_BCC        optional, already set
//   RESEND_SEGMENT_ID   optional — add sample requesters to this Resend segment
//
// `website` is a honeypot: a hidden field real visitors never fill. A bot that fills it
// gets a normal-looking 200 and nothing is sent, so this route can't be pointed at a
// stranger's inbox by a naive form-filler.

const { loadProjectsFromDisk, fetchProjects, pickBalancedSample, toCSV } = require("../lib/dataset");
const { resolveAll } = require("../lib/pricing");

const RESEND_API = "https://api.resend.com";
const FROM = "US Energy Map <data@usenergymap.com>";
const SAMPLE_SIZE = 25;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/; // same loose shape as /api/lead and the buy form

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function cleanSource(v) {
  if (typeof v !== "string") return "";
  return v.trim().replace(/[^A-Za-z0-9/\-_ .]/g, "").slice(0, 80);
}

async function loadProjects(req) {
  try {
    return loadProjectsFromDisk();
  } catch (diskErr) {
    console.warn("sample-request: projects.json not readable from disk, falling back to HTTP:", diskErr.message);
    const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
    const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
    return fetchProjects(`${proto}://${host}`);
  }
}

function priceLine(prices) {
  const snap = prices.snapshot.display;
  const monthly = prices.monthly.display;
  const annual = prices.annual.display;
  const parts = [];
  if (snap) parts.push(`${snap} once`);
  parts.push(`${monthly}/mo`);
  if (annual) parts.push(`${annual}/yr`);
  return parts.join(", or ");
}

function buildEmail(prices) {
  const offer = priceLine(prices);
  const text = [
    "Here's your free 25-project sample from US Energy Map (CSV attached).",
    "",
    "The largest tracked data centers and battery storage (BESS) projects, with operator,",
    "location, county, capacity, status, year online and a public source on each row.",
    "It opens straight in Excel or Sheets.",
    "",
    `The full dataset — every tracked project as Excel + CSV + GeoJSON — is at`,
    `https://usenergymap.com/data (${offer}; subscriptions add a monthly row-level change log).`,
    "",
    "Need an invoice or a team license? Email steve@usenergymap.com.",
    "",
    "— US Energy Map",
    "https://usenergymap.com",
  ].join("\n");

  const html = [
    '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#111">',
    "<p>Here's your free 25-project sample from <strong>US Energy Map</strong> — the CSV is attached.</p>",
    "<p>The largest tracked data centers and battery storage (BESS) projects, with operator, location, county, capacity, status, year online and a public source on each row. It opens straight in Excel or Sheets.</p>",
    `<p>The full dataset — every tracked project as Excel + CSV + GeoJSON — is at <a href="https://usenergymap.com/data" style="color:#0b6">usenergymap.com/data</a> (${escapeHtml(offer)}; subscriptions add a monthly row-level change log).</p>`,
    '<p>Need an invoice or a team license? Email <a href="mailto:steve@usenergymap.com" style="color:#0b6">steve@usenergymap.com</a>.</p>',
    '<p style="color:#666;font-size:13px">— US Energy Map · <a href="https://usenergymap.com" style="color:#666">usenergymap.com</a></p>',
    "</div>",
  ].join("");

  return { subject: "Your free US Energy Map sample (25 projects, CSV attached)", text, html };
}

// Resend answers a duplicate contact with an error; that is "already stored", not a
// failure worth surfacing. Anything else is logged and swallowed.
async function storeContact(email, source) {
  const body = { email, unsubscribed: false };
  const segment = (process.env.RESEND_SEGMENT_ID || "").trim();
  if (segment) body.segments = [{ id: segment }];
  try {
    const r = await fetch(`${RESEND_API}/contacts`, {
      method: "POST",
      headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) return "stored";
    const detail = await r.text().catch(() => "");
    if (r.status === 409 || /already exists/i.test(detail)) return "exists";
    console.error(`sample-request: contact store ${r.status} (${source || "unknown"}): ${detail.slice(0, 300)}`);
    return "failed";
  } catch (err) {
    console.error("sample-request: contact store error:", err.message);
    return "failed";
  }
}

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");

  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "Method not allowed" });
  }

  const body = req.body || {};
  const email = typeof body.email === "string" ? body.email.trim() : "";
  const source = cleanSource(body.source) || "data";

  if (typeof body.website === "string" && body.website.trim()) {
    return res.status(200).json({ ok: true }); // honeypot: look normal, do nothing
  }
  if (!EMAIL_RE.test(email) || email.length > 254) {
    return res.status(400).json({ error: "Enter a valid email address." });
  }
  if (!process.env.RESEND_API_KEY) {
    console.error("sample-request: RESEND_API_KEY not configured; sample not sent.");
    return res.status(500).json({ error: "Sample delivery is not configured yet." });
  }

  try {
    const { projects, last_updated } = await loadProjects(req);
    const rows = pickBalancedSample(projects, SAMPLE_SIZE);
    const stamp = (last_updated || new Date().toISOString()).slice(0, 10);
    const csv = toCSV(rows);

    const Stripe = require("stripe");
    const stripe = process.env.STRIPE_SECRET_KEY ? new Stripe(process.env.STRIPE_SECRET_KEY) : null;
    const [prices, stored] = await Promise.all([resolveAll(stripe), storeContact(email, source)]);

    const { subject, text, html } = buildEmail(prices);
    const payload = {
      from: FROM,
      to: [email],
      subject,
      text,
      html,
      reply_to: "hello@usenergymap.com",
      attachments: [
        { filename: `usenergymap-sample-25-${stamp}.csv`, content: Buffer.from(csv, "utf8").toString("base64") },
      ],
      headers: { "X-UEM-Lead-Source": source },
    };
    if (process.env.DELIVERY_BCC) payload.bcc = [process.env.DELIVERY_BCC];

    const r = await fetch(`${RESEND_API}/emails`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.RESEND_API_KEY}`,
        "Content-Type": "application/json",
        // Double-clicks and repeat submits within the hour collapse into one send.
        "Idempotency-Key": `usenergymap-sample25-${email.toLowerCase()}-${new Date().toISOString().slice(0, 13)}`,
      },
      body: JSON.stringify(payload),
    });

    if (!r.ok) {
      const detail = await r.text().catch(() => "");
      console.error(`sample-request: Resend ${r.status} (${source}): ${detail.slice(0, 300)}`);
      return res.status(502).json({ error: "We couldn't send the sample just now. Try again in a moment." });
    }

    console.log(`sample-request: sent ${rows.length} rows (${source}, contact ${stored}).`);
    return res.status(200).json({ ok: true, rows: rows.length });
  } catch (err) {
    console.error("sample-request error:", err);
    return res.status(500).json({ error: "We couldn't send the sample just now. Try again in a moment." });
  }
};
