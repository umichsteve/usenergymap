#!/usr/bin/env python3
"""
Generate programmatic SEO pages for US Energy Map from projects.json.

Outputs (at repo root):
  state/<slug>.html       — one page per state  (served at /state/<slug>)
  operator/<slug>.html    — one page per operator with >= MIN_OPERATOR projects
  data-centers.html       — data center map landing page  (served at /data-centers)
  data-centers/<slug>.html — one data center map page per state with >= MIN_DC_STATE campuses
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
MIN_DC_STATE = 3  # only build a state data center map page when the state has this many campuses

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
        <a href="/data-centers">Data center map</a>
        <a href="/bess">Battery storage (BESS)</a>
        <a href="/changelog">What changed</a>
        <a href="/data">Data &amp; pricing</a>
        <a href="/methodology">Methodology</a>
        <a href="/license">License</a>
        <a href="/guides/home-backup-power">Home backup power guide</a>
        <a href="/about">About</a>
        <a href="/privacy">Privacy</a>
        <a href="/disclosure">Affiliate disclosure</a>
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

# Free-sample capture. Paid ads land on these pages, so the page needs something to
# convert on that is not a $199 button — the sample is the cheap yes that makes the
# expensive yes possible. Markup is shared with bess.html; behaviour and the Google Ads
# "lead" conversion live in /assets/lead-capture.js.
SAMPLE_CAPTURE = """
  <div class="sample-cta" data-sample-form data-source="{source}">
    <h3>Free sample — the 100 largest tracked projects</h3>
    <p>Every column we publish: operator, city, capacity (MW/MWh), status, year online and a
       public source on each row. Opens straight in Excel or Sheets. No card, no call.</p>
    <form class="sample-form" novalidate>
      <input type="email" name="email" inputmode="email" autocomplete="email"
             placeholder="you@company.com" aria-label="Email address for the free sample" required />
      <button class="btn btn-primary" type="submit">Email me the sample &rarr;</button>
    </form>
    <p class="sample-msg" role="status" aria-live="polite"></p>
    <p class="sample-fine">One email with the file. No list, no drip sequence &mdash; see our
       <a href="/privacy">privacy policy</a>.</p>
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

PIPELINE = ("under_construction", "planned", "announced")

def fmt_usd(n):
    """$50B / $1.5B / $800M for a disclosed investment figure."""
    if not n: return "—"
    if n >= 1e9:
        v = n / 1e9
        return f"${v:.1f}B".replace(".0B", "B")
    return f"${n/1e6:.0f}M"

def dc_stats(projects):
    """Headline figures for a set of data center rows. MW counts only rows with a disclosed figure."""
    n = len(projects)
    with_mw = [p for p in projects if p.get("capacity_mw")]
    mw = sum(p["capacity_mw"] for p in with_mw)
    pipe = [p for p in projects if p["status"] in PIPELINE]
    pipe_mw = sum(p.get("capacity_mw") or 0 for p in pipe)
    return {"n": n, "mw": mw, "n_mw": len(with_mw), "pipe": len(pipe), "pipe_mw": pipe_mw,
            "op": sum(1 for p in projects if p["status"] == "operating"),
            "states": len({p["state"] for p in projects})}

def dc_table(projects, show_state=True):
    """Data center rows ranked by MW. Adds county and disclosed investment, which the mixed table omits."""
    rows = []
    for p in sorted(projects, key=lambda x: (x.get("capacity_mw") or 0, x.get("investment_usd") or 0), reverse=True):
        city = p.get("city") or ""
        county = p.get("county_name") or ""
        if county.lower() == city.lower():  # county-level placements carry the county as the city
            county = ""
        loc = ", ".join(x for x in (city, county, p.get("state", "") if show_state else "") if x)
        if p.get("capacity_mw"):
            mw = ("~" if p.get("capacity_estimated") else "") + fmt(p["capacity_mw"]) + " MW"
        else:
            mw = "—"
        badge = {"operating":"op","under_construction":"uc","planned":"pl","announced":"pl"}.get(p["status"],"pl")
        src = f'<a href="{esc(p["source"])}" target="_blank" rel="noopener">source ↗</a>' if p.get("source") else ""
        rows.append(
            f'<tr id="{esc(p["id"])}"><td><strong><a href="/map#{esc(p["id"])}">{esc(p["name"])}</a></strong></td>'
            f'<td>{esc(p.get("operator","—"))}</td><td>{esc(loc)}</td><td>{mw}</td>'
            f'<td>{fmt_usd(p.get("investment_usd"))}</td>'
            f'<td><span class="badge {badge}">{status_label(p["status"])}</span></td>'
            f'<td>{p.get("year_online","—")}</td><td>{src}</td></tr>'
        )
    return (
        '<div style="overflow-x:auto;"><table class="deftable"><thead><tr>'
        '<th>Campus</th><th>Operator</th><th>Location</th><th>Power</th><th>Investment</th>'
        '<th>Status</th><th>Year</th><th>Source</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table></div>"
    )

