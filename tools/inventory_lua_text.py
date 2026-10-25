#!/usr/bin/env python3
"""List English text inside lua.arcx grouped by script folder (what still needs translating)."""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

from arcx_v66 import decode_entry, read_archive
from luac53 import strings

ENGLISH = re.compile(rb"[A-Za-z]{2,}[ ,.!?'][A-Za-z]")
SOURCE = re.compile(rb"Nova_Client\\Lua\\(Game\\[^\x00]*?\.lua)")


def script_path(chunk: bytes) -> str:
    m = SOURCE.search(chunk)
    return m.group(1).decode().replace("\\", "/") if m else "?"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    archive = read_archive(Path(sys.argv[1]))
    depth = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    chars, count, files = collections.Counter(), collections.Counter(), collections.Counter()
    samples: dict[str, str] = {}
    for i in range(len(archive.entries)):
        chunk = decode_entry(archive, i)
        if not chunk.startswith(b"\x1bLua"):
            continue
        path = script_path(chunk)
        key = "/".join(path.split("/")[:depth])
        found = [s for s in strings(chunk) if ENGLISH.search(s) and b"\\" not in s]
        if found:
            chars[key] += sum(len(s) for s in found)
            count[key] += len(found)
            files[key] += 1
            samples.setdefault(key, max(found, key=len).decode("utf-8", "replace")[:90])
    for key, value in chars.most_common(60):
        print(f"{value:>9} chars {count[key]:>6} strings {files[key]:>4} files  {key}\n           e.g. {samples[key]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
