"""Reading a row at a time must yield exactly what reading in batches yields.

Read-ahead is buffering, not granularity. If changing it changed the rows, the
per-record path would be a different pipeline rather than the same pipeline with
a different unit of work.
"""

from ingestion.readers import iter_file, iter_rows


def write_csv(tmp_path, rows: int):
    path = tmp_path / "rows.csv"
    lines = ["id,name,email"]
    lines += [f"{i},Person {i},p{i}@example.com" for i in range(1, rows + 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_one_row_at_a_time(tmp_path):
    path = write_csv(tmp_path, 7)
    rows = list(iter_rows(path, read_ahead=3))
    assert len(rows) == 7
    assert all(isinstance(row, dict) for _, row in rows)


def test_read_ahead_does_not_change_the_rows(tmp_path):
    path = write_csv(tmp_path, 250)
    small = [row for _, row in iter_rows(path, read_ahead=1)]
    large = [row for _, row in iter_rows(path, read_ahead=1000)]
    batched = [row for _, rows in iter_file(path, 64) for row in rows]
    assert small == large == batched


def test_columns_come_back_with_every_row(tmp_path):
    path = write_csv(tmp_path, 4)
    assert {tuple(cols) for cols, _ in iter_rows(path, read_ahead=2)} == {
        ("id", "name", "email")
    }


def test_a_header_only_file_yields_no_rows(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("id,name\n", encoding="utf-8")
    assert list(iter_rows(path)) == []


def test_blank_rows_are_dropped(tmp_path):
    path = tmp_path / "gaps.csv"
    path.write_text("id,name\n1,A\n,\n2,B\n", encoding="utf-8")
    assert len(list(iter_rows(path, read_ahead=2))) == 2
