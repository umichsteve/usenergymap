// Geospatial helpers for the proximity endpoint. Pure functions, no dependencies.

const R_MI = 3958.7613; // Earth radius, statute miles

function toRad(d) { return (d * Math.PI) / 180; }

// Great-circle distance in miles between two lat/lng points.
function haversineMiles(lat1, lng1, lat2, lng2) {
  const dLat = toRad(lat2 - lat1), dLng = toRad(lng2 - lng1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * R_MI * Math.asin(Math.sqrt(a));
}

// Initial compass bearing from point 1 to point 2, as a 16-point label.
function bearingLabel(lat1, lng1, lat2, lng2) {
  const y = Math.sin(toRad(lng2 - lng1)) * Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) - Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lng2 - lng1));
  const deg = ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
  const pts = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
  return pts[Math.round(deg / 22.5) % 16];
}

/**
 * Nearest projects to a point.
 * @param projects  array of dataset rows with lat/lng
 * @param lat,lng   query point
 * @param opts      { limit=5, radiusMiles=null, type=null ('bess'|'data_center'), status=null (csv), minMw=null }
 * @returns { results: [...], count_within_radius }
 */
function nearest(projects, lat, lng, opts = {}) {
  const limit = Math.min(Math.max(parseInt(opts.limit, 10) || 5, 1), 50);
  const radius = opts.radiusMiles != null && opts.radiusMiles !== "" ? Number(opts.radiusMiles) : null;
  const type = opts.type && opts.type !== "all" ? String(opts.type) : null;
  const statuses = opts.status ? new Set(String(opts.status).split(",").map(s => s.trim()).filter(Boolean)) : null;
  const minMw = opts.minMw != null && opts.minMw !== "" ? Number(opts.minMw) : null;

  const scored = [];
  for (const p of projects) {
    if (typeof p.lat !== "number" || typeof p.lng !== "number") continue;
    if (type && p.type !== type) continue;
    if (statuses && !statuses.has(p.status)) continue;
    if (minMw != null && !((p.capacity_mw || 0) >= minMw)) continue;
    // Cheap bounding-box reject before the trig: 1° lat ≈ 69 mi.
    if (radius != null && Math.abs(p.lat - lat) * 69 > radius) continue;
    const d = haversineMiles(lat, lng, p.lat, p.lng);
    if (radius != null && d > radius) continue;
    scored.push({ p, d });
  }
  scored.sort((a, b) => a.d - b.d);
  const results = scored.slice(0, limit).map(({ p, d }) => ({
    id: p.id, name: p.name, type: p.type, operator: p.operator || null,
    city: p.city || null, state: p.state, latitude: p.lat, longitude: p.lng,
    capacity_mw: p.capacity_mw ?? null, capacity_mwh: p.capacity_mwh ?? null,
    status: p.status, year_online: p.year_online ?? null,
    distance_miles: +d.toFixed(2), distance_km: +(d * 1.609344).toFixed(2),
    bearing: bearingLabel(lat, lng, p.lat, p.lng),
    source: p.source || null,
  }));
  return { results, count_within_radius: radius != null ? scored.length : null };
}

module.exports = { haversineMiles, bearingLabel, nearest };
