"""Minimal, safe SQL engine for SnitchQL (Single View custom queries).

The DBISAM reader already decodes a whole table into a list of dicts
(``Table.rows``). This module runs a *small* SQL subset over those rows in pure
Python — no external DB, no eval, no code execution. The parser is a hand-written
recursive-descent scanner, so a malicious or malformed query can only raise a
``SqlError``; it can never touch the filesystem or the interpreter.

Supported grammar (case-insensitive keywords)::

    SELECT <*> | <col> [, <col> ...]
    FROM   <any-identifier>            -- ignored: queries run on the loaded pane
    [WHERE <condition> [AND|OR <condition> ...]]   -- parentheses allowed
    [ORDER BY <col> [ASC|DESC]]
    [LIMIT <n>]

    <condition> := <col> <op> <value>
    <op>        := = | != | <> | > | < | >= | <= | LIKE
    <value>     := 'string' | "string" | number | bareword

The result is a ``QueryResult`` — a lightweight object with the same
``.columns`` / ``.rows`` / ``.total_rows`` shape ``RowTableModel`` expects, so
the GUI can display it through the existing virtual table with zero special-casing.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Pattern
import re


class SqlError(Exception):
    """Raised for any invalid query (parse or semantic)."""


@dataclass
class _Tok:
    kind: str          # 'kw' 'ident' 'num' 'str' 'op' 'punct' 'star' 'eof'
    val: str


_KEYWORDS = {"SELECT", "FROM", "WHERE", "ORDER", "BY", "ASC", "DESC",
             "LIMIT", "AND", "OR"}
_OPS = {"=", "!=", "<>", ">", "<", ">=", "<=", "LIKE"}


def _tokenize(sql: str) -> List[_Tok]:
    toks: List[_Tok] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "*":
            toks.append(_Tok("star", "*"))
            i += 1
            continue
        if c in "(),;":
            toks.append(_Tok("punct", c))
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            j = i + 1
            buf = []
            while j < n and sql[j] != quote:
                if sql[j] == "\\" and j + 1 < n:
                    buf.append(sql[j + 1])
                    j += 2
                else:
                    buf.append(sql[j])
                    j += 1
            if j >= n:
                raise SqlError("unterminated string literal")
            toks.append(_Tok("str", "".join(buf)))
            i = j + 1
            continue
        if c.isdigit() or (c == "-" and i + 1 < n and sql[i + 1].isdigit()):
            j = i
            while j < n and (sql[j].isdigit() or sql[j] == "."):
                j += 1
            toks.append(_Tok("num", sql[i:j]))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] in "_."):
                j += 1
            word = sql[i:j]
            up = word.upper()
            if up in _KEYWORDS:
                toks.append(_Tok("kw", up))
            elif up == "LIKE":
                # LIKE is an operator, not a keyword, in our mini-grammar.
                toks.append(_Tok("op", up))
            else:
                toks.append(_Tok("ident", word))
            i = j
            continue
        # multi-char operators
        if sql[i:i + 2] in ("!=", "<>", ">=", "<="):
            toks.append(_Tok("op", sql[i:i + 2]))
            i += 2
            continue
        if c in "=<>":
            toks.append(_Tok("op", c))
            i += 1
            continue
        raise SqlError(f"unexpected character {c!r} in query")
    toks.append(_Tok("eof", ""))
    return toks


class _Parser:
    def __init__(self, toks: List[_Tok], valid_cols: List[str]):
        self.toks = toks
        self.pos = 0
        self.valid_cols = set(valid_cols)

    def peek(self) -> _Tok:
        return self.toks[self.pos]

    def next(self) -> _Tok:
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def expect(self, kind, what):
        t = self.next()
        if t.kind != kind:
            raise SqlError(f"expected {what} but found {t.val!r}")

    def parse(self):
        self.expect("kw", "SELECT")
        cols = self._parse_select_list()
        self.expect("kw", "FROM")
        # FROM identifier is accepted but ignored (single-table, runs on the
        # already-loaded pane). Validate it is an identifier for friendliness.
        frm = self.peek()
        if frm.kind in ("ident", "kw"):
            self.next()
        else:
            raise SqlError("expected a table name after FROM")
        where = None
        if self.peek().kind == "kw" and self.peek().val == "WHERE":
            self.next()
            where = self._parse_expr()
        order_col = None
        order_desc = False
        if self.peek().kind == "kw" and self.peek().val == "ORDER":
            self.next()
            self.expect("kw", "BY")
            oc = self.next()
            if oc.kind not in ("ident", "star"):
                raise SqlError("ORDER BY needs a column name")
            order_col = oc.val
            if self.peek().kind == "kw" and self.peek().val in ("ASC", "DESC"):
                order_desc = self.next().val == "DESC"
        limit = None
        if self.peek().kind == "kw" and self.peek().val == "LIMIT":
            self.next()
            lt = self.next()
            if lt.kind != "num":
                raise SqlError("LIMIT needs an integer")
            limit = int(float(lt.val))
        if self.peek().kind != "eof" and self.peek().kind != "punct":
            raise SqlError(f"unexpected token {self.peek().val!r} after query")
        return dict(cols=cols, where=where, order_col=order_col,
                    order_desc=order_desc, limit=limit)

    def _parse_select_list(self):
        if self.peek().kind == "star":
            self.next()
            return None  # SELECT *
        cols = []
        while True:
            t = self.next()
            if t.kind not in ("ident", "star"):
                raise SqlError("expected a column name or * in SELECT list")
            cols.append(t.val)
            if self.peek().kind == "punct" and self.peek().val == ",":
                self.next()
                continue
            break
        return cols

    # expression: OR binds loosest
    def _parse_expr(self):
        node = self._parse_and()
        while self.peek().kind == "kw" and self.peek().val == "OR":
            self.next()
            rhs = self._parse_and()
            node = ("OR", node, rhs)
        return node

    def _parse_and(self):
        node = self._parse_atom()
        while self.peek().kind == "kw" and self.peek().val == "AND":
            self.next()
            rhs = self._parse_atom()
            node = ("AND", node, rhs)
        return node

    def _parse_atom(self):
        if self.peek().kind == "punct" and self.peek().val == "(":
            self.next()
            node = self._parse_expr()
            if self.peek().kind != "punct" or self.peek().val != ")":
                raise SqlError("missing closing parenthesis")
            self.next()
            return node
        return self._parse_condition()

    def _parse_condition(self):
        col = self.next()
        if col.kind not in ("ident", "star"):
            raise SqlError("WHERE condition needs a column name on the left")
        op = self.next()
        if op.kind != "op":
            raise SqlError(f"expected a comparison operator after {col.val!r}")
        val = self.next()
        if val.kind == "str":
            literal: Any = val.val
        elif val.kind == "num":
            literal = float(val.val)
        elif val.kind == "ident":
            # bareword literal: True/False are boolean literals, else string
            up = val.val.upper()
            if up == "TRUE":
                literal = True
            elif up == "FALSE":
                literal = False
            else:
                literal = val.val
        elif val.kind == "kw" and val.val in ("LIKE",):
            raise SqlError("LIKE must follow a column and operator")
        else:
            raise SqlError(f"unexpected value {val.val!r} in condition")
        return ("COND", col.val, op.val, literal)


def _like_to_regex(pattern: str) -> "re.Pattern":
    esc = re.escape(pattern).replace("%", ".*").replace("_", ".")
    return re.compile("^" + esc + "$", re.IGNORECASE)


def _compare(a: Any, op: str, b: Any) -> bool:
    if op == "LIKE":
        if a is None:
            return False
        return _like_to_regex(str(b)).match(str(a)) is not None
    # numeric-aware compare when both sides look numeric
    try:
        an = float(a)
        bn = float(b)
        a2, b2 = an, bn
    except (TypeError, ValueError):
        a2, b2 = a, b
    if a is None or b is None:
        # only equality can meaningfully involve None
        if op in ("=",):
            return a is None and b is None
        if op in ("!=", "<>"):
            return not (a is None and b is None)
        return False
    if op == "=":
        return a2 == b2
    if op in ("!=", "<>"):
        return a2 != b2
    if op == ">":
        return a2 > b2
    if op == "<":
        return a2 < b2
    if op == ">=":
        return a2 >= b2
    if op == "<=":
        return a2 <= b2
    raise SqlError(f"unsupported operator {op!r}")


def _eval(node, row: Dict[str, Any]) -> bool:
    if node[0] == "AND":
        return _eval(node[1], row) and _eval(node[2], row)
    if node[0] == "OR":
        return _eval(node[1], row) or _eval(node[2], row)
    # COND
    _, col, op, lit = node
    return _compare(row.get(col), op, lit)


def run_query(columns, rows, sql: str):
    """Execute ``sql`` against decoded ``rows``.

    ``columns`` is the source ``Table.columns`` list (for type/projection info).
    Returns a ``QueryResult`` with projected/filtered/sorted/limited rows.
    Raises ``SqlError`` on any invalid input.
    """
    valid = [c.name for c in columns]
    toks = _tokenize(sql)
    ast = _Parser(toks, valid).parse()

    # resolve projection
    if ast["cols"] is None:
        proj = [(c.name, c) for c in columns]
    else:
        proj = []
        for name in ast["cols"]:
            match = next((c for c in columns if c.name == name), None)
            if match is None:
                raise SqlError(
                    f"unknown column {name!r}; valid columns: {', '.join(valid)}")
            proj.append((name, match))

    out = []
    for r in rows:
        if ast["where"] is None or _eval(ast["where"], r):
            new_row = {name: r.get(name) for name, _ in proj}
            out.append(new_row)

    if ast["order_col"] is not None:
        oc = ast["order_col"]
        if oc not in valid:
            raise SqlError(f"ORDER BY column {oc!r} not found")
        out.sort(key=lambda r, oc=oc: tuple(_numkey(r.get(oc))), reverse=ast["order_desc"])

    if ast["limit"] is not None:
        out = out[: ast["limit"]]

    # build result columns (keep original type_id so type_name resolves)
    from snitchql.reader import Column
    res_cols = []
    for idx, (name, src) in enumerate(proj):
        res_cols.append(Column(
            index=idx, name=name, type_id=src.type_id,
            length=0, row_offset=0))
    return QueryResult(columns=res_cols, rows=out, total_rows=len(out))


def _numkey(v):
    try:
        return (0, float(v))
    except (TypeError, ValueError):
        return (1, str(v) if v is not None else "")


@dataclass
class QueryResult:
    """Read-only table-shaped result, consumable by ``RowTableModel``."""
    columns: List[Any]
    rows: List[Dict[str, Any]]
    total_rows: int
    path: Optional[str] = field(default=None)
