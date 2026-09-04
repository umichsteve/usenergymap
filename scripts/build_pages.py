#!/usr/bin/env python3
"""
Generate programmatic SEO pages for US Energy Map from projects.json.

Outputs (at repo root):
  state/<slug>.html       — one page per state  (served at /state/<slug>)
  operator/<slug>.html    — one page per operator with >= MIN_OPERATOR projects
  explore.html            — hub linking every state + operator page
  sitemap.xml             — all canonical URLs
  robots.txt              — points crawlers at the sitemap

Re-run after each data refresh:  python3 scripts/build_pages.py
Static + safe: pages reuse /assets/site.css and the shared nav/footer.
"""

import json, os, re, html, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://usenergymap.com"
MIN_OPERATOR = 3  # only build operator pages for portfolios this size or larger

STATE_NAMES = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California",
    "CO":"Colorado","CT":"Connecticut","DE":"Delaware","FL":"Florida","GA":"Georgia",
    "HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas",
    "KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland","MA":"Massachusetts",
    "MI":"Michigan","MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana",
    "NE":"Nebraska","NV":"Nevada","NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico",
    "NY":"New York","NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma",
    "OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina",
    "SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont",
    "VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming",
    "DC":"District of Columbia","PR":"Puerto Rico",
}

def esc(s): return html.escape(str(s if s is not None else ""))
def kebab(s):
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)
def fmt(n): return f"{int(round(n)):,}"

def status_label(s):
    return {"operating":"Operating","under_construction":"Under construction",
            "planned":"Planned","announced":"Announced"}.get(s, s)

def head(title, desc, canonical):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}" />
<link rel="icon" type="image/svg+xml" href="/assets/mark-square.svg" />
<link rel="canonical" href="{canonical}" />
<meta property="og:type" content="website" />
<meta property="og:site_name" content="US Energy Map" />
<meta property="og:title" content="{esc(title)}" />
<meta property="og:description" content="{esc(desc)}" />
<meta property="og:url" content="{canonical}" />
<meta property="og:image" content="{SITE}/assets/og.png" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:image" content="{SITE}/assets/og.png" />
<link rel="stylesheet" href="/assets/site.css" />
<script src="/assets/analytics.js"></script>
</head>
<body>
<nav class="nav">
  <div class="nav-inner">
    <a class="brand" href="/"><img src="/assets/mark-square.svg" alt="" /> US Energy Map</a>
    <div class="nav-links">
      <a href="/map">Map</a>
      <a href="/explore">Explore</a>
      <a href="/changelog">What changed</a>
      <a href="/data">Data</a>
      <a href="/methodology">Methodology</a>
      <a class="cta" href="/map">Open the map →</a>
    </div>
  </div>
</nav>
"""

FOOTER = """<footer class="footer">
  <div class="footer-inner">
    <div>
      <a class="brand" href="/"><img src="/assets/mark-square.svg" alt="" /> US Energy Map</a>
      <p class="tag">An independent, public tracker of the data center and battery storage buildout across North America.</p>
    </div>
    <div class="footer-cols">
      <div class="footer-col">
        <h4>Explore</h4>
        <a href="/map">Interactive map</a>
        <a href="/explore">By state &amp; operator</a>
        <a href="/changelog">What changed</a>
        <a href="/data">Data &amp; pricing</a>
        <a href="/methodology">Methodology</a>
        <a href="/license">License</a>
        <a href="/guides/home-backup-power">Home backup power guide</a>
        <a href="/about">About</a>
        <a href="/privacy">Privacy</a>
      </div>
      <div class="footer-col">
        <h4>Get involved</h4>
        <a href="mailto:hello@usenergymap.com?subject=Sponsoring%20US%20Energy%20Map">Sponsor this map</a>
        <a href="mailto:hello@usenergymap.com?subject=Suggest%20a%20project%20%E2%80%94%20US%20Energy%20Map">Suggest a project</a>
        <a href="https://www.eia.gov/electricity/data/eia860m/" target="_blank" rel="noopener">Data: EIA-860M</a>
      </div>
    </div>
  </div>
  <div class="footer-bottom">
    <span>&copy; <span id="year">2026</span> US Energy Map. Data for informational purposes only.</span>
    <span>Not affiliated with any operator or government agency.</span>
  </div>
