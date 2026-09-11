// POST /api/lead   body: { email, source? }
//
// Free-sample request from the capture block on /bess and the state pages. Emails the
// visitor a link to /api/sample and BCCs DELIVERY_BCC so the lead lands in an inbox.
//
// No database by design: this site has none, and a lead that arrives as an email is a
// lead someone actually replies to. `source` is the page that captured it, so the BCC
// says which ad group is working.
//
// Env (shared with api/stripe/webhook.js — nothing new to configure):
//   RESEND_API_KEY   re_...
//   DELIVERY_BCC     address that gets a copy of every send

const RESEND_ENDPOINT = "https://api.resend.com/emails";
const FROM = "US Energy Map <data@usenergymap.com>";

// Same shape the buy form on /data uses, kept deliberately loose — rejecting a valid
// address is worse than accepting one that bounces.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// The source label is echoed into the BCC subject, so it never leaves this allowlist
// of characters — it arrives from the browser and is not to be trusted with markup.
function cleanSource(v) {
  if (typeof v !== "string") return "";
  return v.trim().replace(/[^A-Za-z0-9/\-_ .]/g, "").slice(0, 80);
}

function buildEmail(sampleLink) {
  const text = [
    "Here's your free sample from US Energy Map.",
    "",
    "100 largest tracked projects — data centers and battery storage (BESS) — with",
    "operator, location, capacity, status, year online and a public source on each row:",
    sampleLink,
    "",
    "It opens straight in Excel or Sheets. The full dataset (every tracked project, as",
    "Excel + CSV + GeoJSON) is at https://usenergymap.com/data — $199 once, or $49/mo",
    "refreshed monthly with a row-level change log of what moved.",
    "",
    "Questions, or need a state or sector cut? Just reply to this email.",
    "",
    "— US Energy Map",
    "https://usenergymap.com",
  ].join("\n");

  const html = [
    '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#111">',
    "<p>Here's your free sample from <strong>US Energy Map</strong>.</p>",
    "<p>100 largest tracked projects — data centers and battery storage (BESS) — with operator, location, capacity, status, year online and a public source on each row.</p>",
    `<p><a href="${escapeHtml(sampleLink)}" style="color:#0b6">Download the sample CSV</a></p>`,
    '<p>It opens straight in Excel or Sheets. The full dataset (every tracked project, as Excel + CSV + GeoJSON) is at <a href="https://usenergymap.com/data" style="color:#0b6">usenergymap.com/data</a> — $199 once, or $49/mo refreshed monthly with a row-level change log of what moved.</p>',
    "<p>Questions, or need a state or sector cut? Just reply to this email.</p>",
    '<p style="color:#666;font-size:13px">— US Energy Map · <a href="https://usenergymap.com" style="color:#666">usenergymap.com</a></p>',
    "</div>",
  ].join("");

  return { subject: "Your free US Energy Map sample (100 projects, CSV)", text, html };
}

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");

  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "Method not allowed" });
  }

  const body = req.body || {};
  const email = typeof body.email === "string" ? body.email.trim() : "";
  const source = cleanSource(body.source);

  if (!EMAIL_RE.test(email) || email.length > 254) {
    return res.status(400).json({ error: "Enter a valid email address." });
  }

  if (!process.env.RESEND_API_KEY) {
    console.error("lead: RESEND_API_KEY not configured; sample not sent.");
    return res.status(500).json({ error: "Sample delivery is not configured yet." });
  }

  try {
    const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
    const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
    const sampleLink = `${proto}://${host}/api/sample?format=csv`;

    const { subject, text, html } = buildEmail(sampleLink);
    const payload = { from: FROM, to: [email], subject, text, html, reply_to: "hello@usenergymap.com" };
    const bcc = process.env.DELIVERY_BCC;
    if (bcc) {
      payload.bcc = [bcc];
      // The BCC is the lead notification, so it carries what the visitor's copy cannot:
      // which page captured them.
      payload.subject = subject;
      payload.headers = { "X-UEM-Lead-Source": source || "unknown" };
    }

    const r = await fetch(RESEND_ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.RESEND_API_KEY}`,
        "Content-Type": "application/json",
        // A double-click, or a visitor who requests the sample from two state pages in
        // the same hour, collapses into one send at the provider.
        "Idempotency-Key": `usenergymap-sample-${email.toLowerCase()}-${new Date().toISOString().slice(0, 13)}`,
      },
      body: JSON.stringify(payload),
    });

    if (!r.ok) {
      const detail = await r.text().catch(() => "");
      console.error(`lead: Resend ${r.status} for ${source || "unknown"}: ${detail.slice(0, 300)}`);
      return res.status(502).json({ error: "We couldn't send the sample just now. Try again in a moment." });
    }

    return res.status(200).json({ ok: true, sample_url: "/api/sample?format=csv" });
  } catch (err) {
    console.error("lead error:", err);
    return res.status(500).json({ error: "We couldn't send the sample just now. Try again in a moment." });
  }
};
