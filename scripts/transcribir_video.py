#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import ssl
import sys
import tempfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("transcribir_video")
ALLOWED_MODELS = ("tiny", "base", "small", "medium", "large")
ALLOWED_FORMATS = ("txt", "md", "srt", "vtt", "json")
STOPWORDS_ES = {
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
    "un", "para", "con", "no", "una", "su", "al", "lo", "como", "más", "mas",
    "pero", "sus", "le", "ya", "o", "este", "sí", "si", "porque", "esta",
    "entre", "cuando", "muy", "sin", "sobre", "también", "tambien", "me",
    "hasta", "hay", "donde", "quien", "desde", "todo", "nos", "durante",
    "todos", "uno", "les", "ni", "contra", "otros", "ese", "eso", "ante",
    "ellos", "e", "esto", "mí", "mi", "antes", "algunos", "qué", "que",
    "unos", "yo", "otro", "otras", "otra", "él", "el", "tanto", "esa",
    "estos", "mucho", "quienes", "nada", "muchos", "cual", "cuál", "poco",
    "ella", "estar", "estas", "algunas", "algo", "nosotros", "mi", "mis",
    "tu", "tú", "te", "ti", "tu", "tus", "ellas", "nosotras", "vosotros",
    "vosotras", "os", "mío", "mia", "mías", "mios", "tuyo", "tuya", "tuyas",
    "tuyos", "suyo", "suya", "suyos", "suyas", "nuestro", "nuestra", "nuestros",
    "nuestras", "vuestro", "vuestra", "vuestros", "vuestras", "esos", "esas",
    "estoy", "estás", "esta", "estamos", "estáis", "estan", "están", "esteis",
    "estéis", "esté", "estes", "estés", "estemos", "estén", "estaré", "estará",
    "estarán", "ser", "es", "son", "fue", "fueron", "era", "eran", "ha", "han",
    "había", "habian", "habían", "he", "hemos", "hoy", "ayer", "mañana",
}


@dataclass(frozen=True)
class TranscriptionConfig:
    video_path: Path
    output_path: Path
    model: str
    language: str | None
    output_format: str
    extra_formats: tuple[str, ...]
    include_timestamps: bool
    analyze: bool
    analysis_output: Path | None
    generate_notes: bool
    notes_output: Path | None
    notes_title: str | None
    notes_module: str
    notes_author: str
    template_file: Path
    overwrite: bool


