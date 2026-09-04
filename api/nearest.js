// GET /api/nearest?lat=..&lng=..[&address=..][&radius_miles=..][&type=bess|data_center][&status=operating,under_construction][&min_mw=..][&limit=..]
//
// Proximity lookup: the nearest tracked BESS / data center sites to a point, with distance and
// bearing. This is the Embedded-tier product (per-address lookups for disclosure reports,
// underwriting, siting). Static-compatible: reads the public projects.json, no database.
//
// Access:
//   - No key: demo mode. Works, but limited to 3 results and no radius counts, so the free
//     map audience can try it while paying customers get the full response.
//   - x-api-key header (or ?key=) matching one of NEAREST_API_KEYS (comma-separated env var):
//     full results (limit ≤ 50), radius counts, CORS for the customer's origin.
//
// Geocoding: if `address` is given without lat/lng we geocode it — US Census Bureau geocoder for
// street addresses (public, free, no key, no redistribution terms — unlike Google/Mapbox), with
// OpenStreetMap Nominatim as the fallback for place-level queries (city, county, ZIP).

const { fetchProjects } = require("../lib/dataset");
const { nearest } = require("../lib/geo");

const DEMO_LIMIT = 3;
let cache = { at: 0, projects: null };
const CACHE_MS = 5 * 60 * 1000;

async function loadProjects(origin) {
  if (cache.projects && Date.now() - cache.at < CACHE_MS) return cache.projects;
  const { projects } = await fetchProjects(origin);
  cache = { at: Date.now(), projects };
  return projects;
}

// Census handles street addresses only; place-level queries ("Moss Landing, CA", a ZIP, a county)
// fall back to OpenStreetMap's Nominatim. We geocode one query point and return its coordinates —
// no OSM data is stored or redistributed, so ODbL isn't engaged; usage stays within Nominatim's
// policy (identified user-agent, low volume, attribution in every response).
const UA = "usenergymap.com nearest-api (hello@usenergymap.com)";

async function geocodeCensus(address) {
  const url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?benchmark=Public_AR_Current&format=json&address=" + encodeURIComponent(address);
  const res = await fetch(url, { headers: { "user-agent": UA } });
  if (!res.ok) return null;
  const j = await res.json();
  const m = j && j.result && j.result.addressMatches && j.result.addressMatches[0];
  if (!m) return null;
  return { lat: m.coordinates.y, lng: m.coordinates.x, matched_address: m.matchedAddress, geocoder: "census" };
}

async function geocodeNominatim(q) {
  const url = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=us&q=" + encodeURIComponent(q);
  const res = await fetch(url, { headers: { "user-agent": UA, "accept-language": "en" } });
  if (!res.ok) return null;
  const j = await res.json();
  const m = Array.isArray(j) && j[0];
  if (!m) return null;
  return { lat: Number(m.lat), lng: Number(m.lon), matched_address: m.display_name, geocoder: "nominatim" };
}

async function geocode(address) {
  // Street addresses (start with a number) → Census first; anything else → Nominatim first.
  const streetish = /^\s*\d+\s+\S/.test(address);
  const order = streetish ? [geocodeCensus, geocodeNominatim] : [geocodeNominatim, geocodeCensus];
  for (const fn of order) {
    try { const r = await fn(address); if (r) return r; } catch (e) { console.warn("geocoder failed:", fn.name, e.message); }
  }
  return null;
}

function isAuthorized(req) {
  const keys = (process.env.NEAREST_API_KEYS || "").split(",").map(s => s.trim()).filter(Boolean);
  if (!keys.length) return false;
  const supplied = req.headers["x-api-key"] || (req.query && req.query.key);
  return !!supplied && keys.includes(String(supplied));
}

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "x-api-key, content-type");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") { res.setHeader("Allow", "GET"); return res.status(405).json({ error: "Method not allowed" }); }

  const q = req.query || {};
  const authorized = isAuthorized(req);
  let lat = q.lat != null && q.lat !== "" ? Number(q.lat) : null;
  let lng = q.lng != null && q.lng !== "" ? Number(q.lng) : null;
  let geocoded = null;

  try {
    if ((lat == null || lng == null) && q.address) {
      geocoded = await geocode(String(q.address).slice(0, 300));
      if (!geocoded) return res.status(404).json({ error: "Could not geocode that query. Street addresses with city, state and ZIP resolve reliably; place names are best-effort. Or pass lat/lng." });
      lat = geocoded.lat; lng = geocoded.lng;
    }
    if (!(Number.isFinite(lat) && Number.isFinite(lng)) || Math.abs(lat) > 90 || Math.abs(lng) > 180) {
      return res.status(400).json({ error: "Provide lat and lng (decimal degrees) or a US street address." });
    }

    const proto = (req.headers["x-forwarded-proto"] || "https").split(",")[0];
    const host = req.headers["x-forwarded-host"] || req.headers.host || "usenergymap.com";
    const projects = await loadProjects(`${proto}://${host}`);

    const opts = {
      limit: authorized ? q.limit : Math.min(parseInt(q.limit, 10) || DEMO_LIMIT, DEMO_LIMIT),
      radiusMiles: authorized ? q.radius_miles : null,
      type: q.type, status: q.status, minMw: q.min_mw,
    };
    const out = nearest(projects, lat, lng, opts);

    res.setHeader("Cache-Control", authorized ? "no-store" : "public, max-age=300");
    return res.status(200).json({
      query: { lat, lng, ...(geocoded ? { address: q.address, matched_address: geocoded.matched_address, geocoder: geocoded.geocoder } : {}),
               type: q.type || "all", status: q.status || "all", radius_miles: authorized && q.radius_miles ? Number(q.radius_miles) : null },
      tier: authorized ? "licensed" : "demo",
      ...(authorized ? {} : { note: `Demo response: ${DEMO_LIMIT} results, no radius counts. Licensed access: usenergymap.com/data#embedded` }),
      count_within_radius: out.count_within_radius,
      results: out.results,
      attribution: "US Energy Map (usenergymap.com) · BESS from EIA-860M · data centers hand-curated",
    });
  } catch (err) {
    console.error("nearest error:", err);
    return res.status(502).json({ error: "Lookup failed. Try again in a moment." });
  }
};
