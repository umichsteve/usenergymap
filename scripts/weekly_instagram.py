#!/usr/bin/env python3
"""
Generate a weekly "What's new" Instagram graphic + caption for US Energy Map.

- Renders a branded 1080x1080 PNG (dark theme, matches the site).
- Always has content: if projects were added since last run it leads with
  "New this week"; otherwise it shows a rotating weekly spotlight
  (milestone / biggest project / top state / top operator / status mix).
- Writes the image + caption.txt to an output dir and prints the caption.

Usage:  python3 scripts/weekly_instagram.py [output_dir]
Deps:   pip install cairosvg  (already available in the Cowork sandbox)
"""

import json, os, sys, html, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "assets", "social", "weekly")
SNAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".weekly_snapshot.json")
FS = "-apple-system, Helvetica, Arial, sans-serif"

STATE_NAMES = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California","CO":"Colorado",
    "CT":"Connecticut","DE":"Delaware","FL":"Florida","GA":"Georgia","HI":"Hawaii","ID":"Idaho",
    "IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas","KY":"Kentucky","LA":"Louisiana",
    "ME":"Maine","MD":"Maryland","MA":"Massachusetts","MI":"Michigan","MN":"Minnesota",
    "MS":"Mississippi","MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada",
    "NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina",
    "ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania",
    "RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee","TX":"Texas",
    "UT":"Utah","VT":"Vermont","VA":"Virginia","WA":"Washington","WV":"West Virginia",
    "WI":"Wisconsin","WY":"Wyoming","DC":"District of Columbia","PR":"Puerto Rico",
}

def esc(s): return html.escape(str(s if s is not None else ""))
def fmt(n): return f"{int(round(n)):,}"

def load():
    return json.load(open(os.path.join(ROOT, "projects.json"))).get("projects", [])

def whats_new(projects):
    ids = {p["id"] for p in projects}
    prev = set()
    if os.path.exists(SNAP):
        try: prev = set(json.load(open(SNAP)))
        except Exception: prev = set()
    new_ids = ids - prev
    json.dump(sorted(ids), open(SNAP, "w"))
    if not prev:        # first run — establish baseline, no "new"
        return []
    return [p for p in projects if p["id"] in new_ids]

def pick_spotlight(projects):
    """Deterministic weekly rotation so each week differs."""
    week = datetime.date.today().isocalendar()[1]
    n = len(projects)
    mw = sum(p.get("capacity_mw") or 0 for p in projects)
    states = len({p["state"] for p in projects})
    kind = week % 5

    if kind == 0:
        return ("THE BIG PICTURE",
                f"{fmt(mw)} MW", "mapped across "
                f"{fmt(n)} projects in {states} states")
    if kind == 1:
        big = max(projects, key=lambda p: p.get("capacity_mw") or 0)
        t = "data center" if big["type"] == "data_center" else "battery storage"
        return ("BIGGEST ON THE MAP",
                esc(big["name"]),
                f'{fmt(big.get("capacity_mw") or 0)} MW {t} · {esc(big.get("city",""))}, {esc(big["state"])}')
    if kind == 2:
        sm = {}
        for p in projects: sm[p["state"]] = sm.get(p["state"], 0) + (p.get("capacity_mw") or 0)
        st, val = max(sm.items(), key=lambda kv: kv[1])
        cnt = sum(1 for p in projects if p["state"] == st)
        return ("STATE IN FOCUS", STATE_NAMES.get(st, st),
                f"{fmt(val)} MW across {fmt(cnt)} projects — the most of any state")
    if kind == 3:
        oc = {}
        for p in projects:
            o = p.get("operator")
            if o: oc[o] = oc.get(o, 0) + 1
        op, cnt = max(oc.items(), key=lambda kv: kv[1])
        return ("OPERATOR SPOTLIGHT", esc(op), f"{fmt(cnt)} tracked projects nationwide")
    # kind == 4
    op = sum(1 for p in projects if p["status"] == "operating")
    uc = sum(1 for p in projects if p["status"] == "under_construction")
    pl = sum(1 for p in projects if p["status"] == "planned")
    return ("THE PIPELINE", f"{fmt(uc + pl)} in the works",
            f"{fmt(op)} operating · {fmt(uc)} under construction · {fmt(pl)} planned")

