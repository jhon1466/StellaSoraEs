#!/usr/bin/env python3
"""Build 0.2.4 PC and Android trees from 0.2.3 with game-loadable bundles.

0.2.3 broke the boot (title video looping forever) for three reasons:
  * bundles were saved by UnityPy with flags 0xC2 instead of the game's 0x243;
  * ss_*.mani kept the original MD5/size/CRC, so Unity rejected the bundles;
  * file_versions.dat was written with CRLF, which made the game wipe versions.

This rewrites every bundle in the original layout and writes ``patch-files.json``
with the MD5/size/CRC the installers must put in file_versions.dat and the mani.
file_versions.dat itself is no longer shipped: installers edit the user's copy.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from unityfs import payload_crc, read_bundle, write_bundle

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.4"


def rewrite_bundle(path: Path) -> int:
    bundle = read_bundle(path.read_bytes())
    data = write_bundle(bundle, {node.path: bundle.node_bytes(node) for node in bundle.nodes})
    rebuilt = read_bundle(data)
    if rebuilt.data != bundle.data or rebuilt.flags != 0x243:
        raise RuntimeError(f"bundle round-trip failed: {path}")
    path.write_bytes(data)
    return payload_crc(rebuilt)


def build(platform: str) -> list[dict]:
    source = ROOT / f"translations/{platform}-v0.2.3"
    target = ROOT / f"translations/{platform}-v{VERSION}"
    if target.exists():
        raise FileExistsError(f"{target} already exists; refusing to overwrite it")
    shutil.copytree(source / "Persistent_Store", target / "Persistent_Store")
    files = []
    for path in sorted((target / "Persistent_Store").rglob("*")):
        if not path.is_file():
            continue
        crc = rewrite_bundle(path) if path.suffix == ".unity3d" else None
        data = path.read_bytes()
        files.append({
            "path": path.relative_to(target).as_posix(),
            "md5": hashlib.md5(data).hexdigest(),
            "size": len(data),
            "crc": crc,
        })
    (target / "patch-files.json").write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8", newline="\n")
    return files


def main() -> int:
    android = build("android")
    pc = build("pc")

    old = json.loads((ROOT / "translations/patch-source-full-v0.2.3.json").read_text(encoding="utf-8"))
    originals = {item["path"]: item.get("originalSha256") for item in old["files"]}
    manifest = {k: v for k, v in old.items() if k not in ("files", "resourceVersion")}
    manifest["patchVersion"] = VERSION
    manifest["files"] = []
    for item in android:
        entry = {"path": item["path"], "source": f"android-v{VERSION}/{item['path']}", "md5": item["md5"], "size": item["size"]}
        if item["crc"] is not None:
            entry["crc"] = item["crc"]
        if originals.get(item["path"]):
            entry["originalSha256"] = originals[item["path"]]
        manifest["files"].append(entry)
    out = ROOT / f"translations/patch-source-full-v{VERSION}.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Android: {len(android)} files · PC: {len(pc)} files · manifest {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
