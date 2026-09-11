// GET /assets/ads.js  (rewritten to /api/ads in vercel.json)
//
// Serves the Google Ads gtag loader with the tag ID and conversion labels pulled from
// environment variables, so no advertising ID is committed to the repo and the preview
// and production deployments can point at different Google Ads accounts.
//
// This is a function rather than a static asset because the site has no build step —
// there is nowhere else for an env var to reach the browser. The response is immutable
// for a given deployment (env vars bind at deploy time), so it is CDN-cached and costs
// one invocation per edge node per hour, not one per pageview.
//
// Env (Vercel → Project → Settings → Environment Variables):
//   GOOGLE_ADS_TAG_ID          AW-XXXXXXXXX   — required; without it this file is a no-op
//   GOOGLE_ADS_LEAD_LABEL      the label half of the "lead" conversion's send_to
//   GOOGLE_ADS_PURCHASE_LABEL  the label half of the "purchase" conversion's send_to
//
// In Google Ads a conversion's send_to is "AW-XXXXXXXXX/AbCdEfGhIjKlMnOp". Only the
// label half is stored here; the tag ID is prepended at runtime.

// Everything below is interpolated into a JavaScript string literal, so values are
// restricted to the character set Google actually uses for tag IDs and labels. An env
// var containing a quote or angle bracket is treated as unset rather than escaped —
// a malformed tag ID is a configuration mistake, not something to render.
const SAFE = /^[A-Za-z0-9_-]{1,64}$/;
const clean = (v) => (typeof v === "string" && SAFE.test(v.trim()) ? v.trim() : "");

function build() {
  const tag = clean(process.env.GOOGLE_ADS_TAG_ID);
  const lead = clean(process.env.GOOGLE_ADS_LEAD_LABEL);
  const purchase = clean(process.env.GOOGLE_ADS_PURCHASE_LABEL);

  if (!tag || tag.indexOf("AW-") !== 0) {
    // Drain the queue into nothing so callers never accumulate an unbounded array,
    // and leave a console hint that is invisible to visitors but obvious in DevTools.
    return `/* US Energy Map — Google Ads tag not configured (GOOGLE_ADS_TAG_ID unset). */
(function () {
  window.uemAds = { configured: false, tagId: null, labels: {} };
  window.uemAdsQueue = [];
  window.uemAdsConversion = function () {};
})();
`;
  }

  return `/* US Energy Map — Google Ads gtag loader. Generated per deployment from env vars. */
(function () {
  var TAG = ${JSON.stringify(tag)};
  var LABELS = ${JSON.stringify({ lead, purchase })};

  // assets/analytics.js defines window.gtag for GA4 and loads gtag.js. If GA is
  // switched off, this file is the only tag on the page and must bootstrap it itself.
  if (typeof window.gtag !== "function") {
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + TAG;
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
  }
  window.gtag("config", TAG);

  window.uemAds = { configured: true, tagId: TAG, labels: LABELS };

  // Fires a Google Ads conversion by name. Returns true when a hit was actually sent,
  // so callers (and the preview smoke test) can tell "fired" from "not configured".
  window.uemAdsConversion = function (name, params) {
    var label = LABELS[name];
    if (!label) return false;
    var payload = { send_to: TAG + "/" + label };
    if (params) {
      for (var k in params) {
        if (Object.prototype.hasOwnProperty.call(params, k) && params[k] !== undefined && params[k] !== null) {
          payload[k] = params[k];
        }
      }
    }
    window.gtag("event", "conversion", payload);
    return true;
  };

  // Conversions requested before this file finished loading (it is async) are queued
  // by the stub in assets/analytics.js. Replay them in order, then retire the queue.
  var q = window.uemAdsQueue || [];
  window.uemAdsQueue = { push: function (args) { window.uemAdsConversion.apply(null, args); } };
  for (var i = 0; i < q.length; i++) window.uemAdsConversion.apply(null, q[i]);
})();
`;
}

module.exports = (req, res) => {
  res.setHeader("Content-Type", "application/javascript; charset=utf-8");
  // Env vars bind at deploy time and Vercel purges the CDN on deploy, so this is safe
  // to cache hard. Changing a tag ID requires a redeploy either way.
  res.setHeader("Cache-Control", "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400");
  res.setHeader("X-Content-Type-Options", "nosniff");
  return res.status(200).send(build());
};