class ProgressTracker:
    def __init__(self) -> None:
        self.current = -1

    def update(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        if value == self.current:
            return
        self.current = value
        print(f"\rProgreso: {value:3d}%", end="", flush=True)
        if value >= 100:
            print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe un video a texto usando Whisper."
    )
    parser.add_argument(
        "video",
        type=Path,
        help="Ruta del archivo de video a transcribir.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Ruta del fichero de salida. "
            "Si no se indica, se genera junto al video."
        ),
    )
    parser.add_argument(
        "-m",
        "--model",
        default="base",
        choices=ALLOWED_MODELS,
        help="Modelo Whisper.",
    )
    parser.add_argument(
        "-l",
        "--language",
        default="es",
        help="Codigo de idioma (por defecto: es). Usa auto para deteccion automatica.",
    )
    parser.add_argument(
        "-f",
        "--format",
        default="txt",
        choices=ALLOWED_FORMATS,
        help="Formato de salida.",
    )
    parser.add_argument(
        "--extra-formats",
        nargs="+",
        default=(),
        choices=ALLOWED_FORMATS,
        help=(
            "Formatos adicionales a generar en la misma ejecucion. "
            "Ejemplo: --format txt --extra-formats md json"
        ),
    )
    parser.add_argument(
        "--timestamps",
        action="store_true",
        help="Incluye marcas de tiempo por segmento en la salida.",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help=(
            "Activa analisis del texto transcrito y extrae tema principal y "
            "puntos clave a un fichero adicional."
        ),
    )
    parser.add_argument(
        "--analysis-output",
        type=Path,
        default=None,
        help=(
            "Ruta de salida para el analisis en Markdown. "
            "Si no se indica, se genera junto a la transcripcion principal."
        ),
    )
    parser.add_argument(
        "--generate-notes",
        action="store_true",
        help=(
            "Genera un fichero de apuntes .md estructurado siguiendo plantilla de AGENTS.md."
        ),
    )
    parser.add_argument(
        "--notes-output",
        type=Path,
        default=None,
        help="Ruta de salida para apuntes .md (por defecto: <salida>.apuntes.md).",
    )
    parser.add_argument(
        "--notes-title",
        default=None,
        help="Titulo del tema para los apuntes (opcional).",
    )
    parser.add_argument(
        "--notes-module",
        default="AUTO",
        help="Valor de categoria/modulo para front matter de apuntes.",
    )
    parser.add_argument(
        "--notes-author",
        default="Generador automatico",
        help="Autor para front matter de apuntes.",
    )
    parser.add_argument(
        "--template-file",
        type=Path,
        default=None,
        help="Ruta de plantilla Markdown (por defecto: scripts/formato.md).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Permite sobrescribir el fichero de salida si ya existe.",
    )
    parser.add_argument(
        "--log-level",
        default="ERROR",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Nivel de logging.",
    )
    parser.add_argument(
        "--ssl-cert-file",
        type=Path,
        default=None,
        help=(
            "Ruta a un fichero PEM de certificados de confianza para descargas HTTPS "
            "(util en redes corporativas)."
        ),
    )
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help=(
            "Desactiva la verificacion SSL para descarga del modelo. "
            "Solo usar como ultimo recurso."
        ),
    )
    return parser.parse_args()


def ensure_dependencies() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "No se encontro ffmpeg en el sistema. "
            "Instalalo y vuelve a ejecutar el script."
        )

    try:
        import whisper  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Falta la dependencia 'openai-whisper'. "
            "Instala con: pip install openai-whisper"
        ) from exc


def build_output_path(video_path: Path, output_arg: Path | None, output_format: str) -> Path:
    if output_arg is None:
        return video_path.with_suffix(f".{output_format}")
    return output_arg.resolve()


def build_output_paths(config: TranscriptionConfig) -> dict[str, Path]:
    paths = {config.output_format: config.output_path}
    for fmt in config.extra_formats:
        if fmt == config.output_format:
            continue
        paths[fmt] = config.output_path.with_suffix(f".{fmt}")
    return paths


def build_analysis_output_path(args: argparse.Namespace, output_path: Path) -> Path:
    if args.analysis_output is not None:
        return args.analysis_output.resolve()
    return output_path.with_suffix(".analysis.md")


def build_notes_output_path(args: argparse.Namespace, output_path: Path) -> Path:
    if args.notes_output is not None:
        return args.notes_output.resolve()
    return output_path.with_suffix(".apuntes.md")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def configure_ssl(cert_file: Path | None, insecure_ssl: bool) -> None:
    if insecure_ssl:
        ssl._create_default_https_context = ssl._create_unverified_context
        LOGGER.warning(
            "SSL sin verificacion activado. Usa esta opcion solo temporalmente."
        )
        return

    if cert_file is not None:
        cert_path = cert_file.resolve()
        if not cert_path.exists() or not cert_path.is_file():
            raise FileNotFoundError(f"No existe el fichero de certificados: {cert_path}")

        def _ssl_context_factory() -> ssl.SSLContext:
            return ssl.create_default_context(cafile=str(cert_path))

        ssl._create_default_https_context = _ssl_context_factory
        LOGGER.info("Usando certificados SSL personalizados: %s", cert_path)


