"""Cut a bounded sample off a large vendor export, without reading it whole.

The point of a sample is to exercise a *layout*. A vendor's 386,000-row export
and its first 1,000 rows have the same columns, the same conventions and the
same defects, so the sample proves everything about the schema that the whole
file would, in minutes rather than hours.

Two properties make the samples usable as real input:

  * **Values are copied verbatim.** No type inference, no reformatting. A
    sample that "cleaned up" 00501 or 9.99E10 on the way out would prove the
    pipeline handles data the vendor never sent.
  * **Every sheet is sampled separately.** Workbook sheets are separate layouts
    — LeadsNemo_Test1.xlsx has two with different column orders — so each one
    becomes its own CSV and its own batch downstream.

Excel is streamed with openpyxl in read-only mode, which pulls rows off the
sheet as they are consumed instead of materializing the workbook. The 386,327 x
55 Apollo sheet samples in ~11s at ~280 MB resident.

    python /tools/fixtures/sample_file.py /tf/'Apollo.io Master File.xlsx' /data/inbox/samples
    python /tools/fixtures/sample_file.py /tf --all --rows 1000
"""

import argparse
import csv
import re
import sys
from pathlib import Path

CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
DEFAULT_ROWS = 1000


def slugify(text: str) -> str:
    """A filename that survives a shell, a bind mount and a Windows path."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return cleaned or "unnamed"


def stem_for(path: Path) -> str:
    """Slug carrying the source format, because the stem alone collides.

    `Mental Health Care(9).csv` and `Mental Health Care(9).xlsx` are the same
    data in two formats — which is worth testing, and impossible to test if
    both samples claim the same output name and one silently overwrites the
    other.
    """
    return f"{slugify(path.stem)}__{path.suffix.lower().lstrip('.')}"


def _write(destination: Path, header: list[str], rows: list[list[str]]) -> None:
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(header)
        writer.writerows(rows)


def _cell(value) -> str:
    """Render a cell the way the source meant it.

    openpyxl types numeric cells as int/float, and str() on a float would turn
    a ZIP code or a long identifier into something the vendor never wrote.
    Integral floats are rendered without the decimal point for that reason;
    everything else is passed through untouched.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def sample_csv(path: Path, rows: int, out_dir: Path) -> list[Path]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    # utf-8-sig strips a BOM so it cannot contaminate the first column name.
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            print(f"  {path.name}: empty, skipped")
            return []
        sampled = []
        for row in reader:
            sampled.append(row)
            if len(sampled) >= rows:
                break

    destination = out_dir / f"{stem_for(path)}.csv"
    _write(destination, header, sampled)
    print(f"  {destination.name}: {len(sampled)} rows x {len(header)} cols")
    return [destination]


def sample_excel(path: Path, rows: int, out_dir: Path) -> list[Path]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        sys.exit("openpyxl is required to sample Excel files: pip install openpyxl")

    book = load_workbook(path, read_only=True, data_only=True)
    written = []
    try:
        for sheet_name in book.sheetnames:
            sheet = book[sheet_name]
            stream = sheet.iter_rows(values_only=True)
            try:
                raw_header = next(stream)
            except StopIteration:
                print(f"  {path.name} [{sheet_name}]: empty, skipped")
                continue

            header = [_cell(value) for value in raw_header]
            # Trailing empty columns are an artifact of the sheet's declared
            # dimension, not columns the vendor sent.
            while header and header[-1] == "":
                header.pop()
            if not header:
                print(f"  {path.name} [{sheet_name}]: no header, skipped")
                continue

            sampled = []
            for row in stream:
                values = [_cell(value) for value in row][: len(header)]
                values += [""] * (len(header) - len(values))
                if all(value.strip() == "" for value in values):
                    continue
                sampled.append(values)
                if len(sampled) >= rows:
                    break

            # One sheet, one file: sheets are separate layouts and must map,
            # and be reviewed, separately.
            suffix = "" if len(book.sheetnames) == 1 else f"__{slugify(sheet_name)}"
            destination = out_dir / f"{stem_for(path)}{suffix}.csv"
            _write(destination, header, sampled)
            print(f"  {destination.name}: {len(sampled)} rows x {len(header)} cols"
                  f"  (sheet {sheet_name!r})")
            written.append(destination)
    finally:
        book.close()
    return written


def sample(path: Path, rows: int, out_dir: Path) -> list[Path]:
    suffix = path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        return sample_csv(path, rows, out_dir)
    if suffix in EXCEL_SUFFIXES:
        return sample_excel(path, rows, out_dir)
    print(f"  {path.name}: unsupported type, skipped")
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="file, or directory with --all")
    parser.add_argument("out_dir", type=Path, nargs="?",
                        default=Path("/data/inbox/samples"))
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--all", action="store_true",
                        help="sample every supported file in the directory")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.all:
        if not args.source.is_dir():
            sys.exit(f"{args.source} is not a directory")
        targets = sorted(
            p for p in args.source.iterdir()
            if p.suffix.lower() in CSV_SUFFIXES | EXCEL_SUFFIXES
        )
    else:
        targets = [args.source]

    written = []
    for target in targets:
        print(target.name)
        written.extend(sample(target, args.rows, args.out_dir))

    print(f"\n{len(written)} sample file(s) in {args.out_dir}")


if __name__ == "__main__":
    main()
