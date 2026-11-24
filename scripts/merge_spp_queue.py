#!/usr/bin/env python3
"""Merge the full SPP queue ingest into projects.json and flip every iso-* record
to the new "queued" status.

Steps:
  1. Strip any existing iso-spp-* records from projects.json.
  2. Insert the records from data/queues/spp_batch.json in their place.
  3. Walk every remaining iso-* record (CAISO, ERCOT) and set status to
     "queued" — those projects are in an operator's queue too and now share
     a single top-level label.

Surgical text edit: untouched records stay byte-for-byte identical so the diff
is exactly the iso- swap plus the status flips on CAISO/ERCOT.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "projects.json"
BATCH = ROOT / "data/queues/spp_batch.json"


def project_to_lines(obj):
    out = ["    {"]
    items = list(obj.items())
    for i, (k, v) in enumerate(items):
        val = json.dumps(v, ensure_ascii=False)
        comma = "," if i < len(items) - 1 else ""
        out.append(f"      {json.dumps(k, ensure_ascii=False)}: {val}{comma}")
    out.append("    }")
    return "\n".join(out)


def find_record_block(text, record_id):
    """Return (start, end) indices of the full `    { ... }` block for the given id."""
    pat = re.compile(
        r'    \{[^{}]*?"id": "' + re.escape(record_id) + r'"[^{}]*?\n    \}',
        re.DOTALL)
    m = pat.search(text)
    return (m.start(), m.end()) if m else None


def strip_record(text, start, end):
    before = text[:start]
    after = text[end:]
    m_after = re.match(r',\n    \{', after)
    if m_after:
        after = after[2:]
        return before + after
    if before.endswith(",\n"):
        before = before[:-2]
    return before + after


def flip_status_to_queued(text, record_id):
    """Rewrite the status field of record_id to "queued". Idempotent."""
    span = find_record_block(text, record_id)
    if not span:
        return text, False
    start, end = span
    block = text[start:end]
    if '"status": "queued"' in block:
        return text, False
    new_block = re.sub(r'("status":\s*")[^"]+(")',
                       lambda m: m.group(1) + "queued" + m.group(2),
                       block, count=1)
    if new_block == block:
        return text, False
    return text[:start] + new_block + text[end:], True


def main():
    text = PROJECTS.read_text()
    batch = json.loads(BATCH.read_text())

    # 1. Strip existing iso-spp-* rows (re-runs are clean).
    spp_ids = [m.group(1) for m in
               re.finditer(r'^      "id": "(iso-spp-[^"]+)",?$', text, flags=re.MULTILINE)]
    for iid in spp_ids:
        span = find_record_block(text, iid)
        if span:
            text = strip_record(text, *span)
    assert not re.search(r'^      "id": "iso-spp-', text, flags=re.MULTILINE), \
        "stripping left iso-spp-* records behind"

    # 2. Insert fresh batch right before the `  ],` that closes the projects array.
    close_pat = re.search(r'\n    \}\n  \],\n', text)
    if not close_pat:
        raise SystemExit("could not find closing `  ],` of projects array")
    insert_pos = close_pat.start() + len("\n    }")
    chunk = ",\n" + ",\n".join(project_to_lines(o) for o in batch)
    text = text[:insert_pos] + chunk + text[insert_pos:]

    # 3. Flip status on every remaining iso-* record (CAISO, ERCOT).
    remaining_iso = [m.group(1) for m in
                     re.finditer(r'^      "id": "(iso-[^"]+)",?$', text, flags=re.MULTILINE)]
    flipped = 0
    for iid in remaining_iso:
        if iid.startswith("iso-spp-"):
            continue  # already set by batch
        text, did = flip_status_to_queued(text, iid)
        if did:
            flipped += 1

    PROJECTS.write_text(text)

    data = json.loads(text)
    ids = [p["id"] for p in data["projects"]]
    assert len(ids) == len(set(ids)), "duplicate ids after merge"
    iso_ct = sum(1 for p in data["projects"] if p["id"].startswith("iso-"))
    spp_ct = sum(1 for p in data["projects"] if p["id"].startswith("iso-spp-"))
    queued_ct = sum(1 for p in data["projects"] if p.get("status") == "queued")

    print(f"[merge] stripped {len(spp_ids)} iso-spp-* rows, inserted {len(batch)} fresh")
    print(f"[merge] flipped {flipped} non-SPP iso-* rows to status=queued")
    print(f"[merge] total projects: {len(data['projects'])} "
          f"(iso-*: {iso_ct}, iso-spp-*: {spp_ct}, queued: {queued_ct})")


if __name__ == "__main__":
    main()
