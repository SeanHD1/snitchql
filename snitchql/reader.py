"""SnitchQL DBISAM reader (pure Python, multi-version).

Format model verified against Damion's real DBISAM v4 files and cross-checked
with the ISC-licensed pydbisam reference implementation. Key facts:

  File header (first 512 bytes), all little-endian:
    0x09  16  file signature
    0x29   4  total rows (live)
    0x2D   2  row size (bytes)
    0x2F   2  total fields
    0x47   1  description length
    0x48     description (cp1252)
    0xC1   2  user version major
    0xC3   1  user version minor
  Field definitions: 768 bytes each, starting at 0x200.
    Within each 768-block:
      0x00  2  index (1-based)
      0x02  n  name (leading control byte = type tag, strip it)
      0xA4  1  type id
      0xA6  2  length (string columns)
      0xAC  2  row offset (absolute within the row block)
  Rows: flat, each = row_size bytes, starting at
        data_offset = 0x200 + total_fields * 768.
    Within a row:
      - byte[0] = deleted flag (0 live, 1 deleted)  [pydbisam uses <B]
      - field i data at row_offset .. row_offset + size
      - byte at (row_offset - 1) = field-presence flag (0 => NULL/empty)
      - string columns carry a leading 0x01 tag byte before the text
"""
import struct
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

_DBISAM_EPOCH = date(1, 1, 1)  # Jan 1, AD 1


# DBISAM field type ids -> (name, native_size)
# native_size 0 means "variable, use column length"; -1 means opaque/unknown.
_TYPE_INFO = {
    1: ("String", 0),
    2: ("Date", 4),
    3: ("BLOB", -1),
    4: ("Boolean", 1),
    5: ("ShortInt", 2),
    6: ("Integer", 4),
    7: ("Double", 8),
    11: ("Timestamp", 8),
    5383: ("Currency", 8),
    7430: ("AutoInc", 4),
}


@dataclass
class Column:
    index: int
    name: str
    type_id: int
    length: int
    row_offset: int

    @property
    def type_name(self) -> str:
        return _TYPE_INFO.get(self.type_id, (f"Type{self.type_id}", -1))[0]

    @property
    def native_size(self) -> int:
        return _TYPE_INFO.get(self.type_id, (-1, -1))[1]

    @property
    def width(self) -> int:
        """Effective byte width of the field in a row.

        For fixed-width types this is the type's native size (Integer=4,
        Date=4, Double=8, ...). For strings it is the declared length.
        NOTE: the raw ``length`` attribute (read from the field-def ``0xA6``
        slot) is 0 for fixed-width types by DBISAM design -- DBISAM implies the
        width from the type and stores 0 there, so ``width`` is what a schema
        viewer should display, not ``length``.
        """
        if self.native_size > 0:
            return self.native_size
        return self.length

    @property
    def read_size(self) -> int:
        if self.native_size > 0:
            return self.native_size
        if self.native_size == 0:       # string
            return max(self.length, 1) + 1   # +1 leading tag byte
        return max(self.length, 1)      # opaque


@dataclass
class Table:
    path: str
    columns: list
    rows: list = field(default_factory=list)
    total_rows: int = 0
    deleted_rows: int = 0
    row_size: int = 0
    user_version: str = ""
    description: str = ""


def _decode(raw: bytes, col: Column):
    t = col.type_id
    if t == 1:  # String
        # row_offset already skips the leading tag byte (+1 at parse time),
        # so raw is pure text + trailing NULs.
        s = raw.rstrip(b"\x00").decode("cp1252", "replace")
        return s
    if t == 4:  # Boolean
        return bool(raw[0]) if raw else False
    if t == 5:
        return struct.unpack_from("<h", raw[:2])[0]
    if t == 6 or t == 7430:
        return struct.unpack_from("<i", raw[:4])[0]
    if t == 7 or t == 5383:
        return struct.unpack_from("<d", raw[:8])[0]
    if t == 2:  # Date
        d = struct.unpack_from("<i", raw[:4])[0]
        if d <= 0:
            return None
        return (_DBISAM_EPOCH + timedelta(days=d - 1)).isoformat()
    if t == 11:  # Timestamp
        ms = struct.unpack_from("<d", raw[:8])[0]
        if ms <= 0 or ms < 24 * 60 * 60 * 1000:
            return None
        return (_DBISAM_EPOCH + timedelta(milliseconds=ms - 1)).isoformat()
    if t == 3:  # BLOB -> pointer in .blb
        return None
    return raw.rstrip(b"\x00").hex()  # opaque/unknown -> hex preview