def format_time(seconds: float, *, srt: bool) -> str:
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3_600_000
    total_ms -= hours * 3_600_000
    minutes = total_ms // 60_000
    total_ms -= minutes * 60_000
    secs = total_ms // 1000
    millis = total_ms - secs * 1000
    sep = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def normalize_text(text: str) -> str:
    cleaned = text.replace("\r", " ").replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def tokenize_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", text.lower())


def keyword_frequencies(text: str) -> dict[str, int]:
    freq: dict[str, int] = {}
    for token in tokenize_words(text):
        if token in STOPWORDS_ES:
            continue
        freq[token] = freq.get(token, 0) + 1
    return freq


def top_keywords(freq: dict[str, int], limit: int = 8) -> list[str]:
    pairs = sorted(freq.items(), key=lambda item: (-item[1], item[0]))
    return [k for k, _ in pairs[:limit]]


def extract_key_points(text: str, freq: dict[str, int], limit: int = 6) -> list[str]:
    sentences = split_sentences(text)
    if not sentences:
        return []

    scored: list[tuple[float, int, str]] = []
    for idx, sentence in enumerate(sentences):
        words = tokenize_words(sentence)
        if not words:
            continue
        score = 0.0
        for word in words:
            score += float(freq.get(word, 0))
        score = score / max(len(words), 1)
        scored.append((score, idx, sentence))

    best = sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
    best_sorted = sorted(best, key=lambda item: item[1])
    return [sentence for _, _, sentence in best_sorted]


def segment_tokens(text: str) -> set[str]:
    return {tok for tok in tokenize_words(text) if tok not in STOPWORDS_ES}


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def top_keywords_for_segment(text: str, limit: int = 4) -> list[str]:
    freq = keyword_frequencies(text)
    return top_keywords(freq, limit=limit)


