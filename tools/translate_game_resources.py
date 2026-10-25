#!/usr/bin/env python3
"""Extract, locally translate, and rebuild Stella Sora English Unity bundles."""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

DEFAULT_MODEL = Path(os.environ.get("LOCALAPPDATA", "")) / "StellaSoraESPatch/models/nllb-200-3.3B-ct2-int8"
TIMING = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->")
PROTECTED = re.compile(r"</?[^<>]+>|\{[^{}]*\}|%\d*\$?[a-zA-Z]|\\[nrt]|\[[A-Z_][A-Z_0-9]*\]|&Param\d+&|##[^#]+#\d+#|[\x00-\x1f]")
PROTECTED_SPLIT = re.compile(f"({PROTECTED.pattern})")
GLOSSARY = {
    "Novian": "Noviano", "Novians": "Novianos",
    "Trekker": "Trekker", "Trekkers": "Trekkers",
    "Monolith": "monolito", "Monoliths": "monolitos",
    "Artifact": "artefacto", "Artifacts": "artefactos",
    "City": "ciudad", "Cities": "ciudades",
    "the Cities": "las ciudades", "The Cities": "las ciudades",
    "Star Tower": "Torre Estelar", "Wild Spirit": "Espíritu salvaje",
    "The Wild": "la naturaleza salvaje",
    "the Wild": "la naturaleza salvaje", "Wild": "naturaleza salvaje",
}
TERM_RE = re.compile(r"\b(?:Star Tower|Wild Spirit|the Cities|the Wild|Wild|Novians?|Trekkers?|Monoliths?|Artifacts?|Cities|City)\b", re.I)
LATIN_LETTER = re.compile(r"[A-Za-z]")
CJK_TEXT = re.compile(r"[\u2e80-\u2eff\u3000-\u30ff\u3100-\u31bf\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
NOISE_WORDS = {"mowang", "uid", "new text", "new tex"}
FIXED_TRANSLATIONS = {
    "Star Pluck Conquest\n": "Conquista de las estrellas\n",
    "Clear 3 different Overlord Stages of Difficulty 6 with max stars in Tier-up Trial mode":
        "Completa con todas las estrellas 3 etapas Overlord de dificultad 6 en el modo Prueba de Ascenso",
    "Battle Stage": "Etapa de combate",
    "Lucky Treasure Shovel": "Pala del tesoro afortunado",
    "Cooldown": "Tiempo de recarga",
    "Reach Authorization Lv.20 to unlock": "Alcanza el nivel de autorización 20 para desbloquear",
    "Conmplete": "Completo",
    "MISS": "FALLO",
    "Miss": "Fallo",
    "Great": "Genial",
    "BOSS Is Incoming": "¡Se acerca un jefe!",
    "High Threat Wave Of Monsters Is Incoming": "¡Se acerca una oleada de monstruos de alta peligrosidad!",
    "A Large Wave Of Monsters Is Incoming": "¡Se acerca una gran oleada de monstruos!",
    "Remain Time:12h26min": "Tiempo restante: 12 h 26 min",
    "Beta": "Beta",
    "Level Objective：": "Objetivo de nivel:",
    "Interacts With": "Interactúa con",
    "Activate Quick Battle": "Activa el combate rápido",
    "TAP TO START": "TOCA PARA EMPEZAR",
    "Exalted Puppets：Copplius&Qlvmpia/Normal": "Marionetas exaltadas: Copplius y Qlvmpia (Normal)",
    "There Has Been A Chaotic Entry Into The Travel Team": "Se ha producido una entrada caótica en el equipo de viaje",
    "Chin": "Mentón",
    "Monster": "Monstruo",
    "Lv 9": "Nv. 9", "Lv.5": "Nv. 5", "Lv.1": "Nv. 1",
    "Lv.23333": "Nv. 23333", "Lv.10": "Nv. 10", "Lv.2": "Nv. 2", "Lv.0": "Nv. 0",
    "Available at Authorization Lvl 8": "Disponible al alcanzar el nivel de autorización 8",
    "Select your apperance": "Elige tu apariencia",
    "Win Steak": "Racha de victorias",
    "Garden of Parasite": "Jardín de parásitos",
    "Clear the prerequisite stage to unlock": "Completa la etapa previa para desbloquear",
    "Chapter of Umbra": "Capítulo de Umbra",
}


def load_translator(model_dir: Path, device: str):
    # The Windows CTranslate2 wheel dynamically loads these NVIDIA runtime DLLs.
    handles = []
    if os.name == "nt":
        site_packages = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
        for relative in ("cublas/bin", "cuda_runtime/bin"):
            dll_dir = site_packages / relative
            if dll_dir.is_dir():
                handles.append(os.add_dll_directory(str(dll_dir)))
                os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
    import ctranslate2
    from tokenizers import Tokenizer

    if not (model_dir / "model.bin").is_file() or not (model_dir / "tokenizer.json").is_file():
        raise RuntimeError(f"No encuentro el modelo NLLB CTranslate2 en {model_dir}")
    if device == "cuda" and ctranslate2.get_cuda_device_count() < 1:
        raise RuntimeError("CTranslate2 no ve una GPU CUDA; revisa el runtime CUDA/cuBLAS de NVIDIA.")
    translator = ctranslate2.Translator(
        str(model_dir), device=device,
        compute_type="int8_float16" if device == "cuda" else "int8",
    )
    tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    return translator, tokenizer, handles


def prepare_text(text: str) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}

    def glossary_value(match: re.Match[str]) -> str:
        return glossary_term(match.group(0))

    def substitute(pattern: re.Pattern[str], value_for_match) -> None:
        nonlocal text

        def apply(match: re.Match[str]) -> str:
            marker = f"ZXQPH{len(replacements):04d}QXZ"
            replacements[marker] = value_for_match(match) if value_for_match else match.group(0)
            return marker

        text = pattern.sub(apply, text)

    # Put terminology directly in the source text. Placeholder tokens for
    # glossary words were often dropped by NLLB, causing whole strings to fall
    # back to English even when the format markers were preserved.
    text = TERM_RE.sub(glossary_value, text)
    substitute(PROTECTED, None)
    return text, replacements


