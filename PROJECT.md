# US Energy Map — Project Brief

> A public-facing tracker for current and proposed US data centers and battery
> energy storage (BESS) projects. Built for industry insiders, investors, and
> reporters who need a single visual answer to "where is the buildout happening?"

**Live:** https://usenergymap.com
**Repo:** https://github.com/umichsteve/usenergymap
**Status (May 2026):** v1 in production. Map + filters + ~1,400 projects.
**Owner:** Steve DeMaagd ([umichsteve](https://github.com/umichsteve))

---

## What the site does today

A single full-screen interactive map. Markers for every tracked project. A
sidebar with filters, summary stats, and a synced project list. Click any
marker (or list item) to see operator, capacity, status, and source.

Stack is intentionally minimal:

- **Frontend:** Single `index.html`, no build step. Leaflet.js over CARTO dark
  tiles. Vanilla JS, no framework. Mobile-responsive via CSS grid.
- **Data:** Flat `projects.json` file fetched on page load. Schema is
  Postgres-importable if the project ever graduates beyond static.
- **Hosting:** Vercel static, custom domain `usenergymap.com` with auto-TLS.
  `vercel.json` sets a 5-minute cache on `projects.json` so updates propagate
  fast.
- **Deploy:** `git push` to `main` → Vercel auto-deploys. No `vercel --prod`
  needed in the regular loop.

### Features in production

| Feature | Detail |
|---|---|
| Marker clustering | Leaflet.markercluster, grouped by zoom level |
| Cluster popup with list | Click cluster → scrollable list of projects + "Zoom to area" link |
| Filters | Type · Status · State · Operator · Min MW · Year range · free-text search |
| Sortable sidebar list | MW / year / name, capped at top 300 for perf |
| Year timeline | Dual "From / To" range slider |
| ISO/RTO overlay | Toggleable polygons for ERCOT, CAISO, PJM, MISO, SPP, NYISO, ISO-NE |
| Hash deep-links | `usenergymap.com#meta-hyperion` opens that project directly |
| Marker styling | Fill = type (DC blue / BESS green) · Ring = status · Size = √MW |

### Data sources

- **BESS:** Auto-ingested monthly from [EIA-860M](https://www.eia.gov/electricity/data/eia860m/),
  the federal preliminary monthly electric generator inventory. The
  `Monthly EIA-860M refresh` GitHub Action runs on the 26th of each month,
  pulls the latest XLSX, runs `scripts/ingest_eia.py`, and opens a PR if
  `projects.json` changed. Merge → auto-deploy.
  - Entries with id prefix `eia-` come from this pipeline.
  - Dedup against hand-curated entries by proximity (~10 km) or strong name
    match in the same state.
- **Data centers:** Hand-curated. No equivalent federal registry exists.
  Sources include operator press releases (AWS, Google, Microsoft, Meta,
  Apple), Data Center Knowledge, Black Ridge Research, and major colo
  portfolios (Equinix, Digital Realty, CyrusOne, CoreSite, NTT, DataBank,
  Stack, CoreWeave, Crusoe, Lambda, xAI).

### Current dataset stats

- **1,436 projects total**
- **109 data centers** (Meta 17, Equinix 12, Google 11, AWS 11, Microsoft 10, Apple 5…)
- **1,327 BESS** (96 hand-curated + 1,231 from EIA-860M)
- **157,735 MW** combined nameplate capacity
- **49 states** represented
- **Status mix:** 935 operating · 228 under-construction · 273 planned
- **Year range:** 2006–2028

---

## File layout

```
index.html                          # The whole app — map, filters, popups, styling, JS
projects.json                       # Dataset (schema_version 1.0)
vercel.json                         # Static deploy config + cache headers
README.md                           # Build / deploy / data instructions
PROJECT.md                          # This file — full project brief
scripts/ingest_eia.py               # Monthly EIA-860M BESS ingest
scripts/build_changelog.py          # Diff vs. last commit → changelog.json
changelog.json                      # Latest refresh summary + row-level changes
.github/workflows/refresh-eia.yml   # Cron + PR for automated refresh
api/                                # Vercel functions: checkout, download, session, portal
lib/                                # dataset.js (exports), entitlement.js, changelog.js
data.html · license.html · changelog.html · data/success.html
.cache/                             # EIA xlsx cache (gitignored)
```

## Data schema

```json
{
  "id": "eia-12345",                // unique; "eia-" prefix = auto-ingested
  "type": "data_center",            // "data_center" | "bess"
  "name": "Microsoft Quincy",
  "operator": "Microsoft",
  "city": "Quincy",
  "state": "WA",                    // 2-letter
  "lat": 47.2342,
  "lng": -119.8525,
  "capacity_mw": 470,               // power capacity (MW)
  "capacity_mwh": 1880,             // BESS only — energy capacity (MWh), optional
  "status": "operating",            // "operating" | "under_construction" | "planned" | "announced"
  "year_online": 2007,              // existing or target year
  "source": "https://..."           // primary public source
}
```

Coordinates: exact site for EIA entries; city/county centroid for hand-curated
when exact site isn't publicly disclosed.

---

## What's intentionally NOT in v1

- No CMS or admin UI. JSON edits are commits.
- No login, no per-user state. (Monetization added Sept 2026 — see “Paid tiers” below; still no accounts or DB.)
- No backend, no database. Static + JSON only.
- No data center automated ingest (no federal registry exists; commercial
  data would be the only path).
- No detailed project pages — popups carry all the info today.
- No charts / analytics / time-series views (the data is point-in-time per project).

---

## Paid tiers (Sept 2026)

The map stays free. `/data` sells the *service* around the dataset, not the records
(BESS source is public-domain EIA-860M; the data center layer is our own curation).

| Tier | Price | Checkout | License |
|---|---|---|---|
| Snapshot | $199 one-time | Stripe (`plan=snapshot`) | Single-user |
| Always Current | $49 / month | Stripe subscription (`plan=current`) | Single-user + change log |
| Region / Sector Feed | $3,000 / yr | Contact → invoice | Commercial, 1 product |
| Full Feed | $7,500 / yr | Contact → invoice | Commercial, all products |
| Embedded & API | from $12,000 / yr | Contact → order form | Commercial + SLA |

Mechanics (no database, Stripe is the source of truth):
- `api/create-checkout.js` — plan-aware Checkout Session; both tiers land on `/data/success?session_id=…`.
- `lib/entitlement.js` — verifies a session: one-time = `payment_status: paid`; subscription = status active/trialing/past_due.
- `api/download.js` — streams xlsx/csv/geojson for any entitled session; `format=changelog` for subscribers only.
- `api/session.js` / `api/portal.js` — success-page status and Stripe billing portal (enable the portal once in the Stripe Dashboard).
- The success-page URL is the subscriber's permanent access link. Lost-link recovery is manual (email) for now.

Change log:
- `scripts/build_changelog.py` diffs the working-tree `projects.json` against `HEAD` (order-independent) → `changelog.json`
  with `summary`, `highlights`, `history` (public) and row-level `changes` (gated CSV).
- Wired into `.github/workflows/refresh-eia.yml`; the PR body carries the summary line.
- The same workflow re-runs `scripts/build_pages.py`, so state/operator pages, the explore hub and sitemap never drift from the data. Orphaned operator pages are pruned and 301'd to /explore via `vercel.json` redirects. Each page's CTA shows that page's own change count from the latest refresh.
- `/changelog` renders the public summary client-side; `/license` holds the tiered terms.

## Open questions for next phase

1. **Information architecture.** Is a single full-screen map the right home
   page, or should there be a landing page → click-through to the map?
   Methodology / About / Press / Contact pages?
2. **Brand identity.** No logo today, just a ⚡ emoji in the header. Need a
   real mark + favicon + social-share image.
3. **Operator portfolio views.** Currently you can filter to operator, but
   no "/operators/meta" page that aggregates their portfolio cleanly.
4. **Capacity timeline chart.** A "GW online over time" stacked-area chart
   could anchor the landing page narrative.
5. **Methodology page.** Clarify: what does "operating" mean for a multi-phase
   campus? How is capacity defined for AI training clusters? How fresh is the
   EIA data?
6. **Data center expansion.** 109 DC entries cover most named hyperscale
   campuses but miss hundreds of mid-tier colos. Worth the curation cost?
7. **Mobile UX.** Current mobile layout (split top/bottom) works but isn't
   thoughtfully designed. Could be much better.

---

## Future direction (rough roadmap)

- **v1.x — Brand & IA pass:** Logo, refreshed visual identity, multi-page
  structure (Home / Map / Methodology / About).
- **v2 — Editorial layer:** Capacity-online-over-time chart on landing.
  "Project of the week" callout. Operator portfolio pages.
- **v2.x — Data quality:** Better dedup between hand-curated + EIA. Add
  energy duration (MWh / MW) calculated metric for BESS. Power-plant level
  vs. campus-level distinction for hyperscale.
- **v3 — Beyond US:** Add Canada (CER data) and Mexico if it makes sense.

## Design constraints (don't violate)

- **Stay static.** No Next.js, no backend, no DB unless there's a real reason
  (multi-user CRUD, auth-gated content). The site loads fast and is free to
  host because it's static. Don't lose that.
- **Don't gate the map behind a paywall.** Editorial / commentary monetization
  is fine; the core visualization stays free and unobstructed.
- **Don't fabricate sources.** Every project needs a real `source` URL.
- **Don't claim EIA data is exhaustive.** It captures utility-scale generators
  ≥1 MW reporting to FERC; behind-the-meter and small commercial BESS aren't
  in there.
