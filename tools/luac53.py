"""Read and rewrite string constants of Lua 5.3 bytecode (xLua, 64-bit sizes).

Only constant strings change; code, upvalues and debug info are copied as-is.
lundump.c reads short (0x04) and long (0x14) strings the same way, so a string
may change length without changing its type tag.
"""

from __future__ import annotations

import struct

HEADER = b"\x1bLua\x53\x00\x19\x93\r\n\x1a\n\x04\x08\x04\x08\x08"


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, n: int) -> bytes:
        chunk = self.data[self.pos : self.pos + n]
        if len(chunk) != n:
            raise ValueError("truncated Lua bytecode")
        self.pos += n
        return chunk

    def byte(self) -> int:
        return self.take(1)[0]

    def int(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def string(self) -> bytes | None:
        size = self.byte()
        if size == 0xFF:
            size = struct.unpack("<Q", self.take(8))[0]
        return None if size == 0 else self.take(size - 1)


def _w_string(out: bytearray, value: bytes | None) -> None:
    if value is None:
        out.append(0)
        return
    size = len(value) + 1
    if size < 0xFF:
        out.append(size)
    else:
        out.append(0xFF)
        out += struct.pack("<Q", size)
    out += value


def _function(r: _Reader, out: bytearray, fn) -> None:
    _w_string(out, r.string())  # source
    out += r.take(4 + 4 + 3)  # linedefined, lastlinedefined, numparams, is_vararg, maxstack
    n = r.int()
    out += struct.pack("<i", n) + r.take(4 * n)  # code
    n = r.int()
    out += struct.pack("<i", n)
    for _ in range(n):
        tag = r.byte()
        out.append(tag)
        if tag == 0x01:
            out += r.take(1)
        elif tag in (0x03, 0x13):
            out += r.take(8)
        elif tag in (0x04, 0x14):
            value = r.string()
            if value is not None:
                value = fn(value)
            _w_string(out, value)
        elif tag != 0x00:
            raise ValueError(f"unknown constant tag {tag:#x}")
    n = r.int()
    out += struct.pack("<i", n) + r.take(2 * n)  # upvalues
    n = r.int()
    out += struct.pack("<i", n)
    for _ in range(n):
        _function(r, out, fn)
    n = r.int()
    out += struct.pack("<i", n) + r.take(4 * n)  # lineinfo
    n = r.int()
    out += struct.pack("<i", n)
    for _ in range(n):
        _w_string(out, r.string())
        out += r.take(8)
    n = r.int()
    out += struct.pack("<i", n)
    for _ in range(n):
        _w_string(out, r.string())


def map_strings(chunk: bytes, fn) -> bytes:
    """Return ``chunk`` with every constant string replaced by ``fn(bytes) -> bytes``."""
    if not chunk.startswith(HEADER):
        raise ValueError("not Lua 5.3 bytecode with 64-bit sizes")
    r = _Reader(chunk)
    out = bytearray(r.take(len(HEADER) + 16 + 1))  # header, LUAC_INT, LUAC_NUM, sizeupvalues
    _function(r, out, fn)
    if r.pos != len(chunk):
        raise ValueError("trailing data after Lua function")
    return bytes(out)


def strings(chunk: bytes) -> list[bytes]:
    found: list[bytes] = []
    map_strings(chunk, lambda s: found.append(s) or s)
    return found
