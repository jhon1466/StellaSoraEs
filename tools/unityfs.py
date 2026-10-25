"""Minimal UnityFS (Unity 2022.3) reader/writer that mirrors the game's own bundle layout.

UnityPy's ``save(packer="lz4")`` writes flags 0xC2 (block info at the end, no
start padding). Stella Sora ships bundles with 0x243 and verifies a CRC from
``ss_win.mani``, so patched bundles must be rewritten in the original layout and
the manifest updated with the new MD5, size and CRC.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import lz4.block

COMPRESSION_MASK = 0x3F
COMBINED = 0x40
INFO_AT_END = 0x80
PAD_AT_START = 0x200
BLOCK_SIZE = 0x20000


@dataclass
class Node:
    offset: int
    size: int
    flags: int
    path: str


@dataclass
class Bundle:
    version: int
    player: str
    engine: str
    flags: int
    block_flags: list[int]
    nodes: list[Node]
    data: bytes  # concatenated uncompressed block payload

    def node_bytes(self, node: Node) -> bytes:
        return self.data[node.offset : node.offset + node.size]


def _cstr(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.index(b"\0", pos)
    return buf[pos:end].decode(), end + 1


def _decompress(payload: bytes, kind: int, size: int) -> bytes:
    if kind == 0:
        return payload
    if kind in (2, 3):
        return lz4.block.decompress(payload, uncompressed_size=size)
    raise ValueError(f"unsupported UnityFS compression {kind}")


def read_bundle(raw: bytes) -> Bundle:
    sig, pos = _cstr(raw, 0)
    if sig != "UnityFS":
        raise ValueError("not a UnityFS bundle")
    (version,) = struct.unpack_from(">I", raw, pos)
    player, pos = _cstr(raw, pos + 4)
    engine, pos = _cstr(raw, pos)
    _total, csize, usize, flags = struct.unpack_from(">QIII", raw, pos)
    pos += 20
    if version >= 7:
        pos = (pos + 15) & ~15
    if flags & INFO_AT_END:
        info_raw = raw[len(raw) - csize :]
    else:
        info_raw = raw[pos : pos + csize]
        pos += csize
    info = _decompress(info_raw, flags & COMPRESSION_MASK, usize)
    if flags & PAD_AT_START:
        pos = (pos + 15) & ~15

    ip = 16  # skip uncompressed data hash
    (block_count,) = struct.unpack_from(">I", info, ip)
    ip += 4
    blocks = []
    for _ in range(block_count):
        blocks.append(struct.unpack_from(">IIH", info, ip))
        ip += 10
    (node_count,) = struct.unpack_from(">I", info, ip)
    ip += 4
    nodes = []
    for _ in range(node_count):
        off, size, nflags = struct.unpack_from(">QQI", info, ip)
        path, ip = _cstr(info, ip + 20)
        nodes.append(Node(off, size, nflags, path))

    chunks = []
    for usz, csz, bflags in blocks:
        chunks.append(_decompress(raw[pos : pos + csz], bflags & COMPRESSION_MASK, usz))
        pos += csz
    return Bundle(version, player, engine, flags, [b[2] for b in blocks], nodes, b"".join(chunks))


def _lz4hc(data: bytes) -> bytes:
    return lz4.block.compress(data, mode="high_compression", compression=9, store_size=False)


def write_bundle(bundle: Bundle, files: dict[str, bytes]) -> bytes:
    """Serialize ``files`` (node path -> bytes, in original node order) as 0x243 UnityFS."""
    nodes: list[Node] = []
    payload = bytearray()
    for node in bundle.nodes:
        content = files[node.path]
        nodes.append(Node(len(payload), len(content), node.flags, node.path))
        payload += content

    blocks = []
    block_data = bytearray()
    for start in range(0, len(payload), BLOCK_SIZE):
        chunk = bytes(payload[start : start + BLOCK_SIZE])
        packed = _lz4hc(chunk)
        if len(packed) >= len(chunk):
            blocks.append((len(chunk), len(chunk), 0))
            block_data += chunk
        else:
            blocks.append((len(chunk), len(packed), 3))
            block_data += packed

    info = bytearray(b"\0" * 16)
    info += struct.pack(">I", len(blocks))
    for usz, csz, bflags in blocks:
        info += struct.pack(">IIH", usz, csz, bflags)
    info += struct.pack(">I", len(nodes))
    for node in nodes:
        info += struct.pack(">QQI", node.offset, node.size, node.flags) + node.path.encode() + b"\0"
    info_packed = _lz4hc(bytes(info))

    head = bytearray(b"UnityFS\0")
    head += struct.pack(">I", bundle.version)
    head += bundle.player.encode() + b"\0" + bundle.engine.encode() + b"\0"
    total_pos = len(head)
    head += struct.pack(">QIII", 0, len(info_packed), len(info), 0x243)
    head += b"\0" * (-len(head) % 16)
    head += info_packed
    head += b"\0" * (-len(head) % 16)
    head += block_data
    struct.pack_into(">Q", head, total_pos, len(head))
    return bytes(head)


def payload_crc(bundle: Bundle) -> int:
    return zlib.crc32(bundle.data) & 0xFFFFFFFF