def dc_map_frame(query, label):
    """The live map, filtered to data centers. /embed is noindex, so the page around it carries the SEO."""
    return (f'<div style="border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:0 0 28px;">'
            f'<iframe src="/embed?{query}" title="{esc(label)}" loading="lazy" '
            f'style="display:block;width:100%;height:min(70vh,560px);border:0;"></iframe></div>')

def dc_statbar(st, margin="0 0 32px"):
    return f"""    <div class="statbar" style="margin:{margin};">
      <div class="stat"><div class="num dc">{fmt(st["n"])}</div><div class="lbl">data center campuses</div></div>
      <div class="stat"><div class="num mw">{fmt(st["mw"])}</div><div class="lbl">MW disclosed</div></div>
      <div class="stat"><div class="num st">{fmt(st["pipe"])}</div><div class="lbl">planned or under construction</div></div>
      <div class="stat"><div class="num bess">{fmt(st["op"])}</div><div class="lbl">operating</div></div>
    </div>"""

def write(path, content):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)

def main():
    data = json.load(open(os.path.join(ROOT, "projects.json")))
    projects = data.get("projects", [])
    urls = ["/", "/map", "/explore", "/changelog", "/data", "/data-centers", "/bess", "/license", "/methodology", "/about", "/disclosure", "/guides/home-backup-power"]

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
        dc_link = (f' <a class="btn btn-ghost" href="/data-centers/{slug}">{esc(name)} data center map →</a>'
                   if dc >= MIN_DC_STATE else "")
        body = head(title, desc, canonical) + f"""
<section><div class="wrap"><article class="prose" style="max-width:980px;">
  <p class="kicker">State profile</p>
  <h1>Data centers &amp; battery storage in {esc(name)}</h1>
  <p class="sub">{fmt(n)} tracked projects · {fmt(mw)} MW · {fmt(dc)} data centers, {fmt(n-dc)} battery (BESS) sites</p>
{stat_cards(plist)}
  <p>Below is every data center and battery energy storage (BESS) project we track in {esc(name)},
     ranked by capacity. Battery projects are sourced from the federal EIA-860M inventory; data centers
     are hand-curated with a public source on each entry. See the <a href="/methodology">methodology</a> for details.</p>
  <p><a class="btn btn-primary" href="/map?state={esc(code)}">View {esc(name)} on the map →</a>{dc_link}</p>
{SAMPLE_CAPTURE.format(source=f"state/{slug}")}
{data_cta(plist)}
  <h2>All {esc(name)} projects</h2>
  {project_table(plist, show_state=False, show_operator=True)}
</article></div></section>
<script src="/assets/lead-capture.js" defer></script>
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

    # ---- Data center map pages ----
    # Search demand is for "data center map" and "<state> data center map", which the mixed
    # state pages and the bare /map title do not answer. These pages filter the same dataset
    # to data centers, lead with the live map, and put the build pipeline first.
    dcs = [p for p in projects if p["type"] == "data_center"]
    op_slugs = {op: slug for op, slug, _, _ in op_links}
    dc_by_state = {}
    for p in dcs:
        dc_by_state.setdefault(p["state"], []).append(p)
    dc_state_pages = {code: kebab(STATE_NAMES.get(code, code))
                      for code, plist in dc_by_state.items() if len(plist) >= MIN_DC_STATE}

    for code, slug in sorted(dc_state_pages.items()):
        plist = dc_by_state[code]
        name = STATE_NAMES.get(code, code)
        st = dc_stats(plist)
        pipe = [p for p in plist if p["status"] in PIPELINE]
        ops = sorted({p.get("operator") for p in plist if p.get("operator")})
        title = f"{name} Data Center Map: {fmt(st['n'])} Campuses Tracked | US Energy Map"
        desc = (f"Map of {fmt(st['n'])} data center campuses in {name}: {fmt(st['op'])} operating and "
                f"{fmt(st['pipe'])} planned or under construction, with operator, county, power in MW "
                f"and a public source on every row.")
        pipe_block = ""
        if pipe:
            pipe_block = f"""
  <h2>Planned and under construction in {esc(name)}</h2>
  <p>{fmt(len(pipe))} {esc(name)} campuses are announced, planned or being built, with
     {fmt(st['pipe_mw'])} MW of disclosed power between them.</p>
  {dc_table(pipe, show_state=False)}"""
        body = head(title, desc, f"{SITE}/data-centers/{slug}") + f"""