</footer>
<script>document.getElementById('year').textContent = new Date().getFullYear();</script>
</body>
</html>"""

DATA_CTA = """
  <div class="callout" style="border-left-color:#16a34a;display:flex;justify-content:space-between;align-items:center;gap:18px;flex-wrap:wrap;margin:26px 0;">
    <div style="flex:1;min-width:240px;"><strong>Working this market?</strong> {changed_line}Get every operator below as a ranked <strong>BD hunting list</strong> (Excel + CSV + GeoJSON) — <strong>$199</strong> once, or <strong>$49/mo</strong> refreshed monthly with a row-level change log.</div>
    <a class="btn btn-primary" href="/data" style="white-space:nowrap;">See data &amp; pricing &rarr;</a>
  </div>
"""

_CHANGELOG = None
def _changelog():
    """Latest changelog.json (built by scripts/build_changelog.py), or None."""
    global _CHANGELOG
    if _CHANGELOG is None:
        path = os.path.join(ROOT, "changelog.json")
        try:
            with open(path) as f:
                _CHANGELOG = json.load(f)
        except Exception:
            _CHANGELOG = {}
    return _CHANGELOG or None

def data_cta(projects):
    """CTA with a live 'what changed here this month' line when the changelog has rows for these projects."""
    log = _changelog()
    changed_line = ""
    if log and log.get("changes"):
        ids = {p["id"] for p in projects}
        hits = [c for c in log["changes"] if c.get("id") in ids]
        if hits:
            added = sum(1 for c in hits if c["kind"] == "added")
            status = sum(1 for c in hits if c["kind"] == "changed" and any(f["field"] == "status" for f in c.get("fields", [])))
            parts = []
            if added: parts.append(f"{added} added")
            if status: parts.append(f"{status} status change{'s' if status != 1 else ''}")
            if not parts: parts.append(f"{len(hits)} record{'s' if len(hits) != 1 else ''} updated")
            changed_line = f'<a href="/changelog">{", ".join(parts)} here in the latest refresh</a>. '
    return DATA_CTA.format(changed_line=changed_line)

def stat_cards(projects):
    n = len(projects)
    mw = sum(p.get("capacity_mw") or 0 for p in projects)
    dc = sum(1 for p in projects if p["type"] == "data_center")
    bess = n - dc
    op = sum(1 for p in projects if p["status"] == "operating")
    return f"""    <div class="statbar" style="margin:0 0 32px;">
      <div class="stat"><div class="num dc">{fmt(n)}</div><div class="lbl">projects</div></div>
      <div class="stat"><div class="num mw">{fmt(mw)}</div><div class="lbl">MW capacity</div></div>
      <div class="stat"><div class="num bess">{fmt(dc)} / {fmt(bess)}</div><div class="lbl">data centers / BESS</div></div>
      <div class="stat"><div class="num st">{fmt(op)}</div><div class="lbl">operating</div></div>
    </div>"""

def project_table(projects, show_state=True, show_operator=True):
    rows = []
    for p in sorted(projects, key=lambda x: (x.get("capacity_mw") or 0), reverse=True):
        loc = f'{esc(p.get("city",""))}, {esc(p.get("state",""))}' if show_state else esc(p.get("city",""))
        typ = "Data center" if p["type"] == "data_center" else "BESS"
        mw = fmt(p["capacity_mw"]) + " MW" if p.get("capacity_mw") else "—"
        badge = {"operating":"op","under_construction":"uc","planned":"pl","announced":"pl"}.get(p["status"],"pl")
        src = f'<a href="{esc(p["source"])}" target="_blank" rel="noopener">source ↗</a>' if p.get("source") else ""
        opcell = f"<td>{esc(p.get('operator','—'))}</td>" if show_operator else ""
        rows.append(
            f'<tr><td><strong>{esc(p["name"])}</strong></td>{opcell}<td>{typ}</td>'
            f'<td>{loc}</td><td>{mw}</td>'
            f'<td><span class="badge {badge}">{status_label(p["status"])}</span></td>'
            f'<td>{p.get("year_online","—")}</td><td>{src}</td></tr>'
        )
    ophdr = "<th>Operator</th>" if show_operator else ""
    return (
        '<table class="deftable"><thead><tr>'
        f'<th>Project</th>{ophdr}<th>Type</th><th>Location</th><th>Capacity</th>'
        '<th>Status</th><th>Year</th><th>Source</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table>"
    )

def write(path, content):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)

def main():
    data = json.load(open(os.path.join(ROOT, "projects.json")))
    projects = data.get("projects", [])
    urls = ["/", "/map", "/explore", "/changelog", "/data", "/license", "/methodology", "/about", "/guides/home-backup-power"]

    # ---- State pages ----
    by_state = {}
    for p in projects:
        by_state.setdefault(p["state"], []).append(p)

    state_links = []
    for code, plist in sorted(by_state.items(), key=lambda kv: -len(kv[1])):
        name = STATE_NAMES.get(code, code)
        slug = kebab(name)
        n = len(plist); mw = sum(x.get("capacity_mw") or 0 for x in plist)
        dc = sum(1 for x in plist if x["type"] == "data_center")
        title = f"Data Centers & Battery Storage in {name} — US Energy Map"
        desc = (f"{fmt(n)} tracked data center and battery storage (BESS) projects in {name} "
                f"totaling {fmt(mw)} MW. Explore operators, capacity, and status on US Energy Map.")
        canonical = f"{SITE}/state/{slug}"
        body = head(title, desc, canonical) + f"""
