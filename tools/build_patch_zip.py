#!/usr/bin/env python3
"""Package extracted Spanish resources in the Android installer's ZIP format."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

GAME_PACKAGE = "com.YoStarEN.StellaSora"
PATCH_FORMAT = "stella-sora-es-patch"
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def valid_destination(value: str) -> bool:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return False
    return value.startswith("Persistent_Store/") or value == "file_versions.dat"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="source manifest JSON")
    parser.add_argument("output", type=Path, help="new ZIP path")
    args = parser.parse_args()

    source_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if source_manifest.get("gamePackage") != GAME_PACKAGE:
        parser.error(f"gamePackage must be {GAME_PACKAGE}")
    for required in ("patchVersion", "clientVersion"):
        if not isinstance(source_manifest.get(required), str):
            parser.error(f"manifest requires string field {required}")
    platform = source_manifest.get("platform", "android")
    if platform != "android":
        parser.error("this tool builds Android packages only")
    entries = source_manifest.get("files")
    if not isinstance(entries, list) or not entries:
        parser.error("manifest files must be a non-empty list")

    base = args.manifest.resolve().parent
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    sources: list[tuple[str, Path]] = []
    for item in entries:
        if not isinstance(item, dict):
            parser.error("each files entry must be an object")
        destination = item.get("path")
        source = item.get("source")
        if not isinstance(destination, str) or not valid_destination(destination):
            parser.error(f"unsafe destination path: {destination!r}")
        if destination in seen:
            parser.error(f"duplicate destination: {destination}")
        seen.add(destination)
        if not isinstance(source, str):
            parser.error(f"missing source for {destination}")
        candidate = base / source
        if candidate.is_symlink():
            parser.error(f"source must not be a symlink: {source}")
        source_path = candidate.resolve()
        try:
            source_path.relative_to(base)
        except ValueError:
            parser.error(f"source must stay inside the manifest directory: {source}")
        if not source_path.is_file():
            parser.error(f"source is missing: {source}")
        original_hash = item.get("originalSha256")
        if original_hash is not None and (not isinstance(original_hash, str) or not HASH_RE.fullmatch(original_hash)):
            parser.error(f"invalid originalSha256 for {destination}")
        record = {"path": destination, "sha256": sha256_file(source_path)}
        # Installers write these into file_versions.dat and ss_and.mani.
        for key in ("md5", "size", "crc"):
            if key in item:
                record[key] = item[key]
        if original_hash:
            record["originalSha256"] = original_hash.lower()
        normalized.append(record)
        sources.append((destination, source_path))

    final_manifest = {
        "format": PATCH_FORMAT,
        "schemaVersion": 1,
        "gamePackage": GAME_PACKAGE,
        "platform": "android",
        "patchVersion": source_manifest["patchVersion"],
        "clientVersion": source_manifest["clientVersion"],
        "files": normalized,
    }
    if "resourceVersion" in source_manifest:
        if not isinstance(source_manifest["resourceVersion"], str):
            parser.error("resourceVersion must be a string")
        final_manifest["resourceVersion"] = source_manifest["resourceVersion"]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error(f"refusing to overwrite existing output: {args.output}")
    with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        bundle.writestr("manifest.json", json.dumps(final_manifest, ensure_ascii=False, indent=2) + "\n")
        for destination, source_path in sources:
            bundle.write(source_path, "payload/" + destination)
    print(f"Created {args.output} with {len(sources)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
