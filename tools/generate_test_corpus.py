"""Generate a ~20,000-record corpus that can evaluate the whole pipeline.

A single file cannot test this system. Survivorship needs two sources
disagreeing about one company; `source.describes` needs a directory and a
company list holding the same domain and meaning different things by it; the
employer projection needs contact rows. So this writes five files that are
meant to be loaded together, in order, and it records what each planted case is
supposed to prove.

    directory_premises.csv   6,000  company  location      reliability 0.55
    company_master.csv       3,500  company  organisation  reliability 0.90
    contacts_primary.csv     6,500  person   organisation  reliability 0.70
    contacts_partner.csv     3,000  person   organisation  reliability 0.40
    broken_export.csv        1,000  company  organisation  reliability 0.30

Load order is part of the fixture, not an accident. `observed_at` comes from the
batch, so "most recent" means "loaded later" -- the partner file exists to be
the newest and least trusted source at the same time, which is the only way to
tell a most_recent rule from a most_reliable_source one.

Every file uses a different vendor's column names on purpose. Testing with our
own vocabulary would skip the mapping stage, which is the stage most likely to
be wrong about a real file.

    python tools/generate_test_corpus.py [outdir]
"""

import csv
import random
import sys
from pathlib import Path

SEED = 20260818  # fixed: a fixture that moves between runs proves nothing

# Franchise brands for the directory. Every premises of a brand shares one
# domain, which is exactly the case `source.describes = 'location'` exists for:
# the shared domain means same BRAND, never same premises.
BRANDS = [
    ("Sunrise Coffee", "sunrisecoffee.com"), ("QuickLube Auto", "quicklube.com"),
    ("Bella Nails Spa", "bellanails.com"), ("Iron Peak Fitness", "ironpeakfit.com"),
    ("Green Leaf Grocers", "greenleafgrocers.com"), ("PawPrint Vets", "pawprintvets.com"),
    ("Riverstone Dental", "riverstonedental.com"), ("Copper Kettle Diner", "copperkettle.com"),
    ("BrightPath Tutors", "brightpathtutors.com"), ("Summit Cleaners", "summitcleaners.com"),
    ("Harbor Pharmacy", "harborpharmacy.com"), ("Blue Ridge Bakery", "blueridgebakery.com"),
    ("Trailhead Outfitters", "trailheadout.com"), ("Lantern Books", "lanternbooks.com"),
    ("Cobalt Barbers", "cobaltbarbers.com"), ("Meadow Florists", "meadowflorists.com"),
    ("Stonebridge Optical", "stonebridgeoptical.com"), ("Maple & Co Hardware", "mapleco.com"),
    ("Vista Physio", "vistaphysio.com"), ("锦江 Noodle House", "jinjiangnoodle.com"),
]

CITIES = [
    ("Portland", "OR", "97201"), ("Austin", "TX", "78701"), ("Denver", "CO", "80202"),
    ("Boston", "MA", "02108"), ("Chicago", "IL", "60601"), ("Seattle", "WA", "98101"),
    ("Miami", "FL", "33101"), ("Phoenix", "AZ", "85001"), ("Nashville", "TN", "37201"),
    ("Detroit", "MI", "48201"), ("Newark", "NJ", "07101"), ("Atlanta", "GA", "30301"),
]

INDUSTRIES = ["Hospital & Health Care", "Information Technology", "Construction",
              "Retail", "Financial Services", "Logistics", "Education",
              "Food & Beverages", "Real Estate", "Manufacturing"]

TECHNOLOGIES = ["Microsoft Office 365", "Drupal", "Salesforce", "Akamai", "VueJS",
                "Amazon SES", "Cloudflare", "HubSpot", "Shopify", "Zendesk"]

FIRST_NAMES = ["Aruna", "Devangi", "Mateo", "Yusuf", "Priya", "Rowan", "Ingrid",
               "Kwame", "Sofia", "Tobias", "Leilani", "Hiroshi", "Amara", "Niall"]
LAST_NAMES = ["Rodrigues", "Okonkwo", "Fitzgerald", "Nakamura", "Beaumont", "Silva",
              "Haddad", "Lindqvist", "Mwangi", "Castellanos", "Oyelaran", "Novak"]
TITLES = ["Chief Executive Officer", "VP Engineering", "Head of Operations",
          "Procurement Manager", "Clinical Director", "Regional Sales Lead"]

