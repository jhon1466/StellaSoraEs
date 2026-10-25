"""Read and rebuild Stella Sora 0x66 data.arcx/lua.arcx archives."""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import lz4.block

MAGIC = 0x5241421A
VERSION = 0x66
HEADER_SIZE = 24
ENTRY_SIZE = 20
PACK_KEY = b"&^^%#$#_$!@![]<_>?GHBFR_7481SDR_"
TEXT_KEY = bytes.fromhex("a921a467c7d7df396e60909c31225bd2")
DELTA = 0x9E3779B9
MASK = 0xFFFFFFFF


@dataclass
class Entry:
    hash64: int
    offset: int
    origin_size: int
    stored_size: int


@dataclass
class Archive:
    flags: int
    entries_origin_size: int
    entries_size: int
    entries: list[Entry]
    data: bytes


def xor_pack(data: bytes) -> bytes:
    return bytes(value ^ PACK_KEY[i % len(PACK_KEY)] for i, value in enumerate(data))


def _mx(total: int, y: int, z: int, pos: int, e: int, key: list[int]) -> int:
    a = (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) & MASK
    b = ((y ^ total) + (key[(e ^ (pos & 3)) & 3] ^ z)) & MASK
    return (a ^ b) & MASK


def xxtea_decrypt(data: bytes, key: bytes = TEXT_KEY) -> bytes:
    if not data or len(data) % 4:
        raise ValueError("XXTEA payload must be a non-empty multiple of four bytes")
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    k = list(struct.unpack("<4I", key[:16].ljust(16, b"\0")))
    n = len(words)
    if n < 2:
        return data
    total = ((6 + 52 // n) * DELTA) & MASK
    z = words[0]
    while total:
        e = (total >> 2) & 3
        for pos in range(n - 1, 0, -1):
            y = words[pos - 1]
            words[pos] = (words[pos] - _mx(total, z, y, pos, e, k)) & MASK
            z = words[pos]
        y = words[-1]
        words[0] = (words[0] - _mx(total, z, y, 0, e, k)) & MASK
        z = words[0]
        total = (total - DELTA) & MASK
    plain = struct.pack(f"<{n}I", *words)
    length = words[-1]
    if length > len(plain) - 4:
        raise ValueError("invalid XXTEA embedded length")
    return plain[:length]


def xxtea_encrypt(data: bytes, key: bytes = TEXT_KEY) -> bytes:
    n = (len(data) + 3) // 4 + 1
    words = [0] * n
    for i, value in enumerate(data):
        words[i >> 2] |= value << ((i & 3) * 8)
    words[-1] = len(data)
    k = list(struct.unpack("<4I", key[:16].ljust(16, b"\0")))
    count = len(words)
    if count < 2:
        return struct.pack(f"<{count}I", *words)
    total = 0
    z = words[-1]
    rounds = 6 + 52 // count
    for _ in range(rounds):
        total = (total + DELTA) & MASK
        e = (total >> 2) & 3
        for pos in range(count - 1):
            y = words[pos + 1]
            words[pos] = (words[pos] + _mx(total, y, z, pos, e, k)) & MASK
            z = words[pos]
        y = words[0]
        words[-1] = (words[-1] + _mx(total, y, z, count - 1, e, k)) & MASK
        z = words[-1]
    return struct.pack(f"<{count}I", *words)


def read_archive(path: Path) -> Archive:
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise ValueError("ARCX header is truncated")
    magic, version, flags, table_origin, table_size, count = struct.unpack_from("<6I", data)
    if magic != MAGIC or version != VERSION:
        raise ValueError(f"expected ARCX 0x66, got magic={magic:#x} version={version:#x}")
    table_end = HEADER_SIZE + table_size
    if table_end > len(data):
        raise ValueError("entry table extends past end of archive")
    table = lz4.block.decompress(xor_pack(data[HEADER_SIZE:table_end]), uncompressed_size=table_origin)
    if len(table) != count * ENTRY_SIZE:
        raise ValueError(f"entry table is {len(table)} bytes for {count} entries")
    entries = [Entry(*struct.unpack_from("<QIII", table, i * ENTRY_SIZE)) for i in range(count)]
    for i, e in enumerate(entries):
        if e.offset < table_end or e.offset + e.stored_size > len(data):
            raise ValueError(f"entry {i} points outside archive: {e.offset:#x}+{e.stored_size:#x}")
    return Archive(flags, table_origin, table_size, entries, data)


def decode_entry(archive: Archive, index: int) -> bytes:
    entry = archive.entries[index]
    packed = archive.data[entry.offset : entry.offset + entry.stored_size]
    xxtea_blob = lz4.block.decompress(packed, uncompressed_size=entry.origin_size)
    return xxtea_decrypt(xxtea_blob)


def _serialize_entries(entries: list[Entry]) -> bytes:
    return b"".join(struct.pack("<QIII", e.hash64, e.offset, e.origin_size, e.stored_size) for e in entries)


def rebuild_archive(source: Path, destination: Path, replacements: dict[int, bytes]) -> None:
    archive = read_archive(source)
    if any(index < 0 or index >= len(archive.entries) for index in replacements):
        raise ValueError("replacement entry index is out of range")
    packed_blocks: list[bytes] = []
    entries: list[Entry] = []
    for i, entry in enumerate(archive.entries):
        if i in replacements:
            xxtea_blob = xxtea_encrypt(replacements[i])
            packed = lz4.block.compress(xxtea_blob, store_size=False)
            origin_size = len(xxtea_blob)
        else:
            packed = archive.data[entry.offset : entry.offset + entry.stored_size]
            origin_size = entry.origin_size
        entries.append(Entry(entry.hash64, 0, origin_size, len(packed)))
        packed_blocks.append(packed)

    # Stored offsets are absolute byte positions in 0x66, not 4 KiB units.
    initial_table = lz4.block.compress(_serialize_entries(entries), store_size=False)
    # Leave a small gap after the table so changing offsets cannot create a
    # compression-size fixed-point cycle. The native reader seeks absolute offsets.
    block_start = HEADER_SIZE + len(initial_table) + 0x1000
    cursor = block_start
    for entry, block in zip(entries, packed_blocks):
        entry.offset = cursor
        cursor += len(block)
    table_plain = _serialize_entries(entries)
    table_stored = xor_pack(lz4.block.compress(table_plain, store_size=False))
    padding = block_start - HEADER_SIZE - len(table_stored)
    if padding < 0:
        raise RuntimeError("entry table grew beyond reserved pre-data padding")

    header = struct.pack("<6I", MAGIC, VERSION, archive.flags, len(table_plain), len(table_stored), len(entries))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        output.write(header)
        output.write(table_stored)
        output.write(b"\0" * padding)
        for block in packed_blocks:
            output.write(block)


def inventory(path: Path) -> None:
    archive = read_archive(path)
    kinds: dict[str, int] = {}
    print(f"ARCX 0x66 entries={len(archive.entries)} flags={archive.flags:#x}")
    for i in range(len(archive.entries)):
        data = decode_entry(archive, i)
        if data.startswith(b"T\x93\x03\0"):
            kind = "bindata"
        elif data.lstrip().startswith((b"{", b"[")):
            kind = "json"
        else:
            kind = "other"
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "json":
            try:
                obj = json.loads(data)
                sample = next(iter(obj), "") if isinstance(obj, dict) else "json-array"
            except Exception:
                sample = "invalid-json"
            print(f"{i}\t{archive.entries[i].hash64:016x}\t{len(data)}\t{sample}")
    print("types=" + json.dumps(kinds, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    if args.inventory:
        inventory(args.archive)
    else:
        archive = read_archive(args.archive)
        print(f"ARCX 0x66: {len(archive.entries)} entries; table {archive.entries_origin_size} -> {archive.entries_size} bytes")
        for i in range(min(5, len(archive.entries))):
            print(i, archive.entries[i], "decoded", len(decode_entry(archive, i)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
