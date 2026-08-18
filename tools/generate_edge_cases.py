"""Generate a 500-row company file where every defect is planted on purpose.

The point is not volume. It is that each row states, in its own
`record_id`, what it is supposed to prove — EDGE-011 is the Excel serial date,
EDGE-032 is the row with no identifier at all — so a result can be checked
against an intention rather than eyeballed.

Three groups:

  EDGE-*   one row per behaviour: normalization forms, validation failures,
           lossless capture, and the record-level rules that decide quarantine.
  DUP-*    rows that must resolve to the SAME entity, by various routes.
  NEAR-*   rows that must NOT resolve automatically, and must become a match
           candidate for a human instead.

Column names are deliberately a vendor's rather than ours: `E-Mail`,
`mobile_no`, `house_address`, `# Employees`. Testing the pipeline with our own
vocabulary would skip the mapping stage, which is the stage most likely to be
wrong about a real file.

    python tools/generate_edge_cases.py [outdir]

Writes edge_cases.csv and edge_cases_manifest.csv side by side.
"""

import csv
import sys
from pathlib import Path

COLUMNS = [
    "record_id", "company_name", "Legal Name", "E-Mail", "mobile_no", "Fax",
    "web_site", "house_address", "location", "state", "zip_code", "country",
    "sector", "# Employees", "founded", "Annual Revenue", "Total Funding",
    "Latest Funding", "Last Raised At", "Technologies", "Keywords",
    "SEO Description", "Number of Retail Locations", "linkedin", "facebook",
    "twitter", "Account Owner", "internal_score",
]

# A row that is unremarkable in every field it does not exist to test.
BASE = {
    "record_id": "", "company_name": "Meridian Health Systems",
    "Legal Name": "Meridian Health Systems, Inc.", "E-Mail": "contact@meridianhs.com",
    "mobile_no": "+1 415 555 0100", "Fax": "+1 415 555 0101",
    "web_site": "https://meridianhs.com", "house_address": "1720 Wisconsin Ave NW",
    "location": "San Francisco", "state": "CA", "zip_code": "94103",
    "country": "United States", "sector": "Hospital & Health Care",
    "# Employees": "4200", "founded": "1994", "Annual Revenue": "812000000",
    "Total Funding": "45000000", "Latest Funding": "Series C",
    "Last Raised At": "2023-04-11", "Technologies": "Microsoft Office 365, Drupal",
    "Keywords": "healthcare, hospital, patient care",
    "SEO Description": "Meridian Health Systems provides regional hospital care.",
    "Number of Retail Locations": "12",
    "linkedin": "https://linkedin.com/company/meridianhs",
    "facebook": "https://facebook.com/meridianhs",
    "twitter": "https://twitter.com/meridianhs",
    "Account Owner": "priya@ourcrm.com", "internal_score": "77",
}


def row(case_id: str, expectation: str, **overrides) -> tuple[dict, dict]:
    data = dict(BASE)
    data["record_id"] = case_id
    data.update(overrides)
    return data, {"record_id": case_id, "expectation": expectation}


