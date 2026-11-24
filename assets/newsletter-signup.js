/* US Energy Map — newsletter double opt-in capture.
   Drives every [data-newsletter-form] block on the page: posts to the
   grand-roma-subscribe Worker, which mints a row in grand-roma-list and
   (when SENDS_ENABLED=true and the brand's Resend key is set) emails a
   confirm link. GA4 generate_lead fires on success; Ads conversion is left
   to lead-capture.js — a newsletter signup is not a paid-sample lead.
*/
(function () {
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  // The URL is attribute-overridable so a preview build can point at a
  // staging worker without editing JS. Default is production.
  var DEFAULT_ENDPOINT = "https://grand-roma-subscribe.grandromallc.workers.dev/subscribe";

  function init(block) {
    var form = block.querySelector("form");
    var input = block.querySelector('input[type="email"]');
    var button = block.querySelector("button");
    var msg = block.querySelector(".newsletter-msg");
    if (!form || !input || !button || !msg) return;

    var source = block.getAttribute("data-source") || location.pathname;
    var endpoint = block.getAttribute("data-endpoint") || DEFAULT_ENDPOINT;

    function say(text, cls) {
      msg.textContent = text;
      msg.className = "newsletter-msg" + (cls ? " " + cls : "");
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = (input.value || "").trim();
      if (!EMAIL_RE.test(email)) {
        say("Enter a valid email address.", "err");
        input.focus();
        return;
      }
      button.disabled = true;
      say("Sending…");

      fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email, site: "usenergymap", source: source }),
      })
        .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, d: d }; }); })
        .then(function (r) {
          if (!r.ok || !r.d.ok) {
            say((r.d && r.d.error) || "We couldn't sign you up just now. Try again in a moment.", "err");
            button.disabled = false;
            return;
          }
          if (r.d.status === "already") {
            say("You're already on the list.", "ok");
          } else {
            say("Check your inbox to confirm your subscription.", "ok");
          }
          input.disabled = true;
          button.textContent = "Signed up ✓";
          if (typeof window.gtag === "function") {
            window.gtag("event", "generate_lead", { source: source, method: "newsletter" });
          }
        })
        .catch(function () {
          say("We couldn't reach the server. Try again in a moment.", "err");
          button.disabled = false;
        });
    });
  }

  function boot() {
    var blocks = document.querySelectorAll("[data-newsletter-form]");
    for (var i = 0; i < blocks.length; i++) init(blocks[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