def detect_topic_changes(
    segments: list[dict[str, Any]],
    *,
    threshold: float = 0.22,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if len(segments) < 2:
        return changes

    prev_tokens = segment_tokens(normalize_text(str(segments[0].get("text", ""))))
    for idx in range(1, len(segments)):
        seg = segments[idx]
        current_text = normalize_text(str(seg.get("text", "")))
        current_tokens = segment_tokens(current_text)
        similarity = jaccard_similarity(prev_tokens, current_tokens)

        if similarity < threshold and current_text:
            start = format_time(float(seg.get("start", 0.0)), srt=False)
            end = format_time(float(seg.get("end", 0.0)), srt=False)
            kws = top_keywords_for_segment(current_text, limit=4)
            changes.append(
                {
                    "index": idx,
                    "start": start,
                    "end": end,
                    "similarity": similarity,
                    "keywords": kws,
                    "snippet": current_text[:180],
                }
            )
        prev_tokens = current_tokens

    return changes


def summarize_segments(
    segments: list[dict[str, Any]],
    *,
    keyword_limit: int = 3,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments, start=1):
        text = normalize_text(str(seg.get("text", "")))
        if not text:
            continue
        start = format_time(float(seg.get("start", 0.0)), srt=False)
        end = format_time(float(seg.get("end", 0.0)), srt=False)
        kws = top_keywords_for_segment(text, limit=keyword_limit)
        out.append(
            {
                "index": idx,
                "start": start,
                "end": end,
                "keywords": kws,
                "text": text,
            }
        )
    return out


def build_analysis_markdown(result: dict[str, Any], config: TranscriptionConfig) -> str:
    raw_text = normalize_text(str(result.get("text", "")))
    freq = keyword_frequencies(raw_text)
    keywords = top_keywords(freq, limit=10)
    key_points = extract_key_points(raw_text, freq, limit=6)
    tema = ", ".join(keywords[:4]) if keywords else "No identificado"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    segments = result.get("segments") or []
    segment_summary = summarize_segments(segments, keyword_limit=3)
    topic_changes = detect_topic_changes(segments, threshold=0.22)

    lines = [
        "# Analisis de transcripcion",
        "",
        "## Metadatos",
        f"- **Archivo:** `{config.video_path.name}`",
        f"- **Modelo:** `Whisper {config.model}`",
        f"- **Idioma:** `{config.language if config.language else 'auto'}`",
        f"- **Generado:** `{generated_at}`",
        f"- **Segmentos detectados:** `{len(segment_summary)}`",
        f"- **Cambios de tema estimados:** `{len(topic_changes)}`",
        "",
        "## Tema principal (estimado)",
        f"- {tema}",
        "",
        "## Palabras clave",
    ]

    if keywords:
        for kw in keywords:
            lines.append(f"- {kw}")
    else:
        lines.append("- Sin datos suficientes")

    lines.extend(["", "## Puntos clave extraidos"])
    if key_points:
        for point in key_points:
            lines.append(f"- {point}")
    else:
        lines.append("- Sin puntos clave detectados")

    lines.extend(
        [
            "",
            "## Segmentos y palabras clave",
        ]
    )
    if segment_summary:
        for seg in segment_summary:
            kws = ", ".join(seg["keywords"]) if seg["keywords"] else "sin keywords"
            lines.append(
                f"- [{seg['start']} - {seg['end']}] "
                f"**Keywords:** {kws} | {seg['text'][:140]}"
            )
    else:
        lines.append("- Sin segmentos disponibles")

    lines.extend(
        [
            "",
            "## Cambios de tema detectados",
        ]
    )
    if topic_changes:
        for change in topic_changes:
            kws = ", ".join(change["keywords"]) if change["keywords"] else "sin keywords"
            lines.append(
                f"- Segmento {change['index'] + 1} "
                f"[{change['start']} - {change['end']}], "
                f"similitud={change['similarity']:.2f}, "
                f"keywords: {kws}"
            )
            lines.append(f"  - {change['snippet']}")
    else:
        lines.append("- No se detectaron cambios de tema significativos")

    lines.extend(
        [
            "",
            "## Texto base normalizado",
            "",
            raw_text if raw_text else "_Sin contenido transcrito._",
            "",
        ]
    )
    return "\n".join(lines)


def load_notes_template(template_path: Path) -> str:
    if not template_path.exists() or not template_path.is_file():
        raise FileNotFoundError(f"No existe la plantilla: {template_path}")
    content = template_path.read_text(encoding="utf-8").strip()
    if not content:
        raise RuntimeError(f"La plantilla esta vacia: {template_path}")
    return content


def infer_title_from_keywords(keywords: list[str], fallback: str) -> str:
    if not keywords:
        return fallback
    title = " ".join(word.capitalize() for word in keywords[:4])
    return title if title.strip() else fallback


def find_sentence_for_keyword(text: str, keyword: str) -> str:
    for sentence in split_sentences(text):
        if keyword.lower() in sentence.lower():
            return sentence
    return ""


def build_vocab_table(keywords: list[str], raw_text: str, limit: int = 8) -> list[str]:
    rows = ["| Término | Definición contextual |", "|---|---|"]
    for kw in keywords[:limit]:
        sentence = find_sentence_for_keyword(raw_text, kw)
        definition = sentence if sentence else f"Concepto relacionado con {kw} en la transcripción."
        rows.append(f"| **{kw}** | {definition} |")
    return rows


def build_notes_markdown(result: dict[str, Any], config: TranscriptionConfig) -> str:
    template = load_notes_template(config.template_file)
    raw_text = normalize_text(str(result.get("text", "")))
    freq = keyword_frequencies(raw_text)
    keywords = top_keywords(freq, limit=12)
    key_points = extract_key_points(raw_text, freq, limit=8)
    seg_summary = summarize_segments(result.get("segments") or [], keyword_limit=3)
    created_iso = datetime.now().strftime("%Y-%m-%d")
    created_human = datetime.now().strftime("%d/%m/%Y")

    inferred = infer_title_from_keywords(keywords, config.video_path.stem.replace("_", " ").title())
    title = config.notes_title.strip() if config.notes_title else inferred
    full_title = f"UD X - X.Y {title}"
    resumen = " ".join(key_points[:2]).strip() if key_points else f"Apuntes sobre {title}."
    objetivos = key_points[:5] if key_points else [f"Comprender {title}."]
    ideas_finales = key_points[:5] if key_points else [f"Resumen de {title}."]
    vocab_table = build_vocab_table(keywords, raw_text, limit=8)

    lines = [
        "---",
        f'title: "{full_title}"',
        f"description: {title}",
        f"summary: {resumen}",
        "authors:",
        f"    - {config.notes_author}",
        f"date: {created_iso}",
        'icon: "material/file-document-outline"',
        f"permalink: /auto/{config.video_path.stem}",
        "categories:",
        f"    - {config.notes_module}",
        "tags:",
    ]
    for tag in keywords[:4] if keywords else ["transcripcion"]:
        lines.append(f'    - "{tag}"')
    lines.extend(
        [
            "---",
            "",
            "## X.Y. Título del tema",
            "",
            f"Introducción: {resumen}",
            "",
            "### Objetivos de aprendizaje",
            "",
        ]
    )
    for item in objetivos:
        lines.append(f"- {item}")

    lines.extend(["", "### Vocabulario clave", ""])
    lines.extend(vocab_table)

    lines.extend(["", "### 1. Primer concepto", ""])
    if seg_summary:
        first_block = seg_summary[: max(1, min(4, len(seg_summary)))]
        lines.extend([f"- {s['text']}" for s in first_block])
    else:
        lines.append("- Desarrollar el concepto principal del tema.")

    lines.extend(["", "#### 1.1. Subconcepto o ejemplo práctico", ""])
    if seg_summary:
        sample = seg_summary[min(1, len(seg_summary) - 1)]
        lines.append(f"Ejemplo práctico: {sample['text']}")
    else:
        lines.append("Ejemplo práctico contextualizado.")

    lines.extend(["", "### 2. Segundo concepto", ""])
    if len(seg_summary) > 4:
        second_block = seg_summary[4:8]
        lines.extend([f"- {s['text']}" for s in second_block])
    else:
        lines.append("- Continuar con estructura similar.")

    lines.extend(["", "### Resumen final", ""])
    for item in ideas_finales[:5]:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "### Referencias",
            "",
            f"- Fuente base: `{config.video_path.name}`",
            "- Método: Transcripción automática con Whisper y postprocesado determinista.",
            "",
            "### Plantilla aplicada",
            "",
            "```text",
            template,
            "```",
            "",
            f"**Fecha de actualización:** {created_human}",
            "",
        ]
    )
    return "\n".join(lines)