<section><div class="wrap"><article class="prose" style="max-width:1040px;">
  <p class="kicker"><a href="/data-centers">US data center map</a> · {esc(name)}</p>
  <h1>{esc(name)} data center map</h1>
  <p class="sub">{fmt(st['n'])} campuses · {fmt(st['mw'])} MW disclosed · {fmt(len(ops))} operators</p>
{dc_map_frame(f"type=data_center&state={esc(code)}", f"Map of data centers in {name}")}
{dc_statbar(st)}
  <p>Every {esc(name)} data center campus we track, from operating hyperscale sites to projects still
     in permitting. Each row carries the operator, city and county, power where the operator or a
     filing has disclosed it, and a public source. Power is shown for {fmt(st['n_mw'])} of
     {fmt(st['n'])} campuses; a tilde marks an estimate. See the <a href="/methodology">methodology</a>
     for definitions.</p>
  <p><a class="btn btn-primary" href="/map?type=data_center&state={esc(code)}">Open the full {esc(name)} map →</a>
     <a class="btn btn-ghost" href="/state/{slug}">{esc(name)} battery storage and all projects →</a></p>
{pipe_block}
{SAMPLE_CAPTURE.format(source=f"data-centers/{slug}")}
{data_cta(plist)}
  <h2>All {esc(name)} data centers</h2>
  {dc_table(plist, show_state=False)}
</article></div></section>
<script src="/assets/lead-capture.js" defer></script>
{FOOTER}"""
        write(f"data-centers/{slug}.html", body)
        urls.append(f"/data-centers/{slug}")

    # Prune state data center pages that fell below the threshold; 301 them to the hub.
    dc_dir = os.path.join(ROOT, "data-centers")
    os.makedirs(dc_dir, exist_ok=True)
    dc_pruned = [fn[:-5] for fn in sorted(os.listdir(dc_dir))
                 if fn.endswith(".html") and fn[:-5] not in set(dc_state_pages.values())]
    for slug in dc_pruned:
        os.remove(os.path.join(dc_dir, slug + ".html"))
    if dc_pruned:
        vpath = os.path.join(ROOT, "vercel.json")
        v = json.load(open(vpath))
        existing = {r["source"] for r in v.get("redirects", [])}
        for slug in dc_pruned:
            src = f"/data-centers/{slug}"
            if src not in existing:
                v.setdefault("redirects", []).append({"source": src, "destination": "/data-centers", "permanent": True})
        with open(vpath, "w") as f:
            f.write(json.dumps(v, indent=2) + "\n")

    st = dc_stats(dcs)
    pipe = [p for p in dcs if p["status"] in PIPELINE]
    state_cards = ""
    for code, plist in sorted(dc_by_state.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        name = STATE_NAMES.get(code, code)
        href = (f"/data-centers/{dc_state_pages[code]}" if code in dc_state_pages
                else f"/state/{kebab(name)}")
        s2 = dc_stats(plist)
        state_cards += (
            f'<a class="card" href="{href}" style="display:block;text-decoration:none;">'
            f'<h3 style="margin:0 0 4px;">{esc(name)}</h3>'
            f'<p style="margin:0;">{fmt(s2["n"])} campus{"es" if s2["n"] != 1 else ""} · '
            f'{fmt(s2["mw"])} MW disclosed · {fmt(s2["pipe"])} in the pipeline</p></a>')
    dc_by_op = {}
    for p in dcs:
        if p.get("operator"):
            dc_by_op.setdefault(p["operator"], []).append(p)
    op_cards = ""
    for op, plist in sorted(dc_by_op.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:12]:
        s2 = dc_stats(plist)
        label = (f'<h3 style="margin:0 0 4px;">{esc(op)}</h3>'
                 f'<p style="margin:0;">{fmt(s2["n"])} campus{"es" if s2["n"] != 1 else ""} · '
                 f'{fmt(s2["mw"])} MW disclosed · {fmt(s2["states"])} state{"s" if s2["states"] != 1 else ""}</p>')
        op_cards += (f'<a class="card" href="/operator/{op_slugs[op]}" style="display:block;text-decoration:none;">{label}</a>'
                     if op in op_slugs else f'<div class="card">{label}</div>')

    hub_title = f"US Data Center Map: {fmt(st['n'])} Hyperscale and AI Campuses | US Energy Map"
    hub_desc = (f"Interactive map of {fmt(st['n'])} US data center campuses across {fmt(st['states'])} states: "
                f"{fmt(st['op'])} operating and {fmt(st['pipe'])} planned or under construction, with operator, "
                f"county, power in MW and a public source on every row.")
    dc_hub = head(hub_title, hub_desc, f"{SITE}/data-centers") + f"""
