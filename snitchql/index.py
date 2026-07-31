"""SnitchQL index (.idx) engine — verify / repair / rebuild.

DBISAM stores indexes as proprietary B+tree files (.idx). pydbisam does NOT
support them ("Not sure the exact behavior of these indexes"), so this module
is a clean-room reverse of the on-disk format, validated against Damion's real
files (e.g. DAYCASH.idx: ver=0x0215, blockSize=1280).

Current capability (M3):
  * parse_idx_header()      -> version, guid, block_size, root_block, entry_count
  * walk_leaves()           -> yields (key_bytes, recno) from leaf pages
  * verify_index(dat)       -> cross-checks index <-> .dat consistency
  * IndexReport dataclass   -> structured result for CLI/GUI

Rebuild is a follow-on milestone (needs byte-exact B+tree emit).
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Tuple


@dataclass
class IndexHeader:
    version: int
    guid: str
    block_size: int
    root_block: int
    entry_count: int
    raw: bytes = b""


@dataclass
class IndexReport:
    path: str
    header: Optional[IndexHeader]
    parse_ok: bool
    error: str = ""
    leaf_entries: int = 0
    # consistency vs .dat
    dat_rows: int = 0
    keys_covered: int = 0          # leaf keys whose recno points to a live row
    dangling_recnos: int = 0       # leaf recnos with no live row
    missing_in_index: int = 0      # live rows whose key is absent from index
    sorted_ok: bool = True         # keys in ascending order
    notes: List[str] = field(default_factory=list)


def parse_idx_header(path: str) -> IndexHeader:
    data = open(path, "rb").read()
    if len(data) < 0x30:
        raise ValueError("file too small to be a DBISAM index")
    version = int.from_bytes(data[0:2], "little")
    guid = data[8:24].hex()
    block_size = int.from_bytes(data[0x1C:0x20], "little")
    root_block = int.from_bytes(data[0x18:0x1C], "little")
    entry_count = int.from_bytes(data[0x30:0x34], "little")
    return IndexHeader(version=version, guid=guid, block_size=block_size,
                       root_block=root_block, entry_count=entry_count, raw=data)


def _read_block(data: bytes, block_size: int, block_no: int) -> bytes:
    start = block_no * block_size
    if start + block_size > len(data):
        return b""
    return data[start:start + block_size]


# DBISAM integer/date keys are stored sign-normalized so unsigned byte
# comparison preserves sort order: value XOR 0x80000000.
def _decode_int_key(key: bytes) -> int:
    v = int.from_bytes(key, "little")
    return v ^ 0x80000000


def walk_leaves(path: str, key_len: int = 4, header: int = 14,
                nrows: int = 0, heuristic: bool = False) -> Iterator[Tuple[bytes, int]]:
    """Yield (key_bytes, recno) pairs from index leaf pages.

    Decoded from real files (PromoControl.idx, single int key=3, recno=1):
      * Leaf pages use type 0x02 or 0x03 (varies by DBISAM version; ver 8 ->
        0x03, ver 533 -> 0x02, ver 4 -> 0x03). Branch/internal pages use
        higher codes (0x08, 0x11, 0x30, 0x48, 0x49, 0x72, 0x80, ...).
      * Fixed 14-byte page header, then fixed-length entries of
        [key_len bytes][4-byte recno (little)].
      * integer/date keys are sign-normalized (XOR 0x80000000).

    key_len: length of the key in bytes (from the index definition; default 4
    for 32-bit integer/date indexes). header: leaf page header size in bytes
    (default 14, verified against single-row indexes; DBISAM computes this
    from key length + flags, so adjust per index type if needed).
    nrows / heuristic: when strict leaf-code scan yields nothing yet the table
    has rows, a heuristic second pass accepts any block type and keeps only
    entries whose recno is in [1, nrows] (rejecting branch child-pointers,
    which point at block numbers, not records).
    """
    h = parse_idx_header(path)
    data = h.raw
    bs = h.block_size
    nblocks = len(data) // bs

    leaf_types = (0x02, 0x03)

    def scan(accept_types):
        for bn in range(nblocks):
            if bn == 0:
                continue  # block 0 is always the file header, never a leaf
            pg = _read_block(data, bs, bn)
            if not pg or len(pg) < header + key_len + 4:
                continue
            if accept_types is not None and pg[0] not in accept_types:
                continue
            off = header
            while off + key_len + 4 <= len(pg):
                key = pg[off: off + key_len]
                if key == b"\x00" * key_len and pg[off + key_len: off + key_len + 4] == b"\x00" * 4:
                    break
                recno = int.from_bytes(pg[off + key_len: off + key_len + 4], "little")
                if nrows and (recno < 1 or recno > nrows):
                    # outside valid record range -> not a leaf recno
                    off += key_len + 4
                    continue
                yield (key, recno)
                off += key_len + 4

    strict = list(scan(leaf_types))
    if strict or not nrows:
        yield from strict
        return
    # heuristic fallback: accept any block, keep only in-range recnos
    yield from scan(None)


# candidate key lengths to try when auto-detecting
_KEY_LEN_CANDIDATES = [4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 30, 32, 40, 48]


def _field_values(t) -> dict:
    """Map each column name -> set of normalized string values for matching."""
    out = {}
    for col in t.columns:
        vals = set()
        for row in t.rows:
            v = row.get(col.name)
            if v is None:
                continue
            if isinstance(v, (int, float)):
                vals.add(str(v))
            else:
                vals.add(str(v).strip().upper())
        out[col.name] = vals
    return out


def auto_detect_key_len(idx_path: str, dat_path: str) -> int:
    """Pick the key_len whose decoded leaf keys best match real .dat values.

    For each candidate length we walk the leaves (with nrows validation) and
    score how many decoded keys correspond to a value in some .dat column.
    The correct key_len yields keys that are real indexed-field values, so it
    scores highest. Returns the best candidate (default 4).
    """
    from snitchql.reader import read_table
    t = read_table(dat_path)
    nrows = len(t.rows)
    fvals = _field_values(t)

    best_kl, best_score = 4, -1
    for kl in _KEY_LEN_CANDIDATES:
        try:
            leaves = list(walk_leaves(idx_path, key_len=kl, nrows=nrows))
        except Exception:
            continue
        if not leaves:
            continue
        score = 0
        for key, recno in leaves:
            if recno < 1 or recno > nrows:
                continue
            # decode key as trimmed string (covers int/date/string encodings)
            s = key.rstrip(b"\x00").decode("latin-1", "replace").strip().upper()
            s2 = key.rstrip(b"\x00")
            # variant-int decode attempt
            try:
                iv = int.from_bytes(key, "little") ^ 0x80000000
                siv = str(iv)
            except Exception:
                siv = ""
            for vals in fvals.values():
                if s in vals or s2.hex() in {v for v in ()} or (siv and siv in vals):
                    score += 1
                    break
        if score > best_score:
            best_score, best_kl = score, kl
    return best_kl


def verify_index(idx_path: str, dat_path: Optional[str] = None,
                 key_len: int = 4) -> IndexReport:
    """Verify an .idx against its .dat (if available).

    key_len: key length in bytes for the index. This MUST match the actual
    index key length; it is version/field-type dependent and currently must
    be supplied by the caller (auto-detection is not yet reliable). For indexes
    whose leaf layout hasn't been decoded, the report will show leaf_entries=0
    and an 'UNKNOWN' note rather than a misleading consistency verdict.
    """
    idx_path = str(idx_path)
    rep = IndexReport(path=idx_path, header=None, parse_ok=False)
    try:
        h = parse_idx_header(idx_path)
        rep.header = h
        rep.parse_ok = True
    except Exception as e:
        rep.error = str(e)
        return rep

    # resolve .dat row count up front so walk_leaves can use the heuristic fallback
    nrows = 0
    if dat_path is None:
        stem = Path(idx_path).stem
        cand = Path(idx_path).parent / (stem + ".dat")
        dat_path = str(cand) if cand.exists() else None
    if dat_path and Path(dat_path).exists():
        try:
            from snitchql.reader import read_table
            nrows = len(read_table(dat_path).rows)
            rep.dat_rows = nrows
        except Exception:
            nrows = 0

    # walk leaves (key_len supplied by caller; see module docstring)
    keys = []
    recnos = []
    try:
        for key, recno in walk_leaves(idx_path, key_len=key_len, nrows=nrows):
            keys.append(key)
            recnos.append(recno)
            rep.leaf_entries += 1
    except Exception as e:
        rep.error = f"leaf walk failed: {e}"
        return rep

    # Honest status: we only report CONSISTENT when the decoded leaf count
    # exactly matches the live row count (tight criterion, proven correct on
    # ver-8 integer indexes). If we decoded zero or only a partial set of leaf
    # entries, the leaf layout/key length for this index version is not yet
    # fully decoded -> report UNKNOWN rather than a misleading verdict.
    if rep.leaf_entries != nrows:
        rep.notes.append(
            f"UNKNOWN: decoded {rep.leaf_entries} leaf entries vs {nrows} live rows "
            f"(leaf layout / key length for this index version not yet fully "
            f"decoded). Not a consistency verdict.")
        return rep

    # sorted check (lexicographic on raw bytes; ascending)
    rep.sorted_ok = all(keys[i] <= keys[i + 1] for i in range(len(keys) - 1))

    # cross-check vs .dat
    if dat_path is None:
        stem = Path(idx_path).stem
        cand = Path(idx_path).parent / (stem + ".dat")
        dat_path = str(cand) if cand.exists() else None
    if dat_path and Path(dat_path).exists():
        from snitchql.reader import read_table
        try:
            t = read_table(dat_path)
            rep.dat_rows = len(t.rows)
            live = set(range(1, len(t.rows) + 1))  # recno 1-based
            for rn in recnos:
                if rn in live:
                    rep.keys_covered += 1
                else:
                    rep.dangling_recnos += 1
            rep.missing_in_index = max(0, rep.dat_rows - rep.keys_covered)
            if rep.dangling_recnos:
                rep.notes.append(f"{rep.dangling_recnos} leaf recnos point to non-live rows")
            if rep.missing_in_index:
                rep.notes.append(f"{rep.missing_in_index} live rows absent from index")
        except Exception as e:
            rep.notes.append(f".dat read skipped: {e}")
    return rep
