# Setup runbook — Cloudflare email forwarding + Google Analytics

Two independent tasks. Do them in either order. The email one moves DNS, so
read the rollback step first.

---

## A. Email forwarding (Cloudflare Email Routing)

**Goal:** `hello@`, `info@`, `contact@usenergymap.com` → forward to `grandromallc@gmail.com`,
matching how your other domains (curatedwalks, velar, weeklysilicon, etc.) are set up.

**Zone:** `usenergymap.com`
**Registered at:** Vercel · **DNS currently at:** Vercel (`ns1/ns2.vercel-dns.com`)

### Records — before (Vercel, today)

| Type | Name | Value |
|---|---|---|
| A | @ | 216.150.1.1, 216.150.1.129 |
| A | www | 216.150.1.1, 216.150.16.1 |
| MX / TXT | — | none |

### Steps

1. **Add the site to Cloudflare.** dash.cloudflare.com → *Add a site* → `usenergymap.com` → Free plan. Cloudflare scans and imports the existing A records above.
2. **Check the imported records**, and make sure the apex `A` and `www` records are **DNS only (grey cloud, NOT proxied/orange).** Vercel terminates TLS; proxying through Cloudflare will break the site. This is the one easy-to-miss step.
   - If `www` didn't import cleanly, set: `CNAME  www → cname.vercel-dns.com` (DNS only).
3. **Copy the two Cloudflare nameservers** Cloudflare assigns you (e.g. `xxx.ns.cloudflare.com`).
4. **At Vercel** (the registrar) → Domains → `usenergymap.com` → Nameservers → switch to **custom / external** and enter the two Cloudflare nameservers. Save. *(This is the cutover; propagation is usually minutes, up to a few hours.)*
   - ⚠️ If Vercel won't let you set external nameservers on a Vercel-registered domain, stop here and use the fallback at the bottom — same email result, no migration.
5. Wait until Cloudflare shows the zone **Active**. Confirm the site still loads at https://usenergymap.com.
6. **Cloudflare → Email → Email Routing → Get started.** This auto-adds the required MX + SPF records.
7. **Add destination address:** `grandromallc@gmail.com`. Cloudflare emails it a verification link — click it.
8. **Add routes (custom addresses):**
   - `hello@usenergymap.com` → `grandromallc@gmail.com`
   - `info@usenergymap.com` → `grandromallc@gmail.com`
   - `contact@usenergymap.com` → `grandromallc@gmail.com`

### Verify

```bash
dig +short NS usenergymap.com      # → *.ns.cloudflare.com
dig +short MX usenergymap.com      # → route1/2/3.mx.cloudflare.net
```
Then send a test email to `hello@usenergymap.com` and confirm it lands in grandromallc@gmail.com.

### Rollback

At Vercel, set the nameservers back to `ns1.vercel-dns.com` / `ns2.vercel-dns.com`.
DNS reverts to the pre-migration state; email routing simply stops.

### Fallback (no migration — if step 4 is blocked)

Keep DNS on Vercel and add these records in the Vercel DNS panel, then create the
aliases at improvmx.com (free) pointing to grandromallc@gmail.com:

| Type | Name | Value | Priority |
|---|---|---|---|
| MX | @ | mx1.improvmx.com | 10 |
| MX | @ | mx2.improvmx.com | 20 |
| TXT | @ | `v=spf1 include:spf.improvmx.com ~all` | — |

---

## B. Google Analytics (GA4)

The site is already wired — every page loads `/assets/analytics.js`, which stays
dormant until you drop in a real Measurement ID. You just need to create the
property and paste one value.

### Create the property

1. analytics.google.com → **Admin** (gear, bottom-left) → **Create** → **Property**.
2. Name it `US Energy Map`, set time zone + currency → **Next** → fill business details → **Create**.
3. In the new property → **Data Streams** → **Web** → URL `https://usenergymap.com`, stream name `US Energy Map` → **Create stream**.
4. Copy the **Measurement ID** (format `G-XXXXXXXXXX`).

### Activate it

Open `assets/analytics.js`, replace the placeholder on line 6:

```js
var GA_ID = "G-XXXXXXXXXX";   // <-- paste your real ID here
```

Commit + push. Within a minute, GA4 Realtime should show your own visit. Done —
analytics now run on all pages (landing, map, methodology, about, privacy).
