/* US Energy Map — free-sample email capture.
   Drives every [data-sample-form] block on the page: posts the address to /api/lead,
   which emails the sample, then fires the Google Ads "lead" conversion.

   Also exposes window.uemFireLead(source) for pages that drive their own form —
   /data posts to /api/sample-request and calls the helper on success so both paths
   share the 30-day dedupe window.

   Included on /bess, the state pages and /data. The conversion call goes through
   window.uemAdsConversion, which is queued in assets/analytics.js until
   /assets/ads.js lands — so a fast submit still counts. */
(function () {
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

  // A visitor who submits on one state page and then another is one lead, not two.
  // Google Ads would happily count both, and the spend decision that follows would be
  // wrong. localStorage, not sessionStorage: sessionStorage is scoped to a single tab,
  // so opening a second state page in a new tab would have re-counted the same person
  // (observed on the preview before this was changed).
  //
  // Suppression expires after 30 days. Permanent would under-count a prospect who
  // genuinely comes back a quarter later, which is a real lead worth bidding on.
  var FIRED_KEY = "uem_lead_fired_at";
  var SUPPRESS_MS = 30 * 24 * 60 * 60 * 1000;
  function alreadyFired() {
    try {
      var at = parseInt(localStorage.getItem(FIRED_KEY), 10);
      return !!at && (Date.now() - at) < SUPPRESS_MS;
    } catch (e) { return false; }
  }
  function markFired() {
    try { localStorage.setItem(FIRED_KEY, String(Date.now())); } catch (e) { /* private mode */ }
  }

  // Shared success-side firing: dedupe + Google Ads conversion + GA4 generate_lead.
  // Exposed so /data (which posts to /api/sample-request, not /api/lead) can reuse the
  // same dedupe window — a visitor who submits on a state page and on /data is one lead.
  window.uemFireLead = function (source) {
    if (alreadyFired()) return false;
    markFired();
    // Google Ads conversion. No value: a free sample has no revenue attached,
    // and a made-up number would poison Smart Bidding's target.
    if (typeof window.uemAdsConversion === "function") {
      window.uemAdsConversion("lead", { event_callback: function () {} });
    }
    // GA4 keeps its own standard event so the funnel is visible there too.
    if (typeof window.gtag === "function") {
      window.gtag("event", "generate_lead", { source: source, method: "free_sample" });
    }
    return true;
  };

  function init(block) {
    var form = block.querySelector("form");
    var input = block.querySelector('input[type="email"]');
    var button = block.querySelector("button");
    var msg = block.querySelector(".sample-msg");
    if (!form || !input || !button || !msg) return;

    var source = block.getAttribute("data-source") || location.pathname;

    function say(text, cls) {
      msg.textContent = text;
      msg.className = "sample-msg" + (cls ? " " + cls : "");
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();

      var email = (input.value || "").trim();
      if (!EMAIL_RE.test(email)) {
        say("Enter a valid email and the sample is on its way.", "err");
        input.focus();
        return;
      }

      button.disabled = true;
      say("Sending…");

      fetch("/api/lead", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email, source: source }),
      })
        .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, d: d }; }); })
        .then(function (r) {
          if (!r.ok || !r.d.ok) {
            say((r.d && r.d.error) || "We couldn't send that just now. Try again in a moment.", "err");
            button.disabled = false;
            return;
          }

          say("Sent — check your inbox for the sample CSV.", "ok");
          input.disabled = true;
          button.textContent = "Sample sent ✓";

          // A direct link too: the email can be slow, filtered, or mistyped, and a
          // visitor who never gets the file is a lead that converts to nothing.
          var direct = document.createElement("p");
          direct.className = "sample-fine";
          direct.innerHTML = 'Didn\'t arrive? <a href="/api/sample?format=csv">Download the sample CSV directly</a>.';
          form.parentNode.insertBefore(direct, msg.nextSibling);

          window.uemFireLead(source);
        })
        .catch(function () {
          say("We couldn't reach the server. Try again in a moment.", "err");
          button.disabled = false;
        });
    });
  }

  function boot() {
    var blocks = document.querySelectorAll("[data-sample-form]");
    for (var i = 0; i < blocks.length; i++) init(blocks[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
