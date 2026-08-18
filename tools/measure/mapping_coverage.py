"""Dry-run the mapping engine over sample files and report what needs a human.

Reads sample CSVs, maps each distinct column layout once, and prints every
column that needs review (with the reason) and every column that maps to
nothing. Writes nothing anywhere — this is for deciding whether the canonical
vocabulary covers a vendor before any of their data is loaded.

    docker compose run --rm pipeline python /tools/measure/mapping_coverage.py
    docker compose run --rm pipeline python /tools/measure/mapping_coverage.py apollo
"""

import csv
import os
import sys
from collections import defaultdict

from mapping.engine import map_columns
from mapping.repository import SAMPLE_SIZE

SAMPLES = "/data/inbox/samples"

PERSON = ("mental_health", "c_suite", "apollo", "consumers", "leadsnemo")


def entity_for(name: str) -> str:
    low = name.lower()
    return "person" if any(h in low for h in PERSON) else "company"


def read_head(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            rows.append(row)
            if len(rows) >= SAMPLE_SIZE:
                break
        return reader.fieldnames or [], rows


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    seen_layouts = {}
    for name in sorted(os.listdir(SAMPLES)):
        if not name.endswith(".csv"):
            continue
        if only and only not in name:
            continue
        cols, rows = read_head(os.path.join(SAMPLES, name))
        key = (entity_for(name), tuple(cols))
        if key in seen_layouts:
            continue
        seen_layouts[key] = name

        samples = defaultdict(list)
        for row in rows:
            for c in cols:
                v = row.get(c)
                if v not in (None, ""):
                    samples[c].append(str(v))

        mappings = map_columns(cols, key[0], {c: samples[c] for c in cols})
        review = [m for m in mappings if m.status == "needs_review"]
        unmapped = [m for m in mappings if m.canonical_field is None]
        print(f"\n### {name}  [{key[0]}]  {len(cols)} cols "
              f"| review {len(review)} | unmapped {len(unmapped)}")
        for m in review:
            competing = m.evidence.get("competing_columns") or []
            why = f"COLLISION with {competing}" if competing else f"conf {m.confidence:.2f}"
            print(f"  review   {m.source_column!r} -> {m.canonical_field} "
                  f"({m.method}, {why})")
        for m in unmapped:
            print(f"  unmapped {m.source_column!r}")


if __name__ == "__main__":
    main()
