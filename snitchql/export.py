"""SnitchQL export helpers: Table -> CSV / JSON.

Pure stdlib (csv, json) so the read/export path has zero heavy dependencies.
"""
import csv
import json
from typing import Iterable, TextIO


def _row_to_dict(table, row: dict) -> dict:
    """Return an ordered dict of column_name -> value for one decoded row."""
    out = {}
    for col in table.columns:
        out[col.name] = row.get(col.name)
    return out


def export_csv(table, stream: TextIO, rows: Iterable[dict] = None) -> None:
    """Write rows (or all rows) to an open text stream as CSV."""
    rows = list(rows) if rows is not None else table.rows
    if not table.columns:
        return
    writer = csv.writer(stream)
    writer.writerow([c.name for c in table.columns])
    for row in rows:
        d = _row_to_dict(table, row)
        writer.writerow(["" if v is None else v for v in d.values()])


def export_json(table, stream: TextIO, rows: Iterable[dict] = None,
                indent: int = 2) -> None:
    """Write rows (or all rows) to an open text stream as JSON."""
    rows = list(rows) if rows is not None else table.rows
    data = [_row_to_dict(table, r) for r in rows]
    json.dump(data, stream, indent=indent, default=str)


def rows_to_csv_string(table, rows: Iterable[dict] = None) -> str:
    import io
    buf = io.StringIO()
    export_csv(table, buf, rows)
    return buf.getvalue()


def rows_to_json_string(table, rows: Iterable[dict] = None) -> str:
    import io
    buf = io.StringIO()
    export_json(table, buf, rows)
    return buf.getvalue()
