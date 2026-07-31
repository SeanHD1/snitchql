#!/usr/bin/env python3
"""SnitchQL CLI - read, inspect, query and export DBISAM .dat tables.

Examples:
  snitchql path/to/STAFF.dat                         # show schema + row count
  snitchql STAFF.dat --dump 5                        # dump first 5 rows
  snitchql STAFF.dat --where SURNAME contains SMITH --where STATUS eq ACTIVE
  snitchql STAFF.dat --csv out.csv                   # export all -> CSV
  snitchql STAFF.dat --json out.json --where ACCESS eq 99
  snitchql STAFF.dat --or --where STATUS eq RESIGNED --where STATUS eq ACTIVE
"""
import argparse
import sys
from pathlib import Path

from snitchql.reader import read_table
from snitchql import export
from snitchql.query import apply_filters


def _print_schema(t):
    print(f"Table : {t.path}")
    print(f"  user_version : {t.user_version}")
    print(f"  description  : {t.description!r}")
    print(f"  total_rows   : {t.total_rows}  (live {len(t.rows)}, deleted {t.deleted_rows})")
    print(f"  row_size     : {t.row_size}")
    print(f"  columns      : {len(t.columns)}")
    print("  schema:")
    for c in t.columns:
        print(f"    {c.index:>2} {c.name:<18} {c.type_name:<10} len={c.length} off={c.row_offset}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="snitchql", description="DBISAM .dat reader/export tool")
    p.add_argument("dat", help="path to a DBISAM .dat file")
    p.add_argument("--dump", type=int, metavar="N", help="print first N rows")
    p.add_argument("--where", nargs=3, action="append", default=[],
                   metavar=("FIELD", "OP", "VALUE"),
                   help="filter FIELD OP VALUE (repeatable). OPs: eq ne contains "
                        "startswith endswith gt lt gte lte")
    p.add_argument("--or", dest="combine", action="store_const", const="OR", default="AND",
                   help="combine --where with OR instead of AND")
    p.add_argument("--csv", metavar="FILE", help="export matching rows to CSV")
    p.add_argument("--json", metavar="FILE", help="export matching rows to JSON")
    p.add_argument("--limit", type=int, default=None, help="limit rows processed/exported")
    args = p.parse_args(argv)

    path = Path(args.dat)
    if not path.exists():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 2

    t = read_table(str(path))
    _print_schema(t)

    # apply filters
    filters = [(f, o, v) for (f, o, v) in args.where]
    rows = apply_filters(t.rows, filters, combine=args.combine)
    if args.limit is not None:
        rows = rows[: args.limit]

    if args.dump:
        print(f"\nFirst {min(args.dump, len(rows))} rows (after filters):")
        for r in rows[: args.dump]:
            d = {c.name: r.get(c.name) for c in t.columns}
            print("  ", d)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            export.export_csv(t, f, rows)
        print(f"\nExported {len(rows)} rows -> {args.csv}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            export.export_json(t, f, rows)
        print(f"\nExported {len(rows)} rows -> {args.json}")

    if not (args.dump or args.csv or args.json):
        print(f"\n(use --dump N, --csv FILE, --json FILE, or --where FIELD OP VALUE to inspect data)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