<header class="hero">
  <div class="hero-grid"></div>
  <div class="hero-inner">
    <span class="eyebrow"><span class="pulse"></span> Hand-curated · public source on every row</span>
    <h1 class="display">The US <span class="grad">data center map</span>, with the power numbers.</h1>
    <p class="lede">{fmt(st['n'])} hyperscale, AI and major colocation campuses across {fmt(st['states'])} states.
       See what is operating, what is under construction and what is planned, with the operator, county
       and megawatts behind each site.</p>
    <div class="hero-cta">
      <a class="btn btn-primary" href="/map?type=data_center">Open the full map →</a>
      <a class="btn btn-ghost" href="#sample">Get the free sample</a>
    </div>
{dc_statbar(st, "40px 0 0")}
  </div>
</header>

<section><div class="wrap">
  <div class="section-head"><p class="kicker">Live map</p>
    <h2>Every tracked US data center</h2>
    <p>Marker size follows power in MW. The ring shows status: operating, under construction or planned.
       Click a marker for the operator and capacity.</p></div>
{dc_map_frame("type=data_center", "Map of US data centers")}
  <p><a class="btn btn-ghost" href="/map?type=data_center">Open the full map with filters and grid operator overlays →</a></p>
</div></section>

<section class="alt"><div class="wrap">
  <div class="section-head"><p class="kicker">The pipeline</p>
    <h2>Planned and under construction</h2>
    <p>{fmt(st['pipe'])} campuses are announced, planned or being built, with {fmt(st['pipe_mw'])} MW of
       disclosed power between them. This is the load utilities and grid operators are planning around.</p></div>
  {dc_table(pipe)}
</div></section>

<section><div class="wrap">
  <div class="section-head"><p class="kicker">By state</p>
    <h2>Data centers by state</h2>
    <p>Jump to a state's own data center map and full campus list.</p></div>
  <div class="cards">{state_cards}</div>
</div></section>

<section class="alt"><div class="wrap">
  <div class="section-head"><p class="kicker">By operator</p>
    <h2>Who is building</h2>
    <p>The largest tracked portfolios. Each links to that operator's full project list.</p></div>
  <div class="cards">{op_cards}</div>
</div></section>

<section id="sample"><div class="wrap"><div class="prose" style="max-width:880px;">
{SAMPLE_CAPTURE.format(source="data-centers")}
{data_cta(dcs)}
</div></div></section>

<section class="alt"><div class="wrap"><article class="prose" style="max-width:1040px;">
  <h2>All {fmt(st['n'])} tracked data centers</h2>
  <p>Ranked by disclosed power. Power is shown for {fmt(st['n_mw'])} of {fmt(st['n'])} campuses; a tilde
     marks an estimate, and a dash means the operator has not published a figure.</p>
  {dc_table(dcs)}
  <h2>What this map covers</h2>
  <p>There is no federal registry of data centers, so this layer is curated by hand from operator
     announcements, utility and permitting filings, and trade reporting, with the source linked on each
     row. It covers hyperscale campuses, AI training sites and the major colocation portfolios. It does
     not yet list every smaller colocation or enterprise facility. Coordinates are the exact site where
     it is public and the city or county centroid where it is not. The
     <a href="/methodology">methodology</a> has the full definitions, and you can
     <a href="mailto:hello@usenergymap.com?subject=Suggest%20a%20data%20center">suggest a project</a>
     we are missing.</p>
  <p>Tracking the storage side of the same buildout? See the
     <a href="/bess">battery storage (BESS) database</a>.</p>
</article></div></section>
<script src="/assets/lead-capture.js" defer></script>
{FOOTER}"""
    write("data-centers.html", dc_hub)

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
    <p>Jump straight to the buildout in any state, or to a single operator's full portfolio.
       Looking only for data centers? See the <a href="/data-centers">US data center map</a>.</p></div>
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
          f"data center map hub + {len(dc_state_pages)} state data center pages, explore hub, sitemap ({len(urls)} urls), robots.txt")

if __name__ == "__main__":
    main()
