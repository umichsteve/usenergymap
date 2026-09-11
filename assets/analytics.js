/* US Energy Map — Google tag loader.
   Loads GA4 (measurement ID below) and the Google Ads tag. The Ads half lives at
   /assets/ads.js, which is served by api/ads.js from environment variables — the tag
   ID is never committed. This file is already included on every page, which is why the
   Ads loader is appended from here rather than added as a second <script> to ~155 files. */
(function () {
  var GA_ID = "G-HC5D8KG8CB"; // GA4 Measurement ID for usenergymap.com

  if (GA_ID && GA_ID.indexOf("G-") === 0 && GA_ID !== "G-XXXXXXXXXX") {
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + GA_ID;
    document.head.appendChild(s);

    window.dataLayer = window.dataLayer || [];
    function gtag() { dataLayer.push(arguments); }
    window.gtag = gtag;
    gtag("js", new Date());
    gtag("config", GA_ID);
  }

  // ---- Google Ads ----
  // /assets/ads.js is async, so a conversion can be requested before it lands (a fast
  // form submit, or a success page that verifies in one round trip). Queue those calls;
  // ads.js replays the queue as its last step. Without this stub an early conversion is
  // a TypeError and the hit is lost — which is the failure mode you never see in
  // reporting, because the number is simply lower than it should be.
  window.uemAdsQueue = window.uemAdsQueue || [];
  window.uemAdsConversion = function () { window.uemAdsQueue.push(arguments); return null; };

  var a = document.createElement("script");
  a.async = true;
  a.src = "/assets/ads.js";
  document.head.appendChild(a);
})();
