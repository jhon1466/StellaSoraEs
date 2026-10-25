#!/usr/bin/env python3
"""Assemble PC and Android trees (0.2.6: OPUS-MT tables + story/chat lua.arcx).

Inputs come from tools/retranslate_opus.py and translate_game_resources.py apply:
  translations/data-arcx-es-0.2.5.arcx
  build/retranslate/{pc-ui,and-ui}/*.en.unity3d   (UnityPy output, re-laid out here)
  build/retranslate/srt-es/*.srt
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from prepare_patch_v024 import rewrite_bundle

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.8"
WORK = ROOT / "build/retranslate"
ARCX = ROOT / f"translations/data-arcx-es-{VERSION}.arcx"
# translate_lua_avg.py output: 0 = PC lua.arcx, 1 = Android lua.arcx.
LUA = {"pc": WORK / "lua/0-lua-lua.arcx", "android": WORK / "lua/1-Scripts-lua.arcx"}
ANDROID_ORIGINALS = Path(r"E:\Documentos\SnowBreakPS\prueba server\Nebula\com.YoStarEN.StellaSora\files")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(platform: str, bundles: Path) -> list[dict]:
    target = ROOT / f"translations/{platform}-v{VERSION}"
    if target.exists():
        raise FileExistsError(f"{target} already exists; refusing to overwrite it")
    store = target / "Persistent_Store"
    (store / "AssetBundles").mkdir(parents=True)
    (store / "Tables").mkdir()
    (store / "Videos").mkdir()
    (store / "Scripts").mkdir()
    shutil.copyfile(LUA[platform], store / "Scripts/lua.arcx")
    for bundle in bundles.glob("*.en.unity3d"):
        shutil.copyfile(bundle, store / "AssetBundles" / bundle.name)
    for srt in (WORK / "srt-es").glob("*.srt"):
        shutil.copyfile(srt, store / "Videos" / srt.name)
    shutil.copyfile(ARCX, store / "Tables/data.arcx")

    files = []
    for path in sorted(store.rglob("*")):
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
    android = build("android", WORK / "and-ui")
    pc = build("pc", WORK / "pc-ui")
    manifest = {
        "gamePackage": "com.YoStarEN.StellaSora",
        "platform": "android",
        "patchVersion": VERSION,
        "clientVersion": "2.0.0",
        "files": [],
    }
    for item in android:
        entry = {"path": item["path"], "source": f"android-v{VERSION}/{item['path']}", "md5": item["md5"], "size": item["size"]}
        if item["crc"] is not None:
            entry["crc"] = item["crc"]
        original = ANDROID_ORIGINALS / item["path"]
        if original.is_file():
            entry["originalSha256"] = sha256(original)
        manifest["files"].append(entry)
    out = ROOT / f"translations/patch-source-full-v{VERSION}.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Android: {len(android)} files · PC: {len(pc)} files · manifest {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
