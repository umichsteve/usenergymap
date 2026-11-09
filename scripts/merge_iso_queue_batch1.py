#!/usr/bin/env python3
"""Replace all iso-* rows in projects.json with the verified batch in
data/queues/iso_batch1.json, and ensure eia-67322 has its ferc_docket.

Surgical text edit: untouched records stay byte-for-byte identical so the
diff is exactly the swap of ISO rows plus the one ferc_docket field.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "projects.json"
BATCH = ROOT / "data/queues/iso_batch1.json"
MAMM_DOCKET = "https://elibrary.ferc.gov/eLibrary/docketsheet?docket_number=QF24-475-001"


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
    """Remove a `    { ... }` record block at [start, end]. Also removes the
    leading `,\n` separator (if preceded by a comma) OR the trailing `,\n` (if
    followed by another record). Leaves the array well-formed."""
    before = text[:start]
    after = text[end:]
    # If the record is followed by ",\n    {" — drop the trailing separator.
    m_after = re.match(r',\n    \{', after)
    if m_after:
        after = after[2:]  # remove ",\n"
        return before + after
    # Otherwise we must drop the preceding ",\n" (this is the last record).
    if before.endswith(",\n"):
        before = before[:-2]
    return before + after


def main():
    text = PROJECTS.read_text()
    batch = json.loads(BATCH.read_text())

    # 1. eia-67322 ferc_docket (idempotent)
    m = re.search(r'(    \{[^{}]*?"id": "eia-67322"[^{}]*?\n    \})', text, flags=re.DOTALL)
    if not m:
        raise SystemExit("eia-67322 not found")
    block = m.group(1)
    if '"ferc_docket"' not in block:
        lines = block.splitlines()
        assert lines[-1].strip() == "}"
        if not lines[-2].rstrip().endswith(","):
            lines[-2] = lines[-2] + ","
        lines.insert(-1, f'      "ferc_docket": {json.dumps(MAMM_DOCKET)}')
        text = text[:m.start()] + "\n".join(lines) + text[m.end():]

    # 2. Strip any existing iso-* records so re-runs are clean.
    existing_iso_ids = [m.group(1) for m in
                        re.finditer(r'^      "id": "(iso-[^"]+)",?$', text, flags=re.MULTILINE)]
    for iid in existing_iso_ids:
        span = find_record_block(text, iid)
        if not span:
            continue
        text = strip_record(text, *span)

    # 3. Verify no iso-* records remain.
    assert not re.search(r'^      "id": "iso-', text, flags=re.MULTILINE), \
        "stripping left iso-* records behind"

    # 4. Insert fresh batch right before the `  ],` that closes the projects array.
    close_pat = re.search(r'\n    \}\n  \],\n', text)
    if not close_pat:
        raise SystemExit("could not find closing `  ],` of projects array")
    insert_pos = close_pat.start() + len("\n    }")
    chunk = ",\n" + ",\n".join(project_to_lines(o) for o in batch)
    text = text[:insert_pos] + chunk + text[insert_pos:]

    PROJECTS.write_text(text)

    data = json.loads(text)
    ids = [p["id"] for p in data["projects"]]
    assert len(ids) == len(set(ids)), "duplicate ids after merge"
    mamm = next(p for p in data["projects"] if p["id"] == "eia-67322")
    assert mamm.get("ferc_docket") == MAMM_DOCKET
    iso_ct = sum(1 for p in data["projects"] if p["id"].startswith("iso-"))

    print(f"[merge] stripped {len(existing_iso_ids)} old iso-* rows, inserted {len(batch)} fresh iso-* rows")
    print(f"[merge] total projects: {len(data['projects'])} (iso-*: {iso_ct})")


if __name__ == "__main__":
    main()
