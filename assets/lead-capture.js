/* US Energy Map — free-sample email capture.
   Drives every [data-sample-form] block on the page: posts the address to /api/lead,
   which emails the sample, then fires the Google Ads "lead" conversion.

   Included on /bess and the state pages. The conversion call goes through
   window.uemAdsConversion, which is queued in assets/analytics.js until
   /assets/ads.js lands — so a fast submit still counts. */
(function () {
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

  // A visitor who submits on one state page and then another is one lead, not two.
  // Google Ads would happily count both; the ad spend decision that follows would be
  // wrong. sessionStorage is enough — it is one browsing session, which is the window
  // that actually inflates a campaign's numbers.
  var FIRED_KEY = "uem_lead_fired";
  function alreadyFired() {
    try { return sessionStorage.getItem(FIRED_KEY) === "1"; } catch (e) { return false; }
  }
  function markFired() {
    try { sessionStorage.setItem(FIRED_KEY, "1"); } catch (e) { /* private mode */ }
  }

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

          if (!alreadyFired()) {
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
          }
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