<section><div class="wrap"><article class="prose" style="max-width:980px;">
  <p class="kicker">State profile</p>
  <h1>Data centers &amp; battery storage in {esc(name)}</h1>
  <p class="sub">{fmt(n)} tracked projects · {fmt(mw)} MW · {fmt(dc)} data centers, {fmt(n-dc)} battery (BESS) sites</p>
{stat_cards(plist)}
  <p>Below is every data center and battery energy storage (BESS) project we track in {esc(name)},
     ranked by capacity. Battery projects are sourced from the federal EIA-860M inventory; data centers
     are hand-curated with a public source on each entry. See the <a href="/methodology">methodology</a> for details.</p>
  <p><a class="btn btn-primary" href="/map?state={esc(code)}">View {esc(name)} on the map →</a></p>
{data_cta(plist)}
  <h2>All {esc(name)} projects</h2>
  {project_table(plist, show_state=False, show_operator=True)}
</article></div></section>
{FOOTER}"""
        write(f"state/{slug}.html", body)
        urls.append(f"/state/{slug}")
        state_links.append((name, slug, n, mw))

    # ---- Operator pages ----
    by_op = {}
    for p in projects:
        op = p.get("operator")
        if op:
            by_op.setdefault(op, []).append(p)

    op_links = []
    for op, plist in sorted(by_op.items(), key=lambda kv: -len(kv[1])):
        if len(plist) < MIN_OPERATOR:
            continue
        slug = kebab(op)
        n = len(plist); mw = sum(x.get("capacity_mw") or 0 for x in plist)
        states = sorted({x["state"] for x in plist})
        title = f"{op} Data Center & Energy Projects — US Energy Map"
        desc = (f"{fmt(n)} tracked {op} data center and battery storage projects across "
                f"{len(states)} states, totaling {fmt(mw)} MW. Locations, capacity, and status.")
        canonical = f"{SITE}/operator/{slug}"
        body = head(title, desc, canonical) + f"""
