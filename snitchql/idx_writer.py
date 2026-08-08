"""Native DBISAM 4.48 .idx WRITER — baseline from old dbisamen.pas source.

Template only. Goal: emit a byte-exact 4.48-format .idx that the real DBISAM
engine (dbsys Verify) accepts. dbsys is used ONLY as a dev-time oracle
(we diff against the known-good 4.48 rebuild in /home/alsaher/Documents/indexes),
NOT as a runtime wrapper.

Constants verified against dbisamcn.pas (old v4 source):
  RECORD_SIZE=4 KEYCOUNT_SIZE=4 DUPBYTE_SIZE=1 TRAILBYTE_SIZE=1
  KEY_DATA=10  NO_COMPRESS=0 DUPBYTE_COMPRESS=1 TRAILBYTE_COMPRESS=2 BOTH_COMPRESS=3
  PAGE_HEADER=13  PAGE_SIZE=4096
TIndexHeader: 512 bytes; TIndexDefinition: 768 bytes (base offset 512).
Leaf entry (uncompressed): [FKeySize][recno 4][keycount 4][dup 1][trail 1].
"""
from __future__ import annotations
import struct
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

RECORD_SIZE = 4
KEYCOUNT_SIZE = 4
DUPBYTE_SIZE = 1
TRAILBYTE_SIZE = 1
KEY_DATA = RECORD_SIZE + KEYCOUNT_SIZE + DUPBYTE_SIZE + TRAILBYTE_SIZE  # 10
NO_COMPRESS, DUPBYTE_COMPRESS, TRAILBYTE_COMPRESS, BOTH_COMPRESS = 0, 1, 2, 3
PAGE_SIZE = 4096
PAGE_HEADER = 13  # PageType(1)+NumberOfKeys(2)+LeftNumber(4)+RightNumber(4)+CompressedSize(2)
HEADER_SIZE = 512
DEF_SIZE = 768
MAX_NUM_INDEXES = 31


@dataclass
class IndexDef:
    name: str
    keysize: int
    unique: bool
    comp: int
    field_count: int
    field_nums: list = field(default_factory=list)


def read_index_defs(idx_path: Path) -> list[IndexDef]:
    """Parse TIndexDefinition records (768 bytes, base 512) from a real 4.48 .idx."""
    data = idx_path.read_bytes()
    defs = []
    off = HEADER_SIZE
    for _ in range(8):
        if off + 130 > len(data):
            break
        namelen = data[off]
        name = data[off + 1:off + 1 + namelen].decode('latin1', 'replace')
        keysize = struct.unpack_from('<H', data, off + 61)[0]
        unique = bool(data[off + 128])
        comp = data[off + 129]
        field_count = data[off + 130]
        flds = []
        for j in range(field_count):
            fn = struct.unpack_from('<H', data, off + 259 + 2 * j)[0]
            flds.append(fn)
        defs.append(IndexDef(name=name, keysize=keysize, unique=unique, comp=comp,
                             field_count=field_count, field_nums=flds))
        off += DEF_SIZE
    return defs


# ---- compression (reverse of TPage.CompressPageData, BOTH_COMPRESS non-unique) ----
def compress_both(key: bytes, recno: int, prev: bytes, keysize: int, unique: bool) -> bytes:
    """Produce the on-disk entry bytes for one key+recno (non-unique BOTH_COMPRESS).

    Source logic (dbisamen.pas 40614-40667): writes
      dup(1) trail(1) suffix(keysize-trail-dup-RECORDID_SIZE) recid(4) recno(4)
    where dup = leading bytes equal to prev key; trail = trailing bytes equal to prev
    key; recid = last RECORDID_SIZE bytes of the key.
    """
    dup = 0
    while dup < keysize and dup < len(prev) and key[dup] == prev[dup]:
        dup += 1
    trail = 0
    while (trail < keysize - dup - RECORD_SIZE and trail < len(prev) and
           key[keysize - 1 - trail] == prev[keysize - 1 - trail]):
        trail += 1
    suffix = key[dup: keysize - trail - RECORD_SIZE]
    recid = key[keysize - RECORD_SIZE: keysize]
    return bytes([dup, trail]) + suffix + recid + struct.pack('<I', recno)


def compress_trail(key, recno, prev, keysize, unique):
    if unique:
        trail = 0
        while trail < keysize and trail < len(prev) and key[keysize-1-trail] == prev[keysize-1-trail]:
            trail += 1
        suffix = key[:keysize - trail]
        return bytes([trail]) + suffix + struct.pack('<I', recno)
    trail = 0
    while (trail < keysize - RECORD_SIZE and trail < len(prev) and
           key[keysize-1-trail] == prev[keysize-1-trail]):
        trail += 1
    suffix = key[:keysize - trail - RECORD_SIZE]
    recid = key[keysize - RECORD_SIZE: keysize]
    return bytes([trail]) + suffix + recid + struct.pack('<I', recno)


def compress_dup(key, recno, prev, keysize, unique):
    dup = 0
    while dup < keysize and dup < len(prev) and key[dup] == prev[dup]:
        dup += 1
    suffix = key[dup:]
    return bytes([dup]) + suffix + struct.pack('<I', recno)


def compress_none(key, recno, prev, keysize, unique):
    return key + struct.pack('<I', recno) + struct.pack('<I', 0) + b'\x00' + b'\x00'


def build_leaf_page(keys: list[bytes], recnos: list[int], keysize: int, comp: int,
                    unique: bool) -> bytes:
    """Build a single compressed leaf page body (excluding the 13-byte header)."""
    entries = []
    prev = b'\x00' * keysize
    for k, r in zip(keys, recnos):
        if comp == BOTH_COMPRESS:
            entries.append(compress_both(k, r, prev, keysize, unique))
        elif comp == TRAILBYTE_COMPRESS:
            entries.append(compress_trail(k, r, prev, keysize, unique))
        elif comp == DUPBYTE_COMPRESS:
            entries.append(compress_dup(k, r, prev, keysize, unique))
        else:
            entries.append(compress_none(k, r, prev, keysize, unique))
        prev = k
    return b''.join(entries)