def split_paragraphs(text: str, max_sentences: int = 3) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return []

    paragraphs: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        current.append(sentence)
        if len(current) >= max_sentences:
            paragraphs.append(" ".join(current))
            current = []

    if current:
        paragraphs.append(" ".join(current))

    return paragraphs


def build_report_context(result: dict[str, Any], config: TranscriptionConfig) -> dict[str, Any]:
    language_label = config.language if config.language else "auto"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = config.video_path.stem.replace("_", " ").strip() or config.video_path.name
    raw_text = normalize_text(str(result.get("text", "")))
    paragraphs = split_paragraphs(raw_text)
    segments = result.get("segments") or []

    return {
        "language_label": language_label,
        "created_at": created_at,
        "title": title,
        "raw_text": raw_text,
        "paragraphs": paragraphs,
        "segments": segments,
    }


def render_txt(result: dict[str, Any], config: TranscriptionConfig) -> str:
    ctx = build_report_context(result, config)

    lines = [
        "TRANSCRIPCION DE VIDEO",
        "======================",
        f"Titulo: {ctx['title']}",
        f"Archivo: {config.video_path.name}",
        f"Modelo: Whisper {config.model}",
        f"Idioma: {ctx['language_label']}",
        f"Generado: {ctx['created_at']}",
        "",
        "CUERPO",
        "------",
    ]

    if ctx["paragraphs"]:
        for paragraph in ctx["paragraphs"]:
            lines.append(paragraph)
            lines.append("")
    else:
        lines.append("(Sin contenido transcrito)")
        lines.append("")

    if not config.include_timestamps:
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(["SEGMENTOS CON MARCAS DE TIEMPO", "-------------------------------"])
    for segment in ctx["segments"]:
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", 0.0))
        text = normalize_text(str(segment.get("text", "")))
        start_h = format_time(start, srt=False)
        end_h = format_time(end, srt=False)
        lines.append(f"[{start_h} - {end_h}] {text}")
    return "\n".join(lines).strip() + "\n"


