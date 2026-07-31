"""DBISAM blob (.blb) reader.

DBISAM stores memo/blob fields in a companion .blb file. The file shares the
standard DBISAM header (version + 16-byte GUID at 0x08, block size at 0x1C)
and is divided into fixed-size blocks. Within the data region, blob records are
self-describing:

    [4-byte flag] [4-byte length] [length bytes of payload]

We walk the data region in 4-byte steps, accept a candidate only when the flag
is small (0..16) and length is sane, and keep it when the payload is mostly
printable (treated as text) or short. This is a best-effort extractor, not a
byte-exact DBISAM blob allocator — sufficient to browse what's stored.

Text payloads are returned decoded (utf-8/cp1252 fallback); binary payloads are
returned as raw bytes with flag intact so callers can decide how to render them.
"""
import struct
from typing import List, Dict

HEADER_SIZE = 0x40


def parse_header(data: bytes) -> Dict:
    if len(data) < HEADER_SIZE:
        raise ValueError("file too small to be a DBISAM blob")
    ver = struct.unpack("<I", data[0:4])[0]
    guid = data[0x08:0x18].hex()
    block_size = struct.unpack("<I", data[0x1C:0x20])[0] or 8192
    return {"version": ver, "guid": guid, "block_size": block_size,
            "size": len(data)}


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("latin-1", "replace")


def read_blobs(path: str, max_records: int = 5000, cap_mb: int = 25) -> Dict:
    """Extract blob records from a .blb file.

    Returns {version, guid, block_size, records:[{flag, data:bytes, text:str|None}]}.
    The scan is capped to cap_mb so very large blob files stay responsive.
    """
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) > cap_mb * 1024 * 1024:
        raw = raw[: cap_mb * 1024 * 1024]
    hdr = parse_header(raw)
    recs: List[Dict] = []
    n = 0
    off = HEADER_SIZE
    nbytes = len(raw)
    while off + 8 <= nbytes and n < max_records:
        flag = struct.unpack("<I", raw[off:off + 4])[0]
        ln = struct.unpack("<I", raw[off + 4:off + 8])[0]
        if 0 <= flag <= 16 and 1 <= ln <= 200000 and off + 8 + ln <= nbytes:
            payload = raw[off + 8:off + 8 + ln]
            printable = sum(1 for x in payload[:min(40, ln)] if 32 <= x < 127)
            is_text = (ln <= 3) or (printable >= min(10, ln // 2))
            recs.append({
                "flag": flag,
                "data": payload,
                "text": _decode(payload) if is_text else None,
            })
            off += 8 + ln
            n += 1
            continue
        off += 4  # records appear 4-byte aligned
    return {"version": hdr["version"], "guid": hdr["guid"],
            "block_size": hdr["block_size"], "records": recs,
            "scanned": nbytes}
