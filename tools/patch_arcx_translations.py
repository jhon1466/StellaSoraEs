"""Replace English language and voice JSON entries inside Stella Sora data.arcx."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from arcx_v66 import decode_entry, read_archive, rebuild_archive


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_arcx", type=Path)
    parser.add_argument("source_language", type=Path)
    parser.add_argument("translated_language", type=Path)
    parser.add_argument("source_bubble", type=Path)
    parser.add_argument("translated_bubble", type=Path)
    parser.add_argument("output_arcx", type=Path)
    args = parser.parse_args()

    english = {
        path.name: load_json(path)
        for path in args.source_language.glob("*.json")
        if not path.name.startswith("_")
    }
    spanish = {
        name: load_json(args.translated_language / name)
        for name in english
        if (args.translated_language / name).is_file()
    }
    source_bubble = load_json(args.source_bubble)
    translated_bubble = load_json(args.translated_bubble)
    archive = read_archive(args.source_arcx)
    replacements: dict[int, bytes] = {}
    matched: dict[str, list[int]] = {}
    bubble_index: int | None = None

    for index in range(len(archive.entries)):
        plaintext = decode_entry(archive, index)
        try:
            value = json.loads(plaintext)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if value == source_bubble:
            if bubble_index is not None:
                raise RuntimeError("BubbleData matches more than one archive entry")
            bubble_index = index
            replacements[index] = (json.dumps(translated_bubble, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            continue
        for name, source_value in english.items():
            if value == source_value:
                if name not in spanish:
                    raise RuntimeError(f"Spanish translation is missing for {name}")
                matched.setdefault(name, []).append(index)
                replacements[index] = (json.dumps(spanish[name], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                break

    if not matched:
        raise RuntimeError("No English language entries matched the archive")
    if bubble_index is None:
        raise RuntimeError("BubbleData did not match any archive entry")
    rebuild_archive(args.source_arcx, args.output_arcx, replacements)

    # Reopen the result and validate every replacement against its JSON source.
    rebuilt = read_archive(args.output_arcx)
    if len(rebuilt.entries) != len(archive.entries):
        raise RuntimeError("entry count changed while rebuilding ARCX")
    for name, indexes in matched.items():
        for index in indexes:
            if json.loads(decode_entry(rebuilt, index)) != spanish[name]:
                raise RuntimeError(f"rebuilt JSON mismatch for {name} at entry {index}")
    if json.loads(decode_entry(rebuilt, bubble_index)) != translated_bubble:
        raise RuntimeError("rebuilt BubbleData mismatch")

    entry_count = sum(len(indexes) for indexes in matched.values())
    print(f"Translated language JSON files: {len(matched)}/{len(english)} · archive entries: {entry_count}")
    print(f"Translated BubbleData entry: {bubble_index}")
    print(f"Unmatched language files: {', '.join(sorted(set(english) - set(matched)))}")
    print(f"Archive: {args.output_arcx} ({args.output_arcx.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