def render_md(result: dict[str, Any], config: TranscriptionConfig) -> str:
    ctx = build_report_context(result, config)
    lines = [
        "# Transcripcion de video",
        "",
        "## Metadatos",
        f"- **Titulo:** {ctx['title']}",
        f"- **Archivo:** `{config.video_path.name}`",
        f"- **Modelo:** `Whisper {config.model}`",
        f"- **Idioma:** `{ctx['language_label']}`",
        f"- **Generado:** `{ctx['created_at']}`",
        "",
        "## Texto transcrito",
        "",
    ]

    if ctx["paragraphs"]:
        lines.extend(ctx["paragraphs"])
        lines.append("")
    else:
        lines.append("_Sin contenido transcrito._")
        lines.append("")

    if config.include_timestamps:
        lines.extend(
            [
                "## Segmentos con marcas de tiempo",
                "",
                "| Inicio | Fin | Texto |",
                "|---|---|---|",
            ]
        )
        for segment in ctx["segments"]:
            start = format_time(float(segment.get("start", 0.0)), srt=False)
            end = format_time(float(segment.get("end", 0.0)), srt=False)
            text = normalize_text(str(segment.get("text", ""))).replace("|", "\\|")
            lines.append(f"| {start} | {end} | {text} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_srt(result: dict[str, Any]) -> str:
    segments = result.get("segments") or []
    lines: list[str] = []
    for idx, segment in enumerate(segments, start=1):
        start = format_time(float(segment.get("start", 0.0)), srt=True)
        end = format_time(float(segment.get("end", 0.0)), srt=True)
        text = str(segment.get("text", "")).strip()
        lines.append(str(idx))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_vtt(result: dict[str, Any]) -> str:
    segments = result.get("segments") or []
    lines: list[str] = ["WEBVTT", ""]
    for segment in segments:
        start = format_time(float(segment.get("start", 0.0)), srt=False)
        end = format_time(float(segment.get("end", 0.0)), srt=False)
        text = str(segment.get("text", "")).strip()
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"


def render_output(
    result: dict[str, Any],
    config: TranscriptionConfig,
    output_format: str,
) -> str:
    if output_format == "txt":
        return render_txt(result, config)
    if output_format == "md":
        return render_md(result, config)
    if output_format == "srt":
        return render_srt(result)
    if output_format == "vtt":
        return render_vtt(result)
    if output_format == "json":
        return render_json(result)
    raise ValueError(f"Formato no soportado: {output_format}")


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=str(path.parent),
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def build_config(args: argparse.Namespace) -> TranscriptionConfig:
    video_path = args.video.resolve()
    if not video_path.exists() or not video_path.is_file():
        raise FileNotFoundError(f"No existe el video: {video_path}")

    language = None if str(args.language).strip().lower() == "auto" else str(args.language).strip()
    output_format = str(args.format).strip().lower()
    extra_formats = tuple(str(fmt).strip().lower() for fmt in args.extra_formats)
    output_path = build_output_path(video_path, args.output, output_format)
    analysis_output = build_analysis_output_path(args, output_path) if args.analyze else None
    notes_output = build_notes_output_path(args, output_path) if args.generate_notes else None
    script_dir = Path(__file__).resolve().parent
    template_file = (
        args.template_file.resolve()
        if args.template_file
        else (script_dir / "formato.md")
    )
    all_paths = [output_path, *[output_path.with_suffix(f".{fmt}") for fmt in extra_formats if fmt != output_format]]
    if analysis_output is not None:
        all_paths.append(analysis_output)
    if notes_output is not None:
        all_paths.append(notes_output)
    if not args.overwrite:
        for path in all_paths:
            if path.exists():
                raise FileExistsError(
                    f"El fichero de salida ya existe: {path}. "
                    "Usa --overwrite para sobrescribir."
                )

    return TranscriptionConfig(
        video_path=video_path,
        output_path=output_path,
        model=args.model,
        language=language,
        output_format=output_format,
        extra_formats=extra_formats,
        include_timestamps=bool(args.timestamps),
        analyze=bool(args.analyze),
        analysis_output=analysis_output,
        generate_notes=bool(args.generate_notes),
        notes_output=notes_output,
        notes_title=args.notes_title,
        notes_module=str(args.notes_module).strip() or "AUTO",
        notes_author=str(args.notes_author).strip() or "Generador automatico",
        template_file=template_file,
        overwrite=bool(args.overwrite),
    )


def transcribir_video(config: TranscriptionConfig, progress: ProgressTracker) -> None:
    import whisper

    progress.update(35)
    model = whisper.load_model(config.model)
    progress.update(55)
    result = model.transcribe(
        str(config.video_path),
        language=config.language,
        task="transcribe",
        verbose=False,
    )
    progress.update(80)
    output_paths = build_output_paths(config)
    total_outputs = len(output_paths) + (1 if config.analyze and config.analysis_output is not None else 0) + (1 if config.generate_notes and config.notes_output is not None else 0)
    completed_outputs = 0
    for fmt, path in output_paths.items():
        output_text = render_output(result, config, fmt)
        write_text_atomic(path, output_text)
        completed_outputs += 1
        progress.update(80 + int((completed_outputs / max(total_outputs, 1)) * 18))

    if config.analyze and config.analysis_output is not None:
        analysis_md = build_analysis_markdown(result, config)
        write_text_atomic(config.analysis_output, analysis_md)
        completed_outputs += 1
        progress.update(80 + int((completed_outputs / max(total_outputs, 1)) * 18))

    if config.generate_notes and config.notes_output is not None:
        notes_md = build_notes_markdown(result, config)
        write_text_atomic(config.notes_output, notes_md)
        completed_outputs += 1
        progress.update(80 + int((completed_outputs / max(total_outputs, 1)) * 18))

    progress.update(99)


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    progress = ProgressTracker()
    progress.update(5)

    try:
        progress.update(10)
        configure_ssl(args.ssl_cert_file, args.insecure_ssl)
        progress.update(20)
        ensure_dependencies()
        progress.update(30)
        config = build_config(args)
        transcribir_video(config, progress)
    except KeyboardInterrupt:
        LOGGER.error("Proceso interrumpido por el usuario.")
        return 130
    except FileExistsError as exc:
        LOGGER.error(str(exc))
        return 2
    except FileNotFoundError as exc:
        LOGGER.error(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            LOGGER.error(
                "Fallo SSL al descargar modelo. Prueba --ssl-cert-file <ruta.pem> "
                "o, como ultimo recurso, --insecure-ssl."
            )
        LOGGER.error("Error en la transcripcion: %s", exc)
        return 1

    progress.update(100)
    generated = build_output_paths(config)
    print("Transcripcion generada:")
    for fmt, path in generated.items():
        print(f"- {fmt}: {path.resolve()}")
    if config.analyze and config.analysis_output is not None:
        print(f"- analysis: {config.analysis_output.resolve()}")
    if config.generate_notes and config.notes_output is not None:
        print(f"- notes: {config.notes_output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
