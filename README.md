# US Data Center & BESS Project Tracker

**Live:** https://usenergymap.com
**Repo:** https://github.com/umichsteve/usenergymap (push to `main` → Vercel auto-deploys)

Interactive map of operating, under-construction, and planned data centers and
battery energy storage system (BESS) projects in the United States.

Static site — no build step. Single `index.html`, single `projects.json`. Map
rendered with [Leaflet](https://leafletjs.com/) over OpenStreetMap (via CARTO dark tiles).

## Project structure

```
index.html                          # The whole app: map, filters, popups, styling
projects.json                       # Project dataset (schema_version 1.0)
vercel.json                         # Static deploy config + cache headers
scripts/ingest_eia.py               # Monthly EIA-860M ingest for BESS entries
.github/workflows/refresh-eia.yml   # Cron + PR for automated refresh
README.md                           # This file
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

**BESS entries are auto-ingested monthly from EIA-860M** (the federal
preliminary monthly electric generator inventory). Entries with id prefix
`eia-` come from that pipeline; entries with any other prefix are
hand-curated.

**Data centers** are hand-curated from operator press releases, Data Center
Knowledge, and Black Ridge Research. There is no equivalent federal registry
for data centers.

Coordinates from EIA are exact site coordinates; hand-curated entries use
city/county centroid where the exact site isn't publicly disclosed.

### EIA refresh — automatic

The `Monthly EIA-860M refresh` GitHub Action runs on the 26th of each month,
re-pulls the latest EIA-860M XLSX, runs `scripts/ingest_eia.py`, and opens a
PR titled "Monthly EIA-860M refresh" if `projects.json` has changed. Merge
the PR → Vercel auto-deploys.

### EIA refresh — manual

```bash
python3 -m pip install pandas openpyxl
python3 scripts/ingest_eia.py
```

Optional `EIA_MONTH=december_generator2025` env override pins a specific
archive instead of "latest".

The script:
- Downloads the requested EIA-860M archive into `.cache/`
- Filters `Technology == "Batteries"` from both `Operating` and `Planned` sheets
- Aggregates generator rows up to plant level (multiple generators per plant are common)
- De-duplicates against hand-curated entries by proximity (~10 km) or strong name match
- Preserves all non-`eia-` entries verbatim
- Writes `projects.json` with the merged set

## Roadmap ideas

- Operator portfolio pages (`#operator=Meta` view)
- Map clustering by ISO/RTO instead of geographic proximity
- Energy duration (MWh / MW) calculated column for BESS
- Migrate to Supabase + admin form for CRUD (if/when dataset becomes user-editable)
- Per-project detail pages (deeper than current popup)
- Data center automated ingest (no equivalent federal registry exists — would require commercial data source)
