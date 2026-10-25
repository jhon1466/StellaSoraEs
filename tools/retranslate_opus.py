#!/usr/bin/env python3
"""Retranslate every Stella Sora text with OPUS-MT tc-big en->es on CUDA.

NLLB skipped long strings (about half the text stayed English) and translated
without sentence context. This splits each string into lines and sentences,
keeps Unity tags/format markers outside the model, translates all unique
sentences in one sorted GPU pass and rebuilds the strings.

  python tools/retranslate_opus.py json-tree <en_US dir> <es_ES dir>
  python tools/retranslate_opus.py bubble <BubbleData.json> <out.json>
  python tools/retranslate_opus.py srt <dir> <out dir>
  python tools/retranslate_opus.py catalog <catalog.json> <out translations.json>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from translate_game_resources import CJK_TEXT, FIXED_TRANSLATIONS, TIMING
from translate_game_resources import PROTECTED as BASE_PROTECTED

# Game markers plus symbols missing from the OPUS vocabulary (they decode as ⁇).
PROTECTED = re.compile(
    BASE_PROTECTED.pattern
    + r"|==[A-Z_0-9]+==|#[A-Za-z][A-Za-z' ]*#|[≤≥<>=→←↑↓★☆①-⑳【】「」『』《》•·×％＋~^|`{}＃＆＊/\\]"
)
PROTECTED_SPLIT = re.compile(f"({PROTECTED.pattern})")
ASCII_PUNCT = str.maketrans({"：": ": ", "，": ", ", "。": ". ", "！": "! ", "？": "? ", "（": "(", "）": ")", "…": "...", "—": " - ", "–": " - "})
# English rewrites so the model picks the game meaning ("Clear" = complete a stage).
SOURCE_FIXES = [
    (re.compile(r"^Clear(?= )"), "Complete"),
    (re.compile(r"\bClear time\b", re.I), "completion time"),
    (re.compile(r"\bCleared\b"), "Completed"),
]
# Character names stay in the original (the model turns Amber into Ámbar, Firefly into Luciérnaga).
# Each name is swapped for an invented token the model copies verbatim, then restored.
NAMES = json.loads((Path(__file__).with_name("character_names.json")).read_text(encoding="utf-8"))
NAME_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) for n in sorted(NAMES, key=len, reverse=True)) + r")(?![\w-])")
PSEUDONYMS = ["Zorvak", "Quelmir", "Vexdor", "Tarnis", "Kolvenz", "Drusmir", "Pelvak", "Yorkath", "Brannox", "Selvik"]
DEFAULT_MODEL = Path(os.environ.get("LOCALAPPDATA", "")) / "StellaSoraESPatch/models/opus-mt-tc-big-en-es-ct2"
SENTENCE_END = re.compile(r"(?<=[.!?…。])\s+(?=[\"'“(\[A-Z0-9¡¿])")
EDGE = re.compile(r"^(\s*)(.*?)(\s*)$", re.S)
WORD = re.compile(r"[A-Za-z]{2,}")
# Game terms the model mistranslates; applied to the Spanish output.
POST_FIXES = [
    (re.compile(r"\bNivel\.\s*(\d)"), r"Nv. \1"),
    (re.compile(r"\bLv\.\s*(\d)"), r"Nv. \1"),
    (re.compile(r"\bEspíritu salvaje\b"), "Espíritu Salvaje"),
    (re.compile(r"\bLa Salvaje\b"), "lo Salvaje"),
    (re.compile(r"\bTrekkers?\b"), lambda m: m.group(0)),
]
KEEP = {"ATK", "DEF", "HP", "DMG", "CRIT", "SP", "EX", "UI", "OK", "VS", "BOSS", "MAX"}


def translatable(unit: str) -> bool:
    if not WORD.search(unit) or CJK_TEXT.search(unit):
        return False
    if unit.upper() == unit and len(unit) <= 6 and unit.replace(" ", "").isalpha():
        return unit not in KEEP and len(unit) > 3
    if re.fullmatch(r"[A-Za-z_]+\d+[A-Za-z0-9_]*|[a-z]+(?:_[a-z0-9]+)+|https?://\S+", unit):
        return False
    return True


def units_of(text: str) -> list[str]:
    """Split a source string into translatable units (protected parts excluded)."""
    out = []
    for part in PROTECTED_SPLIT.split(text):
        if not part or PROTECTED.fullmatch(part):
            continue
        for line in part.split("\n"):
            for sentence in SENTENCE_END.split(line):
                core = sentence.strip()
                if core and translatable(core):
                    out.append(core)
    return out


def rebuild(text: str, table: dict[str, str]) -> str:
    pieces = []
    for part in PROTECTED_SPLIT.split(text):
        if not part or PROTECTED.fullmatch(part):
            pieces.append(part)
            continue
        lines = []
        for line in part.split("\n"):
            sentences = SENTENCE_END.split(line)
            seps = SENTENCE_END.findall(line)
            rebuilt = []
            for i, sentence in enumerate(sentences):
                lead, core, trail = EDGE.match(sentence).groups()
                rebuilt.append(lead + table.get(core, core) + trail)
                if i < len(seps):
                    rebuilt.append(seps[i])
            lines.append("".join(rebuilt))
        pieces.append("\n".join(lines))
    return "".join(pieces)


class Engine:
    def __init__(self, model: Path, device: str):
        if os.name == "nt":
            nvidia = Path(sys.prefix) / "Lib/site-packages/nvidia"
            for rel in ("cublas/bin", "cuda_runtime/bin"):
                if (nvidia / rel).is_dir():
                    os.environ["PATH"] = str(nvidia / rel) + os.pathsep + os.environ["PATH"]
        import ctranslate2
        import sentencepiece

        self.translator = ctranslate2.Translator(
            str(model), device=device, compute_type="float16" if device == "cuda" else "int8"
        )
        self.src = sentencepiece.SentencePieceProcessor(model_file=str(model / "source.spm"))
        self.tgt = sentencepiece.SentencePieceProcessor(model_file=str(model / "target.spm"))

    def translate(self, units: list[str]) -> dict[str, str]:
        unique = list(dict.fromkeys(units))
        prepared = []
        names: list[dict[str, str]] = []
        for unit in unique:
            text = unit.translate(ASCII_PUNCT)
            for pattern, repl in SOURCE_FIXES:
                text = pattern.sub(repl, text)
            found = list(dict.fromkeys(NAME_RE.findall(text)))[: len(PSEUDONYMS)]
            swap = {PSEUDONYMS[i]: name for i, name in enumerate(found)}
            for token, name in swap.items():
                text = re.sub(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", token, text)
            prepared.append(text)
            names.append(swap)
        tokens = [self.src.encode(u, out_type=str)[:400] + ["</s>"] for u in prepared]
        order = sorted(range(len(unique)), key=lambda i: len(tokens[i]))
        result: dict[str, str] = {}
        started = time.time()
        step = 4096
        for start in range(0, len(order), step):
            chunk = order[start : start + step]
            hyps = self.translator.translate_batch(
                [tokens[i] for i in chunk],
                max_batch_size=4096,
                batch_type="tokens",
                beam_size=4,
                max_decoding_length=512,
                repetition_penalty=1.1,
                no_repeat_ngram_size=6,
            )
            for i, hyp in zip(chunk, hyps):
                text = self.tgt.decode(hyp.hypotheses[0])
                for token, name in names[i].items():
                    text = re.sub(token + r"\w*", name, text)
                result[unique[i]] = text
            print(f"  {min(start + step, len(order))}/{len(order)} frases · {time.time() - started:.0f}s", flush=True)
        repair_unknown(result)
        return {source: postfix(source, text) for source, text in result.items()}


UNKNOWN = re.compile(r"(\w*)\s?⁇\s?(\w*)")
# Letters missing from the OPUS target vocabulary; the model emits ⁇ for them.
MISSING_LETTERS = "ÚüÜÉÍÓÁÑúéíóáñ"


def repair_unknown(result: dict[str, str]) -> None:
    """Replace ⁇ with the missing letter that forms a word seen elsewhere in the output."""
    words: set[str] = set()
    for text in result.values():
        if "⁇" not in text:
            words.update(w.lower() for w in re.findall(r"\w+", text))

    def fix(match: re.Match[str]) -> str:
        head, tail = match.groups()
        for letter in MISSING_LETTERS:
            if (head + letter + tail).lower() in words:
                return head + (letter.upper() if not head else letter) + tail
        if head.lower().endswith("g") and tail[:1] in ("e", "i"):
            return head + "ü" + tail  # vergüenza, pingüino, agüita
        if not head and tail.startswith("ltim"):
            return "Ú" + tail
        return match.group(0)

    for source, text in result.items():
        if "⁇" in text:
            result[source] = UNKNOWN.sub(fix, text)


def postfix(source: str, text: str) -> str:
    text = text.strip()
    if not text or "⁇" in text or len(text) > 4 * len(source) + 40:
        return source  # degenerate output: keep English rather than garbage
    for pattern, repl in POST_FIXES:
        text = pattern.sub(repl, text)
    if source[:1].islower() and text[:1].isupper() and not text[:2].isupper():
        text = text[0].lower() + text[1:]
    return text


def translate_values(strings: list[str], engine: Engine) -> dict[str, str]:
    unique = [s for s in dict.fromkeys(strings) if isinstance(s, str)]
    units = [u for s in unique for u in units_of(s)]
    print(f"{len(unique)} textos · {len(set(units))} frases únicas")
    table = engine.translate(units)
    mapping = {s: rebuild(s, table) for s in unique}
    mapping.update({k: v for k, v in FIXED_TRANSLATIONS.items() if k in mapping})
    return mapping


def walk(value, fn, in_text=None):
    """Apply fn to strings; in_text=None means every string, else only under 'text' keys."""
    if isinstance(value, str):
        return fn(value) if in_text in (None, True) else value
    if isinstance(value, dict):
        return {k: walk(v, fn, None if in_text is None else (in_text or k == "text")) for k, v in value.items()}
    if isinstance(value, list):
        return [walk(v, fn, in_text) for v in value]
    return value


def collect(value, in_text=None) -> list[str]:
    found: list[str] = []
    walk(value, lambda s: found.append(s) or s, in_text)
    return found


def dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("json-tree", "bubble", "srt", "catalog"))
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    engine = Engine(args.model_dir, args.device)

    if args.mode == "json-tree":
        files = {p: json.loads(p.read_text("utf-8-sig")) for p in sorted(args.source.glob("*.json")) if not p.name.startswith("_")}
        mapping = translate_values([s for d in files.values() for s in collect(d)], engine)
        for path, data in files.items():
            dump(args.output / path.name, walk(data, lambda s: mapping.get(s, s)))
    elif args.mode == "bubble":
        data = json.loads(args.source.read_text("utf-8-sig"))
        mapping = translate_values(collect(data, False), engine)
        dump(args.output, walk(data, lambda s: mapping.get(s, s), False))
    elif args.mode == "srt":
        lines_by_file = {p: p.read_text("utf-8-sig").splitlines() for p in sorted(args.source.glob("*.srt"))}
        cues = [l for lines in lines_by_file.values() for l in lines if l.strip() and not TIMING.match(l) and not l.strip().isdigit()]
        mapping = translate_values(cues, engine)
        for path, lines in lines_by_file.items():
            args.output.mkdir(parents=True, exist_ok=True)
            out = [mapping.get(l, l) if l.strip() and not TIMING.match(l) and not l.strip().isdigit() else l for l in lines]
            (args.output / path.name).write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    else:
        catalog = json.loads(args.source.read_text("utf-8"))
        mapping = translate_values(catalog["strings"], engine)
        dump(args.output, {"format": "stella-sora-spanish-strings", "engine": "OPUS-MT tc-big en-es · CTranslate2", "translations": mapping})
    changed = sum(k != v for k, v in mapping.items())
    print(f"Listo: {changed}/{len(mapping)} textos traducidos -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