# The companies that appear in more than one file. These are where survivorship
# is actually tested: same company, three sources, deliberately different
# answers, and a rule per field that says which answer should win.
CONTESTED = 120


def _companies(rng, n):
    """The master company list. Index 0..CONTESTED-1 are the contested ones."""
    out = []
    for i in range(n):
        city, state, zipc = CITIES[i % len(CITIES)]
        out.append({
            "idx": i,
            "name": f"{['Northwind','Cascade','Meridian','Arclight','Fernbrook','Halcyon'][i % 6]} "
                    f"{['Diagnostics','Logistics','Systems','Partners','Industries','Labs'][(i // 6) % 6]} {i}",
            "domain": f"company{i}.example.com",
            "city": city, "state": state, "zip": zipc,
            "industry": INDUSTRIES[i % len(INDUSTRIES)],
            "employees": 40 + (i * 13) % 9000,
            "founded": 1950 + (i % 70),
            "revenue": (1 + i % 800) * 1_000_000,
            "street": f"{100 + i} {['Burnside','Elm','Harbor','Chestnut'][i % 4]} Street",
        })
    return out


# --------------------------------------------------------------------------
# 1. the directory: many premises, one domain per brand


def directory_premises(rng, rows):
    cols = ["listing_id", "business_name", "phone_no", "web", "address", "town",
            "state", "zip", "category", "rating", "search_keyword"]
    out, notes = [], []
    for i in range(rows):
        brand, domain = BRANDS[i % len(BRANDS)]
        city, state, zipc = CITIES[(i // len(BRANDS)) % len(CITIES)]
        out.append({
            "listing_id": f"LST-{i:06d}",
            "business_name": f"{brand} #{i % 900:03d}",
            # A per-premises number: the thing that genuinely distinguishes them.
            "phone_no": f"+1 {200 + i % 700} 555 {i % 10000:04d}",
            "web": domain,                       # SHARED across every premises
            "address": f"{10 + i % 9000} {['Main','Oak','Pine','Cedar'][i % 4]} Street",
            "town": city, "state": state, "zip": zipc,
            "category": brand.split()[-1],
            "rating": f"{3 + (i % 20) / 10:.1f}",
            "search_keyword": brand.lower().replace(" ", "-"),
        })
    for brand, domain in BRANDS:
        notes.append({
            "case": f"LOCATION::{domain}",
            "expectation": f"every {brand} premises shares {domain}; as a 'location' "
                           "source the domain must NOT merge them into one entity",
        })
    return cols, out, notes


# --------------------------------------------------------------------------
# 2. the company master: one row per company, domain identifies the company


def company_master(rng, companies):
    cols = ["org_id", "org_name", "domain", "email", "telephone", "street_address",
            "city", "state", "postcode", "country", "sector", "employees",
            "founded", "revenue", "linkedin"]
    out = []
    for c in companies:
        out.append({
            "org_id": f"ORG-{c['idx']:06d}",
            "org_name": c["name"],
            "domain": c["domain"],
            "email": f"info@{c['domain']}",
            "telephone": f"+1 415 555 {c['idx'] % 10000:04d}",
            "street_address": c["street"],
            "city": c["city"], "state": c["state"], "postcode": c["zip"],
            "country": "United States",
            "sector": c["industry"],
            "employees": str(c["employees"]),
            # EARLIEST wins for founded_year, and this file is loaded first --
            # so this value is the one that must survive.
            "founded": str(c["founded"]),
            "revenue": str(c["revenue"]),
            "linkedin": f"https://linkedin.com/company/company{c['idx']}",
        })
    # The brand HQs, so a directory domain also exists in an 'organisation'
    # source. The premises must still not merge with each other.
    for brand, domain in BRANDS:
        out.append({
            "org_id": f"ORG-BRAND-{domain}", "org_name": f"{brand} Holdings",
            "domain": domain, "email": f"hq@{domain}",
            "telephone": "+1 415 555 0000", "street_address": "1 Corporate Plaza",
            "city": "Chicago", "state": "IL", "postcode": "60601",
            "country": "United States", "sector": "Retail",
            "employees": "5000", "founded": "1988", "revenue": "250000000",
            "linkedin": f"https://linkedin.com/company/{domain.split('.')[0]}",
        })
    return cols, out


# --------------------------------------------------------------------------
# 3. contacts, Apollo-shaped: person rows carrying their employer


def contacts_primary(rng, companies, rows):
    cols = ["Apollo Contact Id", "First Name", "Last Name", "Email", "Title",
            "Seniority", "Departments", "Person Linkedin Url", "Mobile Phone",
            "City", "State", "Country", "Company", "Company Domain",
            "Company City", "Company State", "# Employees", "Industry",
            "Annual Revenue", "Total Funding", "Latest Funding",
            "Latest Funding Amount", "Last Raised At", "Technologies", "Keywords",
            "SEO Description", "Number of Retail Locations", "Email Sent", "Stage"]
    out, notes = [], []
    for i in range(rows):
        c = companies[i % len(companies)]
        first = FIRST_NAMES[i % len(FIRST_NAMES)]
        last = LAST_NAMES[(i // len(FIRST_NAMES)) % len(LAST_NAMES)]
        out.append({
            "Apollo Contact Id": f"APL-{i:06d}",
            "First Name": first, "Last Name": last,
            "Email": f"{first.lower()}.{last.lower()}{i}@{c['domain']}",
            "Title": TITLES[i % len(TITLES)],
            "Seniority": ["c_suite", "vp", "director", "manager"][i % 4],
            "Departments": ["engineering", "operations", "sales", "finance"][i % 4],
            "Person Linkedin Url": f"https://linkedin.com/in/{first.lower()}-{last.lower()}-{i}",
            "Mobile Phone": f"+1 503 555 {i % 10000:04d}",
            "City": c["city"], "State": c["state"], "Country": "United States",
            "Company": c["name"], "Company Domain": c["domain"],
            "Company City": c["city"], "Company State": c["state"],
            # Same employer facts as the master file: agreement should RAISE
            # confidence rather than look like a conflict.
            "# Employees": str(c["employees"]),
            "Industry": c["industry"],
            "Annual Revenue": str(c["revenue"]),
            "Total Funding": str((1 + i % 40) * 250_000),
            "Latest Funding": ["Series A", "Series B", "Debt Financing", "Seed"][i % 4],
            "Latest Funding Amount": str((1 + i % 20) * 1_000_000),
            # Half ISO, half Excel serial: the same field, two vendor spellings.
            "Last Raised At": (f"20{18 + i % 7}-0{1 + i % 9}-15" if i % 2
                               else str(43000 + (i % 2000))),
            "Technologies": ", ".join(TECHNOLOGIES[: 3 + i % 6]),
            "Keywords": ", ".join([c["industry"].lower(), "b2b", f"segment-{i % 30}"]),
            "SEO Description": f"{c['name']} serves the {c['industry'].lower()} sector.",
            "Number of Retail Locations": str(i % 60),
            "Email Sent": "true" if i % 3 else "false",   # CRM state: must stay unmapped
            "Stage": ["Cold", "Warm", "Contacted"][i % 3],
        })
    notes.append({
        "case": "EMPLOYER-PROJECTION",
        "expectation": "employer columns on a person row must land on the COMPANY "
                       "entity, and reach the same entity the master file created",
    })
    notes.append({
        "case": "CRM-COLUMNS-UNMAPPED",
        "expectation": "'Email Sent' and 'Stage' describe the vendor's CRM, not the "
                       "company, and must stay unmapped",
    })
    return cols, out, notes


# --------------------------------------------------------------------------
# 4. the partner file: newest, least trusted, and deliberately disagrees


def contacts_partner(rng, companies, rows):
    cols = ["contact_ref", "full name", "e_mail", "designation", "cell",
            "employer", "company_domain", "company_city", "company_employees",
            "company_founded", "company_industry", "company_revenue"]
    out, notes = [], []
    for i in range(rows):
        c = companies[i % CONTESTED]         # only the contested companies
        first = FIRST_NAMES[i % len(FIRST_NAMES)]
        last = LAST_NAMES[(i // len(FIRST_NAMES)) % len(LAST_NAMES)]
        out.append({
            "contact_ref": f"PTR-{i:06d}",
            "full name": f"{first} {last}",
            # Same people as the primary file, so the person entities merge.
            "e_mail": f"{first.lower()}.{last.lower()}{i}@{c['domain']}",
            # MOST_RECENT for job_title: this file is loaded last, so THIS wins.
            "designation": "Chief Transformation Officer",
            "cell": f"+1 503 555 {i % 10000:04d}",
            "employer": c["name"],
            "company_domain": c["domain"],
            # MOST_RECENT for city: loaded last, so this wins despite low trust.
            "company_city": "Reno",
            # MOST_RECENT for employee_count: this wins too.
            "company_employees": str(c["employees"] + 500),
            # EARLIEST for founded_year: the master file was first, so this
            # LOSES even though it arrived later. The two rules must not agree
            # by accident, which is why these disagree in opposite directions.
            "company_founded": str(c["founded"] + 12),
            # MOST_RELIABLE_SOURCE for industry: master (0.90) must beat this.
            "company_industry": "Miscellaneous",
            "company_revenue": str(c["revenue"] + 7_000_000),
        })
    notes += [
        {"case": "SURVIVORSHIP::job_title(most_recent)",
         "expectation": "partner file loaded last -> 'Chief Transformation Officer' wins"},
        {"case": "SURVIVORSHIP::city(most_recent)",
         "expectation": "partner loaded last -> company city becomes 'Reno' despite 0.40 trust"},
        {"case": "SURVIVORSHIP::founded_year(earliest)",
         "expectation": "master loaded first -> its founded year wins, partner's +12 loses"},
        {"case": "SURVIVORSHIP::industry(most_reliable_source)",
         "expectation": "master at 0.90 beats partner at 0.40 -> NOT 'Miscellaneous'"},
        {"case": "SURVIVORSHIP::company_name(most_frequent)",
         "expectation": "master + primary agree on the name, partner agrees too -> unanimous"},
    ]
    return cols, out, notes


# --------------------------------------------------------------------------
# 5. the broken export: bad input must degrade, not fail


def broken_export(rng, rows):
    cols = ["id", "company", "Phone", "email", "site", "addr", "city", "st",
            "zip", "staff", "since", "notes"]
    out, notes = [], []
    for i in range(rows):
        city, state, zipc = CITIES[i % len(CITIES)]
        broken_phone = i % 10 != 0          # 90% junk, like a real c_suite export
        row = {
            "id": f"BRK-{i:05d}",
            "company": f"Fallbrook Trading {i}",
            "Phone": "[object Object]" if broken_phone else f"+1 212 555 {i % 10000:04d}",
            "email": f"contact@fallbrook{i}.example.com",
            "site": f"fallbrook{i}.example.com",
            "addr": f"{i} Canal Street",
            "city": city, "st": state, "zip": zipc,
            "staff": f"{10 + i % 400}-{500 + i % 400}" if i % 7 == 0 else str(10 + i % 900),
            "since": ["1998", "N/A", "unknown", "--", "2015"][i % 5],
            "notes": ["", "n/a", "see attachment", "NULL"][i % 4],
        }
        if i % 97 == 0:                     # a wholly blank row, ids aside
            row = {c: "" for c in cols} | {"id": f"BRK-{i:05d}"}
        if i % 89 == 0:                     # values shifted one column left
            row["city"], row["st"], row["zip"] = row["st"], row["zip"], row["city"]
        out.append(row)
    notes += [
        {"case": "BROKEN::phone-column",
         "expectation": "90% of Phone is '[object Object]'; those rows must still "
                        "resolve on email and website, and must NOT be quarantined"},
        {"case": "BROKEN::staff-ranges",
         "expectation": "'10-500' must be refused as value.range_given, not stored as 10500"},
        {"case": "BROKEN::shifted-columns",
         "expectation": "state in the city column: captured, flagged, never fatal"},
        {"case": "BROKEN::blank-rows",
         "expectation": "rows carrying only an id keep the id and warn as illegible"},
    ]
    return cols, out, notes


def write(path: Path, cols: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {len(rows):>6} rows  {path.name}")


def main() -> int:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/inbox/corpus")
    outdir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    notes: list[dict] = []

    companies = _companies(rng, 3500)

    cols, rows, n = directory_premises(rng, 6000)
    write(outdir / "directory_premises.csv", cols, rows)
    notes += n

    cols, rows = company_master(rng, companies)
    write(outdir / "company_master.csv", cols, rows)

    cols, rows, n = contacts_primary(rng, companies, 6500)
    write(outdir / "contacts_primary.csv", cols, rows)
    notes += n

    cols, rows, n = contacts_partner(rng, companies, 3000)
    write(outdir / "contacts_partner.csv", cols, rows)
    notes += n

    cols, rows, n = broken_export(rng, 1000)
    write(outdir / "broken_export.csv", cols, rows)
    notes += n

    manifest = outdir / "corpus_manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["case", "expectation"])
        writer.writeheader()
        writer.writerows(notes)
    print(f"  {len(notes):>6} planted expectations  {manifest.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
