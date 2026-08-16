"""Generate a 20,000-row company file with defects planted at known intervals.

The intervals matter: a rule that fires 645 times against a defect planted every
31st row is doing what it claims. A rule that fires an unpredictable number of
times is not evidence of anything.
"""

from pathlib import Path

COLUMNS = ["company_name", "email", "phone", "website", "street_address",
           "city", "postal_code", "employee_count", "founded_year", "description"]

ROWS = 20_000
FILLER_PHONE_EVERY = 31     # phone.filler
LOST_SPACING_EVERY = 53     # address.spacing


def main() -> None:
    lines = [",".join(COLUMNS)]
    for i in range(1, ROWS + 1):
        phone = "0000000000" if i % FILLER_PHONE_EVERY == 0 else f"+1 555 {i:05d}"
        street = (f"{i}WisconsinAveNW" if i % LOST_SPACING_EVERY == 0
                  else f"{i} Wisconsin Ave NW")
        email = f"info{i}@org{i}.com" if i % 7 else "N/A"
        website = f"https://org{i}.com" if i % 5 else ""
        postal = "00501" if i % 3 == 0 else f"{10000 + (i % 80000)}"
        lines.append(
            f'"Org {i}","{email}","{phone}","{website}","{street}",'
            f'"City {i % 200}","{postal}","{(i % 900) + 1}","{1950 + (i % 70)}","desc {i}"'
        )

    path = Path("/data/inbox/_scale20k.csv")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {ROWS:,} rows to {path}")
    print(f"  phone.filler planted every {FILLER_PHONE_EVERY} rows "
          f"-> expect {ROWS // FILLER_PHONE_EVERY}")
    print(f"  address.spacing planted every {LOST_SPACING_EVERY} rows "
          f"-> expect {ROWS // LOST_SPACING_EVERY}")


if __name__ == "__main__":
    main()