def read_table(path: str, include_deleted: bool = False) -> Table:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 0x30:
        # empty or truncated file (e.g. a 0-byte placeholder like user.dat)
        return Table(
            path=path, columns=[], rows=[],
            total_rows=0, deleted_rows=0, row_size=0,
            user_version="", description="",
        )
    mv = memoryview(data)

    total_rows = struct.unpack_from("<I", mv, 0x29)[0]
    row_size = struct.unpack_from("<H", mv, 0x2D)[0]
    total_fields = struct.unpack_from("<H", mv, 0x2F)[0]
    uv_major = struct.unpack_from("<H", mv, 0xC1)[0]
    uv_minor = mv[0xC3]
    desc_len = mv[0x47]
    description = bytes(mv[0x48:0x48 + desc_len]).rstrip(b"\x00").decode("cp1252", "replace")

    columns = []
    for i in range(total_fields):
        base = 0x200 + i * 768
        if base + 0xB0 > len(mv):
            break
        fidx = struct.unpack_from("<H", mv, base)[0]
        raw_name = bytes(mv[base + 0x02: base + 0x02 + 162]).rstrip(b"\x00")
        if raw_name[:1] < b"\x20":     # strip leading type-tag control byte
            raw_name = raw_name[1:]
        name = raw_name.decode("latin-1", "replace").split("\x00", 1)[0].strip()
        type_id = mv[base + 0xA4]
        length = struct.unpack_from("<H", mv, base + 0xA6)[0]
        row_off = struct.unpack_from("<H", mv, base + 0xAC)[0] + 1  # +1: def points at tag byte; data follows
        columns.append(Column(fidx, name, type_id, length, row_off))

    # rows flat from data_offset
    data_offset = 0x200 + total_fields * 768
    rows = []
    deleted = 0
    pos = data_offset
    seen_live = 0
    while pos + row_size <= len(mv) and (seen_live < total_rows or deleted < 999999):
        deleted_flag = mv[pos]
        row = {}
        ok = True
        for col in columns:
            off = pos + col.row_offset
            n = col.read_size
            raw = mv[off: off + n]
            if len(raw) < n:
                ok = False
                break
            # presence byte sits just before the data (at row_offset - 1)
            pres = mv[pos + col.row_offset - 1] if (pos + col.row_offset - 1) >= 0 else 0
            if pres == 0:
                # field absent/empty -> match pydbisam semantics
                row[col.name] = "" if col.type_id == 1 else None
                continue
            try:
                row[col.name] = _decode(bytes(raw), col)
            except Exception:
                ok = False
                break
        if ok:
            if deleted_flag == 1:
                deleted += 1
                if include_deleted:
                    row["__deleted__"] = True
                    rows.append(row)
            else:
                seen_live += 1
                rows.append(row)
        pos += row_size

    return Table(
        path=path, columns=columns, rows=rows,
        total_rows=total_rows, deleted_rows=deleted,
        row_size=row_size,
        user_version=f"{uv_major}.{uv_minor}",
        description=description,
    )


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "/home/alsaher/sampledat/Staff data bearing.dat"
    t = read_table(p)
    print(f"Table: {p}")
    print(f"  user_version : {t.user_version}")
    print(f"  description  : {t.description!r}")
    print(f"  total_rows   : {t.total_rows} (live {len(t.rows)}, deleted {t.deleted_rows})")
    print(f"  row_size     : {t.row_size}")
    print(f"  columns      : {len(t.columns)}")
    print("  schema:")
    for c in t.columns:
        print(f"    {c.index:>2} {c.name:<18} {c.type_name:<10} len={c.length} off={c.row_offset}")
    print("\n  first 3 rows:")
    for r in t.rows[:3]:
        print("   ", {k: v for k, v in r.items() if k != "__deleted__"})
