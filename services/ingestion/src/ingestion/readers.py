"""Read source files without altering what they say.

Every value is read as a string. Type inference is disabled everywhere: it is
what turns 00501 into 501, long IDs into scientific notation, and phone numbers
into floats. Ingestion's whole job is to record the source verbatim.
"""

import csv
from collections.abc import Iterator
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
    """Read a whole file into memory. Prefer `iter_file` for anything large."""
    suffix = path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        return read_csv(path)
    if suffix in EXCEL_SUFFIXES:
        return read_excel(path)
    raise UnsupportedFileType(
        f"{path.name}: expected one of {sorted(CSV_SUFFIXES | EXCEL_SUFFIXES)}"
    )


DEFAULT_BATCH_SIZE = 5000


def iter_csv(path: Path, batch_size: int) -> Iterator[tuple[list[str], list[Row]]]:
    """Stream a CSV in batches, so peak memory is a batch and not the file.

    Polars reads in its own internal chunks and we regroup them to the requested
    size, so a caller asking for 5,000 gets 5,000 regardless of how the reader
    decided to split the file.
    """
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    columns = disambiguate(_csv_header(path, delimiter))
    if not columns:
        return

    # scan_csv is lazy: rows are pulled from disk as batches are consumed, so
    # peak memory is a batch rather than the file. Every option here mirrors
    # read_csv exactly — batching must not change what is read.
    lazy = pl.scan_csv(
        path,
        has_header=False,
        skip_rows=1,
        new_columns=columns,
        separator=delimiter,
        infer_schema=False,
        truncate_ragged_lines=False,
        quote_char='"',
    )

    pending: list[Row] = []
    try:
        for frame in lazy.collect_batches(chunk_size=batch_size):
            # Regrouped, because the reader chooses its own chunk boundaries and
            # a caller asking for 5,000 should get 5,000.
            pending.extend(_frame_to_rows(frame))
            while len(pending) >= batch_size:
                yield columns, pending[:batch_size]
                pending = pending[batch_size:]
    except pl.exceptions.NoDataError:
        # A header with no rows under it. An empty export is a normal thing for
        # a vendor to send, and it means zero records — not a failure.
        return
    if pending:
        yield columns, pending


def iter_excel(path: Path, batch_size: int) -> Iterator[tuple[list[str], list[Row]]]:
    """Batch an Excel file after reading it.

    Unlike CSV there is no streaming path: the format is a zip archive whose
    rows cannot be read without decompressing the sheet, so the workbook is
    necessarily resident. Batching still bounds the size of each transaction and
    keeps the write path identical to CSV — but peak memory here is the file,
    and that limit is real rather than an oversight.
    """
    columns, rows = read_excel(path)
    for start in range(0, len(rows), batch_size):
        yield columns, rows[start : start + batch_size]


def iter_file(
    path: Path, batch_size: int = DEFAULT_BATCH_SIZE
) -> Iterator[tuple[list[str], list[Row]]]:
    """Yield (columns, rows) a batch at a time. Empty files yield nothing."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    suffix = path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        yield from iter_csv(path, batch_size)
    elif suffix in EXCEL_SUFFIXES:
        yield from iter_excel(path, batch_size)
    else:
        raise UnsupportedFileType(
            f"{path.name}: expected one of {sorted(CSV_SUFFIXES | EXCEL_SUFFIXES)}"
        )


def iter_rows(
    path: Path, read_ahead: int = DEFAULT_BATCH_SIZE
) -> Iterator[tuple[list[str], Row]]:
    """Yield (columns, row) one row at a time.

    `read_ahead` is how much is pulled off disk per read, and it is not the
    processing granularity: the caller still gets exactly one row at a time.
    Reading a single row per syscall would be slower with no benefit to anyone —
    how much the disk hands over in one go is not a fact about the data, whereas
    how much is processed at once decides what fails together.
    """
    for columns, rows in iter_file(path, read_ahead):
        for row in rows:
            yield columns, row
