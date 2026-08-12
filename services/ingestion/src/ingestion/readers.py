"""Read source files without altering what they say.

Every value is read as a string. Type inference is disabled everywhere: it is
what turns 00501 into 501, long IDs into scientific notation, and phone numbers
into floats. Ingestion's whole job is to record the source verbatim.
"""

import csv
from pathlib import Path

import polars as pl

CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}

Row = dict[str, str | None]


class UnsupportedFileType(Exception):
    pass


def disambiguate(names: list[str]) -> list[str]:
    """Make column names unique deterministically: name, name__2, name__3."""
    seen: dict[str, int] = {}
    result = []
    for raw in names:
        name = raw.strip() or "unnamed"
        seen[name] = seen.get(name, 0) + 1
        result.append(name if seen[name] == 1 else f"{name}__{seen[name]}")
    return result


def _csv_header(path: Path, delimiter: str) -> list[str]:
    # utf-8-sig strips a BOM if present so it doesn't contaminate the first name.
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle, delimiter=delimiter):
            return row
    return []


def _frame_to_rows(frame: pl.DataFrame) -> list[Row]:
    rows: list[Row] = []
    for row in frame.iter_rows(named=True):
        # Drop rows that are entirely empty — they carry no observation.
        if all(value is None or str(value).strip() == "" for value in row.values()):
            continue
        rows.append({key: value for key, value in row.items()})
    return rows


def read_csv(path: Path) -> tuple[list[str], list[Row]]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    columns = disambiguate(_csv_header(path, delimiter))
    if not columns:
        return [], []

    frame = pl.read_csv(
        path,
        has_header=False,
        skip_rows=1,
        new_columns=columns,
        separator=delimiter,
        infer_schema=False,
        truncate_ragged_lines=False,
        quote_char='"',
    )
    return columns, _frame_to_rows(frame)


def read_excel(path: Path) -> tuple[list[str], list[Row]]:
    # infer_schema_length=0 makes every column pl.String.
    frame = pl.read_excel(path, engine="calamine", infer_schema_length=0)
    columns = disambiguate(list(frame.columns))
    frame.columns = columns
    return columns, _frame_to_rows(frame)


def read_file(path: Path) -> tuple[list[str], list[Row]]:
    suffix = path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        return read_csv(path)
    if suffix in EXCEL_SUFFIXES:
        return read_excel(path)
    raise UnsupportedFileType(
        f"{path.name}: expected one of {sorted(CSV_SUFFIXES | EXCEL_SUFFIXES)}"
    )
