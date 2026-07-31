"""SnitchQL query/filter layer.

Field-based predicates with AND/OR combination. Kept intentionally simple
(no SQL parser yet) so the GUI can build filters from widgets and the CLI
can express them as repeated --where FIELD OP VALUE.
"""
import operator
from typing import Callable, List, Tuple

# Single source of truth for supported operators. Order matters: it drives the
# GUI operator dropdown so the UI can never drift from what the engine handles.
OPERATORS = ["eq", "ne", "contains", "startswith", "endswith", "gt", "lt", "gte", "lte"]

_DISPLAY = {
    "eq": "=",
    "ne": "≠",
    "contains": "contains",
    "startswith": "starts with",
    "endswith": "ends with",
    "gt": ">",
    "lt": "<",
    "gte": "≥",
    "lte": "≤",
}

# Supported operators -> python predicate builder
_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "contains": lambda a, b: b in (a or ""),
    "startswith": lambda a, b: (a or "").startswith(b),
    "endswith": lambda a, b: (a or "").endswith(b),
    "gt": lambda a, b: a is not None and a > b,
    "lt": lambda a, b: a is not None and a < b,
    "gte": lambda a, b: a is not None and a >= b,
    "lte": lambda a, b: a is not None and a <= b,
}

OPERATOR_LABELS = _DISPLAY


def normalize_rules(rules):
    """Filter raw GUI/CLI rules -> valid (field, op, value) tuples.

    Drops rules with a blank field or blank value, and unknown operators.
    Accepts (field, op, value) as tuple or 3-list. Returns a list of tuples.
    """
    out = []
    for r in rules or []:
        try:
            field, op, value = r[0], r[1], r[2]
        except (TypeError, ValueError, IndexError):
            continue
        if not field or not str(value).strip():
            continue
        if op not in _OPS:
            continue
        out.append((field, op, value))
    return out


def coerce(value, target):
    """Try to coerce a string filter value to the type of the target cell."""
    if target is None:
        return value
    if isinstance(target, bool):
        return value.lower() in ("true", "1", "yes")
    if isinstance(target, (int, float)):
        try:
            return type(target)(value)
        except (ValueError, TypeError):
            return value
    return value


def make_predicate(field: str, op: str, value: str):
    if op not in _OPS:
        raise ValueError(f"unknown operator {op!r}; supported: {sorted(_OPS)}")
    fn = _OPS[op]

    # case-insensitive string matching for contains/startswith/endswith
    text_ops = ("contains", "startswith", "endswith")
    ci = op in text_ops

    def pred(row: dict):
        cell = row.get(field)
        if cell is None:
            return op == "eq" and value in ("", "None", "null")
        target = cell
        v = value
        if ci and isinstance(cell, str):
            target = cell.lower()
            v = value.lower()
        return fn(target, coerce(v, cell))

    return pred


def apply_filters(rows: List[dict], filters, combine="AND") -> List[dict]:
    """filters: list of (field, op, value) tuples. combine: AND | OR."""
    if not filters:
        return rows
    preds = [make_predicate(f, o, v) for (f, o, v) in filters]
    out = []
    for row in rows:
        results = [p(row) for p in preds]
        if combine == "OR":
            if any(results):
                out.append(row)
        else:
            if all(results):
                out.append(row)
    return out
