# US Data Center & BESS Project Tracker

**Live:** https://usenergymap.com
**Repo:** https://github.com/umichsteve/usenergymap (push to `main` → Vercel auto-deploys)

Interactive map of operating, under-construction, and planned data centers and
battery energy storage system (BESS) projects in the United States.

Static site — no build step. Single `index.html`, single `projects.json`. Map
rendered with [Leaflet](https://leafletjs.com/) over OpenStreetMap (via CARTO dark tiles).

## Project structure

```
index.html       # The whole app: map, filters, popups, styling
projects.json    # Project dataset (schema_version 1.0)
vercel.json      # Static deploy config + cache headers for projects.json
README.md        # This file
```

## Adding or editing a project

Edit `projects.json`. Each entry follows this schema:

```json
{
  "id": "dc-msft-quincy",
  "type": "data_center",        // "data_center" | "bess"
  "name": "Microsoft Quincy",
  "operator": "Microsoft",
  "city": "Quincy",
  "state": "WA",                // 2-letter state code
  "lat": 47.2342,
  "lng": -119.8525,
  "capacity_mw": 470,           // power capacity in MW
  "capacity_mwh": null,         // (BESS only) energy capacity in MWh
  "status": "operating",        // "operating" | "under_construction" | "planned" | "announced"
  "year_online": 2007,          // existing or target year
  "source": "https://..."       // URL to public source
}
```

The schema is intentionally flat so it can be loaded straight into a Postgres
table later (one row per object) if/when this graduates to Supabase.

## Run locally

Any static file server works. Example:

```bash
cd "Data Center / BESS  Tracking Website"
python3 -m http.server 8080
# open http://localhost:8080
```

Opening `index.html` via `file://` will fail because `fetch()` needs HTTP.

## Deploy

Pushing to `main` on GitHub auto-deploys via the Vercel project's git binding:

```bash
git add -A && git commit -m "your message" && git push
```

Manual deploys (rarely needed, e.g. to redeploy without code changes):

```bash
vercel --prod
```

Vercel treats this as a static site automatically (no framework preset needed).
`vercel.json` sets a 5-minute cache on `projects.json` so updates propagate quickly.

Production domain `usenergymap.com` is registered through Vercel and attached
to the `bess-tracking-website` project. Apex serves the site, `www` 301-redirects
to apex. TLS is auto-provisioned.

## Data sources

Initial dataset (May 2026) compiled from public reporting. Coordinates are
approximate to city/county centroid when exact site address is not publicly
disclosed. See `source` field on each project for primary reference.

Notable references:
- EIA Today in Energy (battery storage capacity forecasts)
- Black Ridge Research (upcoming data center / BESS lists)
- Data Center Knowledge (new developments tracking)
- Cleanview.co (operating battery storage map)

## Roadmap ideas

- Cluster popup showing list of projects in the cluster
- Sort/list view in sidebar (scrollable project list synced with map filters)
- Timeline slider (filter by year_online)
- Migrate to Supabase + admin form for CRUD
- Per-project detail pages (hash-routed)
- ISO/RTO overlay (PJM, ERCOT, CAISO boundaries)