def edge_cases() -> list[tuple[dict, dict]]:
    r = row
    return [
        # --- normalization: making values comparable -----------------------
        r("EDGE-001", "casing folds: WASHINGTON -> Washington",
          location="WASHINGTON", state="dc"),
        r("EDGE-002", "whitespace collapses, value otherwise untouched",
          company_name="  Acme   Holdings   Ltd  "),
        r("EDGE-003", "null tokens become no value; raw string kept verbatim",
          **{"E-Mail": "N/A", "mobile_no": "NULL", "Fax": "-",
             "sector": "(null)", "founded": "unknown"}),
        r("EDGE-004", "explicit multi-value ^^ splits into two observations",
          **{"E-Mail": "sales@acme.com^^billing@acme.com"}),
        r("EDGE-005", "semicolon splits phones, not addresses",
          mobile_no="+1 415 555 0100;+1 415 555 0102",
          house_address="Suite 4; 1720 Wisconsin Ave NW"),
        r("EDGE-006", "international phone keeps its country code",
          mobile_no="+91 (98765)-43210"),
        r("EDGE-007", "bare national number is NOT given a +1 it never had",
          mobile_no="18003514494"),
        r("EDGE-008", "URL lowercases host, keeps path case, drops trailing /",
          web_site="HTTPS://WWW.Example.COM/Careers/"),
        r("EDGE-009", "bare domain is a valid website",
          web_site="meridianhs.com"),
        r("EDGE-010", "money: $1.2M and 1200000 must normalize identically",
          **{"Annual Revenue": "$1.2M", "Total Funding": "1200000"}),
        r("EDGE-011", "Excel serial date decodes to 2023-05-01",
          **{"Last Raised At": "45047"}),
        r("EDGE-012", "ISO timestamp reduces to its day",
          **{"Last Raised At": "2024-09-01T00:00:00+00:00"}),
        r("EDGE-013", "ambiguous dd/mm vs mm/dd is REFUSED, not guessed",
          **{"Last Raised At": "07/28/2025"}),
        r("EDGE-014", "impossible date refused; raw stays visible",
          **{"Last Raised At": "2024-02-31"}),
        r("EDGE-015", "thousands separators and currency words stripped",
          **{"Annual Revenue": "USD 3,181,850,000"}),
        r("EDGE-016", "sub-unit precision dropped: 2,500,000.00 -> 2500000",
          **{"Total Funding": "2,500,000.00"}),
        r("EDGE-017", "leading zero in ZIP survives",
          zip_code="01234"),
        r("EDGE-018", "ZIP+4 kept",
          zip_code="20036-1234"),
        r("EDGE-019", "region name title-cases, short code upper-cases",
          state="california"),
        r("EDGE-020", "address punctuation preserved: #610, Ave NW",
          house_address="#610, 1720 Wisconsin Ave NW"),

        # --- validation: is this usable as the field it was mapped to? -----
        r("EDGE-021", "email syntax error",
          **{"E-Mail": "not-an-email"}),
        r("EDGE-022", "consumer mailbox on a company record -> warning",
          **{"E-Mail": "meridianhs@gmail.com"}),
        r("EDGE-023", "role mailbox -> reported, not rejected",
          **{"E-Mail": "info@meridianhs.com"}),
        r("EDGE-024", "phone with too few digits -> error",
          mobile_no="555 0100"),
        r("EDGE-025", "filler phone 0000000000 -> warning",
          mobile_no="0000000000"),
        r("EDGE-026", "phone that normalizes to nothing -> error",
          mobile_no="ask reception"),
        r("EDGE-027", "employee count 0 -> warning",
          **{"# Employees": "0"}),
        r("EDGE-028", "employee count beyond any employer -> error",
          **{"# Employees": "99000000"}),
        r("EDGE-029", "range input distorts the integer -> warning",
          **{"# Employees": "50-100"}),
        r("EDGE-030", "founded year in the future -> error",
          founded="2099"),
        r("EDGE-031", "founded year before 1600 -> error",
          founded="1420"),
        r("EDGE-032", "negative money -> error",
          **{"Total Funding": "-500000"}),
        r("EDGE-033", "money beyond any company -> error, column is not money",
          **{"Annual Revenue": "99000000000000"}),
        r("EDGE-034", "future funding date -> error",
          **{"Last Raised At": "2099-01-01"}),
        r("EDGE-035", "url with no usable host -> error",
          web_site="http:// /nowhere"),
        r("EDGE-036", "address with lost spacing -> warning, value kept",
          house_address="1720WisconsinAveNW"),
        r("EDGE-037", "address too short to be plausible -> warning",
          house_address="x"),
        r("EDGE-038", "ZIP that lost its leading zero -> warning",
          zip_code="1234"),

        # --- record-level: can this row ever become an entity? -------------
        r("EDGE-039", "no identifier at all -> QUARANTINE",
          company_name="", **{"Legal Name": "", "E-Mail": "", "web_site": "",
                              "record_id": "EDGE-039"}),
        r("EDGE-040", "every column blank -> QUARANTINE",
          **{c: "" for c in COLUMNS if c != "record_id"}),
        r("EDGE-041", "identified only by vendor id -> kept, warned as illegible",
          company_name="", **{"Legal Name": "", "E-Mail": "", "web_site": "",
                              "mobile_no": "", "house_address": ""}),

        # --- lossless capture ----------------------------------------------
        r("EDGE-042", "unmapped columns still captured (Account Owner, internal_score)",
          **{"Account Owner": "devangi@ourcrm.com", "internal_score": "91"}),
        r("EDGE-043", "ambiguous column `location` proposes city but needs review",
          location="Portland, Oregon, United States"),
        r("EDGE-044", "very long technologies value must not break the golden index",
          Technologies=", ".join(f"Technology-{n}" for n in range(260))),
        r("EDGE-045", "unicode and accents survive intact",
          company_name="Hôpital Sainte-Croix Solutions", location="Montréal",
          country="Canada"),
        r("EDGE-046", "quotes and commas inside a quoted CSV field",
          company_name='Smith, Jones & "Partners" LLC'),
        r("EDGE-047", "emoji and control-ish text survive",
          **{"SEO Description": "Care that travels 🚑 — regional hospital network."}),
        r("EDGE-048", "value longer than a btree tuple in a normal text field",
          Keywords=", ".join(f"keyword-{n}" for n in range(400))),
        r("EDGE-049", "leading apostrophe from a spreadsheet export",
          **{"# Employees": "'4200"}),
        r("EDGE-050", "whole row is null tokens except the id",
          **{c: "N/A" for c in COLUMNS if c != "record_id"}),
    ]