def glossary_term(source: str) -> str:
    target = GLOSSARY.get(source.lower().title(), GLOSSARY.get(source, source))
    if source.islower():
        return target.lower()
    if source.isupper():
        return target.upper()
    return target


def translate_strings(
    strings: list[str], translator, tokenizer, batch_size: int,
    checkpoint_path: Path | None = None,
) -> tuple[dict[str, str], list[str]]:
    # English bundles also contain Chinese/Japanese names and numeric labels.
    # Keep those untouched instead of feeding them to the English source side.
    def is_translatable(source: str) -> bool:
        value = source.strip()
        if len(value) < 4 or not LATIN_LETTER.search(value) or CJK_TEXT.search(value):
            return False
        if PROTECTED.search(value) or value.lower().rstrip(":") in NOISE_WORDS:
            return False
        if re.fullmatch(r"(?:BT|BE)-?\d+", value) or (re.fullmatch(r"[A-Z]{2,}", value) and value not in {"MISS", "FEVER", "RANK"}) or re.fullmatch(r"\d+[A-Za-z]+|[A-Za-z]{1,4}\d+", value):
            return False
        if re.fullmatch(r"\+?\d+(?:\.\d+)?[A-Za-z]{1,2}(?:\.{2,}|…)?", value):
            return False
        if re.fullmatch(r"[Tt]a{3,}", value):
            return False
        compact = re.sub(r"[^A-Za-z0-9]", "", value).lower()
        if not compact or re.search(r"([a-z])\1{3,}", compact):
            return False
        if len(compact) > 12 and len(set(compact)) <= 5:
            return False
        for size in range(1, min(80, len(compact) // 2) + 1):
            if len(compact) % size == 0 and compact[:size] * (len(compact) // size) == compact:
                return False
        return True

    all_unique = list(dict.fromkeys(s for s in strings if isinstance(s, str) and is_translatable(s)))
    translated: dict[str, str] = {
        source: glossary_term(source.strip())
        for source in all_unique
        if source.strip().lower().title() in GLOSSARY and source.strip() == source
    }
    if checkpoint_path and checkpoint_path.is_file():
        try:
            checkpoint_translations = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            checkpoint_translations = {}
        translated.update({
            key: value for key, value in checkpoint_translations.items()
            if key in all_unique and isinstance(value, str) and key not in translated
        })
        if translated:
            print(f"Checkpoint recuperado: {len(translated)}/{len(all_unique)} textos", flush=True)
    unique = [source for source in all_unique if source not in translated]
    prepared: list[str] = []
    restore_maps: list[dict[str, str]] = []
    for source in unique:
        value, restore = prepare_text(source)
        prepared.append(value)
        restore_maps.append(restore)
    tokenized = [tokenizer.encode("eng_Latn " + value).tokens for value in prepared]
    order = sorted(range(len(unique)), key=lambda index: len(tokenized[index]))
    unique = [unique[index] for index in order]
    restore_maps = [restore_maps[index] for index in order]
    tokenized = [tokenized[index] for index in order]
    issues: list[str] = []
    start = 0
    batch_limit = max(1, batch_size)
    batches_since_checkpoint = 0
    while start < len(unique):
        max_length = max(len(tokens) for tokens in tokenized[start:start + batch_limit])
        token_budget_batch = max(1, 2048 // max_length)
        active_batch_size = min(batch_limit, token_budget_batch)
        source_batch = unique[start:start + active_batch_size]
        token_batch = tokenized[start:start + active_batch_size]
        try:
            results = translator.translate_batch(
                token_batch,
                target_prefix=[["spa_Latn"] for _ in token_batch],
                beam_size=1,
                max_decoding_length=512,
            )
        except RuntimeError as error:
            if "out of memory" not in str(error).lower():
                raise
            gc.collect()
            if active_batch_size > 1:
                batch_limit = max(1, active_batch_size // 2)
                print(f"VRAM llena; reduzco lote máximo a {batch_limit} y reintento", flush=True)
                if checkpoint_path:
                    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                    checkpoint_path.write_text(json.dumps(translated, ensure_ascii=False), encoding="utf-8")
                continue
            source = source_batch[0]
            translated[source] = source
            issues.append(f"Conservada sin traducir (sin memoria de GPU): {source[:180]}")
            start += 1
            if checkpoint_path:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint_path.write_text(json.dumps(translated, ensure_ascii=False), encoding="utf-8")
            continue
        for offset, result in enumerate(results):
            index = start + offset
            tokens = result.hypotheses[0][1:]
            output = tokenizer.decode(
                [tokenizer.token_to_id(token) for token in tokens],
                skip_special_tokens=True,
            ).strip()
            missing = [marker for marker in restore_maps[index] if marker not in output]
            if missing or not output:
                issues.append(f"Conservada sin traducir (falló marcador): {unique[index][:180]}")
                output = unique[index]
            else:
                for marker, original in restore_maps[index].items():
                    output = output.replace(marker, original)
            translated[unique[index]] = output
        start += len(source_batch)
        finished = len(all_unique) - len(unique) + start
        print(f"  {finished}/{len(all_unique)} textos", flush=True)
        batches_since_checkpoint += 1
        del results
        if checkpoint_path and batches_since_checkpoint >= 4:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(json.dumps(translated, ensure_ascii=False), encoding="utf-8")
            batches_since_checkpoint = 0
    if checkpoint_path:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps(translated, ensure_ascii=False), encoding="utf-8")
    return translated, issues


def translate_segmented_strings(strings: list[str], translator, tokenizer, batch_size: int) -> tuple[dict[str, str], list[str]]:
    """Translate text around Unity tags/control markers while preserving markers exactly."""
    special = list(dict.fromkeys(
        value for value in strings
        if isinstance(value, str) and PROTECTED.search(value)
        and LATIN_LETTER.search(value) and not CJK_TEXT.search(value)
    ))
    parts_by_source = {source: PROTECTED_SPLIT.split(source) for source in special}
    segments = [
        part for parts in parts_by_source.values() for part in parts
        if part and not PROTECTED.fullmatch(part)
    ]
    segment_map, issues = translate_strings(segments, translator, tokenizer, batch_size)
    rebuilt = {}
    for source, parts in parts_by_source.items():
        rebuilt[source] = "".join(
            part if PROTECTED.fullmatch(part or "") else segment_map.get(part, part)
            for part in parts
        )
    return rebuilt, issues


def extract_bundles(root: Path, output: Path) -> int:
    import UnityPy

    bundles = sorted(root.glob("*.en.unity3d"))
    if not bundles:
        raise RuntimeError(f"No hay archivos *.en.unity3d en {root}")
    strings: dict[str, None] = {}
    occurrences = 0
    for number, path in enumerate(bundles, 1):
        env = UnityPy.load(str(path))
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                text = getattr(obj.read(), "m_text", None)
            except Exception:
                continue
            if isinstance(text, str) and text.strip():
                strings.setdefault(text, None)
                occurrences += 1
        if number % 25 == 0 or number == len(bundles):
            print(f"  {number}/{len(bundles)} bundles · {occurrences} componentes · {len(strings)} textos únicos", flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "format": "stella-sora-english-strings",
        "sourceRoot": str(root),
        "bundleCount": len(bundles),
        "textObjectCount": occurrences,
        "strings": list(strings),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Catálogo guardado: {output}")
    return len(strings)


def translate_catalog(catalog_path: Path, output: Path, model_dir: Path, device: str, batch_size: int) -> None:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    translator, tokenizer, _handles = load_translator(model_dir, device)
    print(f"Motor: NLLB-200 3.3B CTranslate2 · {translator.device} · {translator.compute_type}")
    strings = catalog.get("strings")
    if not isinstance(strings, list):
        raise RuntimeError("Catálogo inválido; falta la lista strings.")
    mapping, issues = translate_strings(strings, translator, tokenizer, batch_size)
    for source, target in FIXED_TRANSLATIONS.items():
        if source in mapping:
            mapping[source] = target
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "format": "stella-sora-spanish-strings",
        "engine": "NLLB-200 3.3B · CTranslate2 CUDA · int8_float16",
        "sourceCatalog": str(catalog_path),
        "translations": mapping,
        "issues": issues,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Traducciones guardadas: {len(mapping)} · incidencias de marcadores: {len(issues)}")


def apply_bundles(root: Path, translations_path: Path, output_root: Path, pack: str) -> None:
    import UnityPy

    payload = json.loads(translations_path.read_text(encoding="utf-8"))
    mapping = payload.get("translations")
    if not isinstance(mapping, dict) or not mapping:
        raise RuntimeError("No hay traducciones válidas para aplicar.")
    output_root.mkdir(parents=True, exist_ok=True)
    bundles = sorted(root.glob("*.en.unity3d"))
    total = 0
    changed_bundles = 0
    for number, path in enumerate(bundles, 1):
        env = UnityPy.load(str(path))
        bundle_changes = 0
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                data = obj.read()
                source = getattr(data, "m_text", None)
            except Exception:
                continue
            translated = mapping.get(source) if isinstance(source, str) else None
            if translated and translated != source:
                data.m_text = translated
                obj.save_typetree(data)
                bundle_changes += 1
        if bundle_changes:
            # UnityPy saves the rebuilt bundle under its original basename.
            env.save(pack=pack, out_path=str(output_root))
            total += bundle_changes
            changed_bundles += 1
            rebuilt = output_root / path.name
            check = UnityPy.load(str(rebuilt))
            seen = []
            for obj in check.objects:
                if obj.type.name != "MonoBehaviour":
                    continue
                try:
                    seen.append(getattr(obj.read(), "m_text", None))
                except Exception:
                    continue
            if not any(value in mapping.values() for value in seen):
                raise RuntimeError(f"No pude verificar el bundle reconstruido: {rebuilt}")
        if number % 25 == 0 or number == len(bundles):
            print(f"  {number}/{len(bundles)} bundles · {changed_bundles} reconstruidos · {total} textos", flush=True)
    print(f"Bundles listos: {changed_bundles} · componentes actualizados: {total} · carpeta: {output_root}")


def translate_srt(source_root: Path, output_root: Path, model_dir: Path, device: str, batch_size: int) -> None:
    translator, tokenizer, _handles = load_translator(model_dir, device)
    files = sorted(source_root.glob("*.srt"))
    if not files:
        raise RuntimeError(f"No hay subtítulos SRT en {source_root}")
    all_cue_lines: list[str] = []
    parsed: list[tuple[Path, list[str], list[int]]] = []
    for path in files:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        indices: list[int] = []
        for index, line in enumerate(lines):
            if TIMING.match(line):
                pos = index + 1
                while pos < len(lines) and lines[pos].strip():
                    indices.append(pos)
                    pos += 1
        all_cue_lines.extend(lines[i] for i in indices)
        parsed.append((path, lines, indices))
    mapping, issues = translate_strings(all_cue_lines, translator, tokenizer, batch_size)
    segmented, segmented_issues = translate_segmented_strings(all_cue_lines, translator, tokenizer, batch_size)
    mapping.update(segmented)
    issues.extend(segmented_issues)
    output_root.mkdir(parents=True, exist_ok=True)
    for path, lines, indices in parsed:
        for index in indices:
            lines[index] = mapping.get(lines[index], lines[index])
        # Keep the game's original resource name so its subtitle loader finds it.
        (output_root / path.name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Subtítulos traducidos: {len(files)} archivos · {len(mapping)} líneas únicas · incidencias: {len(issues)}")


def translate_json_tree(source_root: Path, output_root: Path, model_dir: Path, device: str, batch_size: int) -> None:
    translator, tokenizer, _handles = load_translator(model_dir, device)
    files = sorted(source_root.rglob("*.json"))
    if not files:
        raise RuntimeError(f"No hay archivos JSON en {source_root}")
    parsed: list[tuple[Path, object]] = []
    strings: list[str] = []

    def collect(value) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        parsed.append((path, data))
        collect(data)

    print(f"Motor: NLLB-200 3.3B CTranslate2 · {translator.device} · {translator.compute_type}")
    report_path = output_root / "_translation-report.json"
    prior_issues: list[str] = []
    if report_path.is_file():
        try:
            prior_issues = json.loads(report_path.read_text(encoding="utf-8")).get("issues", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            prior_issues = []
    checkpoint = output_root.parent / f".{output_root.name}-checkpoint.json"
    mapping, issues = translate_strings(strings, translator, tokenizer, batch_size, checkpoint)
    for source, target in FIXED_TRANSLATIONS.items():
        if source in mapping:
            mapping[source] = target
    # Only retry sources that actually failed marker validation in the first
    # pass. Segmenting every markup-bearing table string needlessly re-translates
    # tens of thousands of already valid strings.
    failed_prefixes = {issue.partition(": ")[2] for issue in prior_issues}
    marker_retry = [
        source for source in dict.fromkeys(strings)
        if isinstance(source, str) and source[:180] in failed_prefixes
    ]
    segmented, segmented_issues = translate_segmented_strings(marker_retry, translator, tokenizer, batch_size)
    mapping.update(segmented)
    resolved_prefixes = {source[:180] for source in segmented}
    prior_issues = [issue for issue in prior_issues if issue.partition(": ")[2] not in resolved_prefixes]
    issues = list(dict.fromkeys(prior_issues + issues + segmented_issues))

    def replace(value):
        if isinstance(value, str):
            return mapping.get(value, value)
        if isinstance(value, dict):
            return {key: replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        return value

    for path, data in parsed:
        destination = output_root / path.relative_to(source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(replace(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "format": "stella-sora-spanish-language-map",
        "engine": "NLLB-200 3.3B · CTranslate2 CUDA · int8_float16",
        "sourceRoot": str(source_root),
        "fileCount": len(files),
        "sourceUniqueStringCount": len(set(strings)),
        "translatedStringCount": sum(source != target for source, target in mapping.items()),
        "issues": issues,
        "translations": mapping,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if checkpoint.exists():
        checkpoint.unlink()
    print(f"Tablas JSON listas: {len(files)} archivos · {report['translatedStringCount']} cadenas traducidas · marcadores no seguros: {len(issues)}")


def translate_bubble_tree(source_root: Path, output_root: Path, model_dir: Path, device: str, batch_size: int) -> None:
    """Translate only dialogue under BubbleData `text` fields, preserving timing/animation data."""
    translator, tokenizer, _handles = load_translator(model_dir, device)
    files = sorted(source_root.rglob("*.json"))
    if not files:
        raise RuntimeError(f"No hay archivos JSON en {source_root}")
    parsed: list[tuple[Path, object]] = []
    strings: list[str] = []

    def collect(value, in_text: bool = False) -> None:
        if isinstance(value, str):
            if in_text:
                strings.append(value)
        elif isinstance(value, dict):
            for key, child in value.items():
                collect(child, in_text or key == "text")
        elif isinstance(value, list):
            for child in value:
                collect(child, in_text)

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        parsed.append((path, data))
        collect(data)

    print(f"Motor: NLLB-200 3.3B CTranslate2 · {translator.device} · {translator.compute_type}")
    checkpoint = output_root.parent / f".{output_root.name}-checkpoint.json"
    mapping, issues = translate_strings(strings, translator, tokenizer, batch_size, checkpoint)
    for source, target in FIXED_TRANSLATIONS.items():
        if source in mapping:
            mapping[source] = target
    segmented, segmented_issues = translate_segmented_strings(strings, translator, tokenizer, batch_size)
    mapping.update(segmented)
    issues.extend(segmented_issues)

    def replace(value, in_text: bool = False):
        if isinstance(value, str):
            return mapping.get(value, value) if in_text else value
        if isinstance(value, dict):
            return {key: replace(child, in_text or key == "text") for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child, in_text) for child in value]
        return value

    for path, data in parsed:
        destination = output_root / path.relative_to(source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(replace(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "format": "stella-sora-spanish-bubble-dialogue",
        "engine": "NLLB-200 3.3B · CTranslate2 CUDA · int8_float16",
        "sourceRoot": str(source_root),
        "fileCount": len(files),
        "sourceUniqueStringCount": len(set(strings)),
        "translatedStringCount": sum(source != target for source, target in mapping.items()),
        "issues": issues,
        "translations": mapping,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "_translation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if checkpoint.exists():
        checkpoint.unlink()
    print(f"Diálogos listos: {len(files)} archivos · {report['translatedStringCount']} cadenas traducidas · marcadores no seguros: {len(issues)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_extract = sub.add_parser("extract", help="extrae m_text de los bundles ingleses")
    p_extract.add_argument("bundle_root", type=Path)
    p_extract.add_argument("catalog", type=Path)
    p_translate = sub.add_parser("translate", help="traduce un catálogo con NLLB/CTranslate2")
    p_translate.add_argument("catalog", type=Path)
    p_translate.add_argument("output", type=Path)
    p_apply = sub.add_parser("apply", help="reconstruye los bundles con traducciones")
    p_apply.add_argument("bundle_root", type=Path)
    p_apply.add_argument("translations", type=Path)
    p_apply.add_argument("output_root", type=Path)
    p_apply.add_argument("--pack", choices=("none", "lz4"), default="lz4")
    p_srt = sub.add_parser("srt", help="traduce subtítulos con el mismo modelo local")
    p_srt.add_argument("source_root", type=Path)
    p_srt.add_argument("output_root", type=Path)
    p_json = sub.add_parser("json-tree", help="traduce tablas JSON manteniendo sus claves")
    p_json.add_argument("source_root", type=Path)
    p_json.add_argument("output_root", type=Path)
    p_bubble = sub.add_parser("bubble-tree", help="traduce diálogos BubbleData conservando animaciones y tiempos")
    p_bubble.add_argument("source_root", type=Path)
    p_bubble.add_argument("output_root", type=Path)
    for child in (p_translate, p_srt, p_json, p_bubble):
        child.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
        child.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
        child.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if args.command == "extract":
        extract_bundles(args.bundle_root, args.catalog)
    elif args.command == "translate":
        translate_catalog(args.catalog, args.output, args.model_dir, args.device, args.batch_size)
    elif args.command == "apply":
        apply_bundles(args.bundle_root, args.translations, args.output_root, args.pack)
    elif args.command == "srt":
        translate_srt(args.source_root, args.output_root, args.model_dir, args.device, args.batch_size)
    elif args.command == "json-tree":
        translate_json_tree(args.source_root, args.output_root, args.model_dir, args.device, args.batch_size)
    elif args.command == "bubble-tree":
        translate_bubble_tree(args.source_root, args.output_root, args.model_dir, args.device, args.batch_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
