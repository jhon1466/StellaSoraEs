#!/usr/bin/env python3
"""Translate story, phone chats, character stories and AVG presets inside lua.arcx.

Text lives as string constants of compiled Lua 5.3 scripts under
Game/UI/Avg/_en/ (Config: STm main story, STev events, STsp character stories,
PM phone chats, DP dates, BBm bubbles, CG; Preset: contacts, AVG UI text).
Command names, ids and asset keys are identifier-like and left untouched.

  python tools/translate_lua_avg.py <lua.arcx> [<lua.arcx> ...] --out-suffix .es
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from arcx_v66 import decode_entry, read_archive, rebuild_archive
from inventory_lua_text import script_path
from luac53 import map_strings, strings
from retranslate_opus import DEFAULT_MODEL, Engine, translate_values

IDENTIFIER = re.compile(r"[A-Za-z0-9_.\-]*")
# Ruby/emphasis marks (<r=Kohaku></r>, <r=·>word</r>) split words apart; drop the tags, keep the text.
RUBY = re.compile(r"</?r(?:=[^<>]*)?>")
TAG = re.compile(r"</?[^<>]+>")
UI_TEXT_KEY = re.compile(r"(?:AVG|Menu)_\w+|SEX|==\w+==")
# Single-word dialogue worth translating even without punctuation.
SHORT_WORDS = {"Yeah", "Yes", "No", "Nope", "Okay", "Sure", "Thanks", "Sorry", "Really", "Hello", "Hi", "Bye",
               "Prologue", "Epilogue", "Interlude", "Finale", "Intermission", "Cancel", "Confirm", "Selected", "Records", "Hide", "Skip", "Auto", "Log", "Close", "Back",
               "sisters", "brothers", "lady", "lord", "Lady", "Lord", "girl", "boy", "she", "he", "her", "him"}


def is_text(value: str, path: str) -> bool:
    if not value.strip():
        return False
    if path.endswith("AvgUIText.lua") and UI_TEXT_KEY.fullmatch(value):
        return False
    if IDENTIFIER.fullmatch(value):
        return value in SHORT_WORDS
    plain = TAG.sub("", value)
    return bool(re.search(r"[A-Za-z]{2,}", plain)) and not re.fullmatch(r"[\w./\-]+", plain)


def avg_chunks(archive) -> dict[int, tuple[str, bytes]]:
    found = {}
    for index in range(len(archive.entries)):
        chunk = decode_entry(archive, index)
        if chunk.startswith(b"\x1bLua"):
            path = script_path(chunk)
            if "/Avg/_en/" in path:
                found[index] = (path, chunk)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archives", type=Path, nargs="+")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args()

    loaded = []
    texts: list[str] = []
    for path in args.archives:
        archive = read_archive(path)
        chunks = avg_chunks(archive)
        loaded.append((path, chunks))
        for script, chunk in chunks.values():
            for raw in strings(chunk):
                value = raw.decode("utf-8", "replace")
                if is_text(value, script):
                    texts.append(RUBY.sub("", value))
        print(f"{path}: {len(chunks)} scripts AVG")

    mapping = translate_values(texts, Engine(args.model_dir, "cuda"))
    left = sorted({t for t in texts if mapping.get(t, t) == t}, key=len, reverse=True)
    print(f"Sin cambios tras traducir: {len(left)}/{len(set(texts))} (nombres, onomatopeyas...)")
    for sample in left[:25]:
        print("   ", repr(sample[:100]))

    for (path, chunks), number in zip(loaded, range(len(loaded))):
        replacements = {}
        for index, (script, chunk) in chunks.items():
            def replace(raw: bytes, script=script) -> bytes:
                value = raw.decode("utf-8", "replace")
                if not is_text(value, script):
                    return raw
                return mapping.get(RUBY.sub("", value), value).encode("utf-8")

            patched = map_strings(chunk, replace)
            strings(patched)  # re-parse to validate structure
            replacements[index] = patched
        target = args.output_dir / f"{number}-{path.parent.name}-lua.arcx"
        rebuild_archive(path, target, replacements)
        rebuilt = read_archive(target)
        for index, data in replacements.items():
            if decode_entry(rebuilt, index) != data:
                raise RuntimeError(f"rebuilt entry mismatch {index} in {target}")
        print(f"Listo: {len(replacements)} scripts traducidos -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
