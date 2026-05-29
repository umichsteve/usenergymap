/* US Energy Map — Google Analytics 4 loader.
   Paste your Measurement ID below (looks like G-XXXXXXXXXX) and that's it —
   this script is already included on every page. Until a real ID is set,
   it does nothing, so it's safe to ship. */
(function () {
  var GA_ID = "G-HC5D8KG8CB"; // GA4 Measurement ID for usenergymap.com

  if (!GA_ID || GA_ID.indexOf("G-") !== 0 || GA_ID === "G-XXXXXXXXXX") return;

  var s = document.createElement("script");
  s.async = true;
  s.src = "https://www.googletagmanager.com/gtag/js?id=" + GA_ID;
  document.head.appendChild(s);

  window.dataLayer = window.dataLayer || [];
  function gtag() { dataLayer.push(arguments); }
  window.gtag = gtag;
  gtag("js", new Date());
  gtag("config", GA_ID);
})();