<section><div class="wrap"><article class="prose" style="max-width:980px;">
  <p class="kicker">Operator portfolio</p>
  <h1>{esc(op)} — tracked projects</h1>
  <p class="sub">{fmt(n)} projects · {fmt(mw)} MW · {len(states)} states</p>
{stat_cards(plist)}
  <p>Every {esc(op)} data center and battery storage project we track, ranked by capacity.
     See the <a href="/methodology">methodology</a> for how this data is sourced and defined.</p>
  <p><a class="btn btn-primary" href="/map?operator={esc(op)}">View {esc(op)} on the map →</a></p>
{data_cta(plist)}
  <h2>All {esc(op)} projects</h2>
  {project_table(plist, show_state=True, show_operator=False)}
</article></div></section>
{FOOTER}"""
        write(f"operator/{slug}.html", body)
        urls.append(f"/operator/{slug}")
        op_links.append((op, slug, n, mw))

    # ---- Explore hub ----
    state_items = "".join(
        f'<a class="card" href="/state/{slug}" style="display:block;text-decoration:none;">'
        f'<h3 style="margin:0 0 4px;">{esc(name)}</h3>'
        f'<p style="margin:0;">{fmt(n)} projects · {fmt(mw)} MW</p></a>'
        for name, slug, n, mw in sorted(state_links, key=lambda x: x[0])
    )
    op_items = "".join(
        f'<a class="card" href="/operator/{slug}" style="display:block;text-decoration:none;">'
        f'<h3 style="margin:0 0 4px;">{esc(op)}</h3>'
        f'<p style="margin:0;">{fmt(n)} projects · {fmt(mw)} MW</p></a>'
        for op, slug, n, mw in sorted(op_links, key=lambda x: -x[2])
    )
    hub = head(
        "Explore Data Centers & Battery Storage by State and Operator — US Energy Map",
        "Browse every U.S. state and major operator's data center and battery storage (BESS) projects, with capacity and status. From US Energy Map.",
        f"{SITE}/explore",
    ) + f"""
<section><div class="wrap">
  <div class="section-head"><p class="kicker">Explore</p>
    <h2>Browse by state &amp; operator</h2>
    <p>Jump straight to the buildout in any state, or to a single operator's full portfolio.</p></div>
  <h2 style="font-size:20px;margin:8px 0 16px;">By state</h2>
  <div class="cards">{state_items}</div>
  <h2 style="font-size:20px;margin:40px 0 16px;">By operator</h2>
  <div class="cards">{op_items}</div>
</div></section>
{FOOTER}"""
    write("explore.html", hub)

    # ---- sitemap.xml + robots.txt ----
    today = datetime.date.today().isoformat()
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        sm.append(f"  <url><loc>{SITE}{u}</loc><lastmod>{today}</lastmod></url>")
    sm.append("</urlset>")
    write("sitemap.xml", "\n".join(sm))
    write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")

    # ---- Prune orphaned operator pages (operator renamed by EIA or portfolio fell below MIN_OPERATOR) ----
    # Deleting them would 404 any indexed link, so each pruned slug gets a 301 → /explore in vercel.json.
    generated = {slug for _, slug, _, _ in op_links}
    op_dir = os.path.join(ROOT, "operator")
    pruned = []
    for fn in sorted(os.listdir(op_dir)):
        if fn.endswith(".html") and fn[:-5] not in generated:
            os.remove(os.path.join(op_dir, fn)); pruned.append(fn[:-5])
    if pruned:
        vpath = os.path.join(ROOT, "vercel.json")
        v = json.load(open(vpath))
        existing = {r["source"] for r in v.get("redirects", [])}
        for slug in pruned:
            src = f"/operator/{slug}"
            if src not in existing:
                v.setdefault("redirects", []).append({"source": src, "destination": "/explore", "permanent": True})
        with open(vpath, "w") as f:
            f.write(json.dumps(v, indent=2) + "\n")
        print(f"Pruned {len(pruned)} orphaned operator pages → 301 to /explore: {', '.join(pruned)}")

    print(f"Generated {len(state_links)} state pages, {len(op_links)} operator pages, "
          f"explore hub, sitemap ({len(urls)} urls), robots.txt")

if __name__ == "__main__":
    main()
