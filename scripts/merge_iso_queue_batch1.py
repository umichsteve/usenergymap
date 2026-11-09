#!/usr/bin/env python3
"""Merge data/queues/iso_batch1.json into projects.json and add ferc_docket to eia-67322.

Surgical text edit (does not round-trip through json.dumps) so unchanged records
stay byte-for-byte identical — the diff is exactly: one line added to eia-67322
plus the 25 new ISO queue records.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "projects.json"
BATCH = ROOT / "data/queues/iso_batch1.json"
MAMM_DOCKET = "https://elibrary.ferc.gov/eLibrary/docketsheet?docket_number=QF24-475-001"


def project_to_lines(obj):
    """Match the existing 2-space indent / per-field-on-own-line style."""
    out = ["    {"]
    items = list(obj.items())
    for i, (k, v) in enumerate(items):
        val = json.dumps(v, ensure_ascii=False)
        comma = "," if i < len(items) - 1 else ""
        out.append(f"      {json.dumps(k, ensure_ascii=False)}: {val}{comma}")
    out.append("    }")
    return "\n".join(out)


def main():
    text = PROJECTS.read_text()
    batch = json.loads(BATCH.read_text())
    before_ids = set(re.findall(r'^      "id": "([^"\\]+)",?$', text, flags=re.MULTILINE))

    # --- 1. eia-67322: add ferc_docket after year_online ---
    # Find the eia-67322 record block.
    m = re.search(r'(    \{[^{}]*?"id": "eia-67322"[^{}]*?\n    \})', text)
    if not m:
        raise SystemExit("eia-67322 record not found")
    block = m.group(1)
    if '"ferc_docket"' in block:
        print("[merge] eia-67322 already has ferc_docket — leaving alone")
        new_block = block
    else:
        # Append ferc_docket as the last field. The last field currently is "year_online".
        # Strategy: replace the final `  }` with the new line + `  }` and add a comma
        # to the line immediately before `  }`.
        lines = block.splitlines()
        # Find the last field line (second-to-last; last is the closing "    }")
        assert lines[-1].strip() == "}"
        last_field = lines[-2]
        if not last_field.rstrip().endswith(","):
            lines[-2] = last_field + ","
        lines.insert(-1, f'      "ferc_docket": {json.dumps(MAMM_DOCKET)}')
        new_block = "\n".join(lines)
    text = text[:m.start()] + new_block + text[m.end():]

    # --- 2. Insert new ISO queue rows before the closing `]` of the projects array ---
    # Only add rows whose id is not already present.
    new_rows = [row for row in batch if row["id"] not in before_ids]
    skipped = [row for row in batch if row["id"] in before_ids]

    if new_rows:
        # Find the `  ],` that closes the projects array.
        # projects.json always has it immediately before `  "eia_source_month":` or `}` at eof.
        close_pat = re.search(r'\n    \}\n  \],\n', text)
        if not close_pat:
            raise SystemExit("could not find closing `  ],` of projects array")
        insert_pos = close_pat.start() + len("\n    }")  # right after final existing project's closing `}`
        chunk = ",\n" + ",\n".join(project_to_lines(o) for o in new_rows)
        text = text[:insert_pos] + chunk + text[insert_pos:]

    PROJECTS.write_text(text)

    # Validation
    data = json.loads(text)
    ids = [p["id"] for p in data["projects"]]
    assert len(ids) == len(set(ids)), "duplicate ids after merge"
    mamm = next(p for p in data["projects"] if p["id"] == "eia-67322")
    assert mamm.get("ferc_docket") == MAMM_DOCKET, "ferc_docket missing on eia-67322"

    print(f"[merge] added {len(new_rows)} new ISO queue rows ({len(skipped)} already present, left alone)")
    print(f"[merge] eia-67322 ferc_docket: {mamm['ferc_docket']}")
    print(f"[merge] total projects: {len(data['projects'])}")
    print("[merge] ids unique ✔")


if __name__ == "__main__":
    main()
