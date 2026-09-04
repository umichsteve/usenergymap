#!/usr/bin/env python3
"""
Build changelog.json by diffing the working-tree projects.json against the previous
committed version (or two explicit files).

    python3 scripts/build_changelog.py                 # HEAD:projects.json  →  ./projects.json
    python3 scripts/build_changelog.py --old a.json --new b.json

Runs in the monthly refresh workflow right after ingest_eia.py, so the diff is
"what this month's refresh changed". Row-level detail is what Always Current
subscribers download; the public /changelog page renders only the summary.
"""
import argparse, json, subprocess, sys, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "changelog.json"
TRACKED = ["name", "operator", "city", "state", "status", "capacity_mw", "capacity_mwh", "year_online", "type", "investment_usd", "lat", "lng"]
HISTORY_MAX = 24  # months of summaries kept on the public page


def load(path):
    with open(path) as f:
        return json.load(f)


def load_git(rev, rel):
    try:
        out = subprocess.check_output(["git", "show", f"{rev}:{rel}"], cwd=ROOT, stderr=subprocess.DEVNULL)
        return json.loads(out)
    except subprocess.CalledProcessError:
        return None


def row(p):
    return {k: p.get(k) for k in ["id", "name", "type", "operator", "city", "state", "status", "capacity_mw", "capacity_mwh", "year_online"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old"); ap.add_argument("--new"); ap.add_argument("--rev", default="HEAD")
    a = ap.parse_args()

    new = load(a.new) if a.new else load(ROOT / "projects.json")
    old = load(a.old) if a.old else load_git(a.rev, "projects.json")
    if old is None:
        print("No previous projects.json found — writing an empty changelog.", file=sys.stderr)
        old = {"projects": [], "last_updated": None}

    old_by = {p["id"]: p for p in old.get("projects", [])}
    new_by = {p["id"]: p for p in new.get("projects", [])}

    changes = []
    for pid in sorted(new_by.keys() - old_by.keys()):
        changes.append({"kind": "added", **row(new_by[pid])})
    for pid in sorted(old_by.keys() - new_by.keys()):
        changes.append({"kind": "removed", **row(old_by[pid])})
    for pid in sorted(new_by.keys() & old_by.keys()):
        o, n = old_by[pid], new_by[pid]
        fields = [{"field": k, "from": o.get(k), "to": n.get(k)} for k in TRACKED if o.get(k) != n.get(k)]
        if fields:
            changes.append({"kind": "changed", **row(n), "fields": fields})

    def count(kind, field=None):
        if field is None:
            return sum(1 for c in changes if c["kind"] == kind)
        return sum(1 for c in changes if c["kind"] == "changed" and any(f["field"] == field for f in c["fields"]))

    def mw(ps):
        return round(sum((p.get("capacity_mw") or 0) for p in ps))

    new_ps, old_ps = list(new_by.values()), list(old_by.values())
    added_mw = mw([new_by[c["id"]] for c in changes if c["kind"] == "added"])
    now_operating = [c for c in changes if c["kind"] == "changed" and any(f["field"] == "status" and f["to"] == "operating" for f in c["fields"])]

    summary = {
        "from": old.get("last_updated"),
        "to": new.get("last_updated"),
        "eia_source_month": new.get("eia_source_month"),
        "eia_source_month_prev": old.get("eia_source_month"),
        "added": count("added"),
        "removed": count("removed"),
        "changed": count("changed"),
        "status_changes": count("changed", "status"),
        "capacity_changes": count("changed", "capacity_mw") + count("changed", "capacity_mwh"),
        "newly_operating": len(now_operating),
        "added_mw": added_mw,
        "total_projects": len(new_ps),
        "total_mw": mw(new_ps),
        "net_mw": mw(new_ps) - mw(old_ps),
        "by_type": {
            t: {"added": sum(1 for c in changes if c["kind"] == "added" and c["type"] == t),
                "changed": sum(1 for c in changes if c["kind"] == "changed" and c["type"] == t)}
            for t in ("bess", "data_center")
        },
    }

    # Keep prior summaries so the public page can show a trend without exposing rows.
    history = []
    if OUT.exists():
        try:
            prev = load(OUT)
            history = prev.get("history", [])
            ps_ = prev.get("summary")
            # Keep the prior summary unless this run is a pure re-run of the same diff
            # (same period and identical counts). Two distinct passes on one day both survive.
            same = ps_ and all(ps_.get(k) == summary.get(k) for k in ("from", "to", "added", "removed", "changed"))
            if ps_ and not same:
                history.insert(0, ps_)
        except Exception:
            pass
    history = history[:HISTORY_MAX]

    # Highlights for the public page: biggest additions + notable status flips (names only).
    top_added = sorted([c for c in changes if c["kind"] == "added"], key=lambda c: -(c.get("capacity_mw") or 0))[:8]
    highlights = {
        "top_added": [{"name": c["name"], "type": c["type"], "state": c["state"], "capacity_mw": c["capacity_mw"], "operator": c["operator"]} for c in top_added],
        "newly_operating": [{"name": c["name"], "type": c["type"], "state": c["state"], "capacity_mw": c["capacity_mw"]} for c in
                            sorted(now_operating, key=lambda c: -(c.get("capacity_mw") or 0))[:8]],
    }

    out = {
        "schema_version": "1.0",
        "generated": datetime.date.today().isoformat(),
        "summary": summary,
        "highlights": highlights,
        "history": history,
        "changes": changes,
    }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"changelog.json: {summary['added']} added, {summary['removed']} removed, {summary['changed']} changed "
          f"({summary['status_changes']} status) · {summary['from']} → {summary['to']}")


if __name__ == "__main__":
    main()