def duplicates() -> list[tuple[dict, dict]]:
    """Rows that must land on ONE entity. Different route each time."""
    return [
        row("DUP-001", "same website domain as DUP-002 -> same entity",
            company_name="Northwind Diagnostics", web_site="https://northwindx.com",
            **{"E-Mail": "hello@northwindx.com"}),
        row("DUP-002", "same domain, different spelling of the name",
            company_name="Northwind Diagnostics Inc.", web_site="northwindx.com",
            **{"E-Mail": "support@northwindx.com"}),
        row("DUP-003", "same company email as DUP-001 -> same entity",
            company_name="NORTHWIND DIAGNOSTICS", web_site="",
            **{"E-Mail": "hello@northwindx.com"}),
        row("DUP-004", "identical row to DUP-001 in every field",
            company_name="Northwind Diagnostics", web_site="https://northwindx.com",
            **{"E-Mail": "hello@northwindx.com"}),
    ]


def near_matches() -> list[tuple[dict, dict]]:
    """Rows that must NOT merge automatically. Ambiguity is a human's call."""
    return [
        row("NEAR-001", "similar name, no shared key -> match candidate, not a merge",
            company_name="Summit Ridge Medical", web_site="https://summitridge-med.com",
            **{"E-Mail": "info@summitridge-med.com"}),
        row("NEAR-002", "near-identical name, DIFFERENT domain -> must stay separate",
            company_name="Summit Ridge Medical Group",
            web_site="https://summitridgemedical.org",
            **{"E-Mail": "info@summitridgemedical.org"}),
        row("NEAR-003", "same name, different city and domain -> must stay separate",
            company_name="Summit Ridge Medical", location="Denver", state="CO",
            web_site="https://summitridge.co", **{"E-Mail": "hi@summitridge.co"}),
    ]


def filler(start: int, count: int) -> list[tuple[dict, dict]]:
    """Ordinary rows, so the defects sit in a realistic file rather than alone."""
    out = []
    for i in range(start, start + count):
        out.append(row(
            f"ORD-{i:04d}", "ordinary row, expected to pass cleanly",
            company_name=f"Cascade Clinical Partners {i}",
            **{"Legal Name": f"Cascade Clinical Partners {i}, LLC",
               "E-Mail": f"contact@cascade{i}.com",
               "mobile_no": f"+1 503 555 {i:04d}",
               "Fax": f"+1 503 555 {i + 5000:04d}",
               "web_site": f"https://cascade{i}.com",
               "house_address": f"{i} Burnside Street",
               "location": "Portland", "state": "OR",
               "zip_code": f"{97200 + (i % 99):05d}",
               "# Employees": str(50 + (i * 7) % 4000),
               "founded": str(1950 + (i % 70)),
               "Annual Revenue": str((i % 900 + 1) * 1_000_000),
               "Total Funding": str((i % 50 + 1) * 250_000),
               "Number of Retail Locations": str(i % 40),
               "record_id": f"ORD-{i:04d}",
               "linkedin": f"https://linkedin.com/company/cascade{i}",
               "facebook": f"https://facebook.com/cascade{i}",
               "twitter": f"https://twitter.com/cascade{i}",
               "internal_score": str(i % 100)},
        ))
    return out


def main() -> int:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/inbox")
    outdir.mkdir(parents=True, exist_ok=True)

    rows = edge_cases() + duplicates() + near_matches()
    rows += filler(1, 500 - len(rows))

    data_path = outdir / "edge_cases.csv"
    with data_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(record for record, _ in rows)

    manifest_path = outdir / "edge_cases_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["record_id", "expectation"])
        writer.writeheader()
        writer.writerows(note for _, note in rows if not note["record_id"].startswith("ORD-"))

    print(f"{len(rows)} rows -> {data_path}")
    print(f"{sum(1 for _, n in rows if not n['record_id'].startswith('ORD-'))} "
          f"planted cases -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