def render_png(eyebrow, headline, subline, out_png):
    import cairosvg
    # auto-shrink very long headlines
    hsize = 104 if len(headline) <= 16 else (78 if len(headline) <= 26 else 58)
    svg = f"""<svg viewBox="0 0 1080 1080" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0b1224"/><stop offset="1" stop-color="#111c33"/></linearGradient>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2563eb"/><stop offset="1" stop-color="#06b6d4"/></linearGradient>
  </defs>
  <rect width="1080" height="1080" fill="url(#bg)"/>
  <g stroke="#1e293b" stroke-width="1" opacity="0.55">
    {''.join(f'<line x1="0" y1="{y}" x2="1080" y2="{y}"/>' for y in range(120,1080,120))}
    {''.join(f'<line x1="{x}" y1="0" x2="{x}" y2="1080"/>' for x in range(120,1080,120))}
  </g>
  <g transform="translate(80,70)">
    <circle cx="24" cy="24" r="22" fill="url(#g)"/>
    <circle cx="24" cy="38" r="2.6" fill="#34d399"/>
    <circle cx="24" cy="10" r="2" fill="#fff" opacity="0.55"/>
    <circle cx="38" cy="24" r="2" fill="#fff" opacity="0.55"/>
    <circle cx="10" cy="24" r="2" fill="#fff" opacity="0.55"/>
    <path d="M28 9 L14.5 27 L22.5 27 L20 40 L34 20.5 L25.5 20.5 Z" fill="#fff"/>
  </g>
  <text x="142" y="103" font-family="{FS}" font-size="26" font-weight="700" fill="#e2e8f0" letter-spacing="1.5">US ENERGY MAP</text>
  <text x="80" y="430" font-family="{FS}" font-size="34" font-weight="700" fill="#38bdf8" letter-spacing="3">{esc(eyebrow)}</text>
  <text x="80" y="430" font-family="{FS}" font-size="{hsize}" font-weight="800" fill="#ffffff" letter-spacing="-1.5" dy="100">{esc(headline)}</text>
  <text x="80" y="620" font-family="{FS}" font-size="34" font-weight="400" fill="#94a3b8" dy="40">{esc(subline)}</text>
  <g transform="translate(80,900)">
    <rect x="0" y="0" width="380" height="76" rx="38" fill="url(#g)"/>
    <text x="190" y="50" text-anchor="middle" font-family="{FS}" font-size="30" font-weight="700" fill="#fff">usenergymap.com</text>
  </g>
  <text x="1000" y="950" text-anchor="end" font-family="{FS}" font-size="26" font-weight="600" fill="#64748b">@usenergymap</text>
</svg>"""
    cairosvg.svg2png(bytestring=svg.encode(), write_to=out_png, output_width=1080, output_height=1080)

def build_caption(eyebrow, headline, subline, new_projects):
    today = datetime.date.today().strftime("%B %-d, %Y")
    if new_projects:
        sample = ", ".join(p["name"] for p in sorted(
            new_projects, key=lambda x: x.get("capacity_mw") or 0, reverse=True)[:3])
        lead = (f"📍 What's new on US Energy Map — week of {today}\n\n"
                f"{len(new_projects)} project(s) just added, including {sample}. "
                f"Explore them all on the live map.")
    else:
        lead = (f"📍 US Energy Map — week of {today}\n\n"
                f"{eyebrow.title()}: {headline}. {subline}.")
    tags = ("#energy #datacenter #batterystorage #BESS #cleanenergy #gridtech "
            "#infrastructure #renewableenergy #AI #energytransition #powergrid #energystorage")
    return (f"{lead}\n\nSee where the U.S. buildout is happening → link in bio (usenergymap.com)\n\n{tags}")

def main():
    os.makedirs(OUT, exist_ok=True)
    projects = load()
    new = whats_new(projects)
    if new:
        eyebrow = "WHAT'S NEW THIS WEEK"
        headline = f"{len(new)} new project{'s' if len(new) != 1 else ''}"
        added_mw = sum(p.get("capacity_mw") or 0 for p in new)
        subline = f"just added — {fmt(added_mw)} MW across {len({p['state'] for p in new})} states"
    else:
        eyebrow, headline, subline = pick_spotlight(projects)

    stamp = datetime.date.today().isoformat()
    png = os.path.join(OUT, f"weekly-{stamp}.png")
    txt = os.path.join(OUT, f"weekly-{stamp}.txt")
    render_png(eyebrow, headline, subline, png)
    caption = build_caption(eyebrow, headline, subline, new)
    open(txt, "w").write(caption)

    print(f"IMAGE: {png}")
    print(f"CAPTION_FILE: {txt}")
    print("----- CAPTION -----")
    print(caption)

if __name__ == "__main__":
    main()
