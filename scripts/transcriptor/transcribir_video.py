#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import logging
import os
import re
import shutil
import ssl
import sys
import tempfile
import time
import warnings
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

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
    topic_threshold: float
    overwrite: bool


class ProgressTracker:
    def __init__(self) -> None:
        self.current = -1
        self.start_ts = time.monotonic()
        self.bar_width = 28

    def _format_eta(self, value: int) -> str:
        if value <= 0:
            return "--:--"
        elapsed = time.monotonic() - self.start_ts
        total_estimated = elapsed / (value / 100.0)
        eta = max(total_estimated - elapsed, 0.0)
        eta_min = int(eta // 60)
        eta_sec = int(eta % 60)
        return f"{eta_min:02d}:{eta_sec:02d}"

    def update(self, value: int, *, stage: str = "", detail: str = "") -> None:
        value = max(0, min(100, int(value)))
        if value == self.current and not stage and not detail:
            return
        self.current = value
        elapsed = time.monotonic() - self.start_ts
        elapsed_min = int(elapsed // 60)
        elapsed_sec = int(elapsed % 60)
        eta = self._format_eta(value)
        suffix_parts = []
        if stage:
            suffix_parts.append(stage)
        if detail:
            suffix_parts.append(detail)
        suffix = " | ".join(suffix_parts)
        if suffix:
            suffix = " | " + suffix
        filled = int((value / 100) * self.bar_width)
        empty = self.bar_width - filled
        bar = "[" + ("█" * filled) + ("░" * empty) + "]"
        msg = (
            f"\rProgreso: {value:3d}% {bar} | Transcurrido: {elapsed_min:02d}:{elapsed_sec:02d} "
            f"| ETA: {eta}{suffix}"
        )
        if sys.stdout.isatty():
            term_width = shutil.get_terminal_size((120, 20)).columns
            clean = msg.replace("\r", "")
            if len(clean) > term_width - 1:
                clean = clean[: max(1, term_width - 2)] + "…"
            # Limpia la linea antes de reescribir para evitar restos de texto previo.
            print(f"\r\033[2K{clean}", end="", flush=True)
        else:
            print(msg, end="", flush=True)
        if value >= 100:
            print()

    def whisper_realtime_update(self, ratio: float) -> None:
        """
        Actualiza progreso en tiempo real durante la transcripcion.
        Mapea [0..1] de Whisper al rango [55..80] del flujo global.
        """
        ratio = max(0.0, min(1.0, float(ratio)))
        mapped = 55 + int(ratio * 25)
        self.update(
            mapped,
            stage="Transcripcion",
            detail=f"Procesando audio ({int(ratio * 100):02d}%)",
        )


class _RealtimeTqdm:
    """
    Sustituto silencioso de tqdm para capturar avance real de Whisper sin imprimir barras externas.
    """

    def __init__(
        self,
        iterable: Iterable[Any] | None = None,
        *,
        total: int | None = None,
        progress: ProgressTracker | None = None,
        **_: Any,
    ) -> None:
        self._iterable = iterable
        self._total = total if total is not None else (len(iterable) if iterable is not None and hasattr(iterable, "__len__") else None)
        self._n = 0
        self._progress = progress
        self._last_percent = -1

    def _emit(self) -> None:
        if self._progress is None or not self._total or self._total <= 0:
            return
        ratio = self._n / self._total
        percent = int(ratio * 100)
        if percent != self._last_percent:
            self._last_percent = percent
            self._progress.whisper_realtime_update(ratio)

    def __iter__(self) -> Iterator[Any]:
        if self._iterable is None:
            return iter(())
        for item in self._iterable:
            self._n += 1
            self._emit()
            yield item

    def update(self, n: int = 1) -> None:
        self._n += int(n)
        self._emit()

    def close(self) -> None:
        self._emit()

    def __enter__(self) -> "_RealtimeTqdm":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe un video a texto usando Whisper."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Ruta de archivo de configuracion (.yaml/.yml/.json).",
    )
    parser.add_argument(
        "video",
        nargs="?",
        type=Path,
        default=None,
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
        "--topic-threshold",
        type=float,
        default=0.22,
        help="Umbral (0-1) para detectar cambios de tema entre segmentos.",
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
        "--bundle",
        action="store_true",
        help="Genera en una pasada: txt + md + analisis + apuntes.",
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


def load_config_file(config_path: Path) -> dict[str, Any]:
    if not config_path.exists() or not config_path.is_file():
        raise FileNotFoundError(f"No existe el archivo de configuracion: {config_path}")

    suffix = config_path.suffix.lower()
    text = config_path.read_text(encoding="utf-8")

    if suffix == ".json":
        data = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Para usar configuracion YAML instala PyYAML: pip install pyyaml"
            ) from exc
        data = yaml.safe_load(text)
    else:
        raise RuntimeError("Formato de config no soportado. Usa .json, .yaml o .yml")

    if not isinstance(data, dict):
        raise RuntimeError("El archivo de configuracion debe contener un objeto clave-valor.")
    return data


def _coerce_value(attr: str, value: Any) -> Any:
    path_fields = {
        "video",
        "output",
        "analysis_output",
        "notes_output",
        "template_file",
        "ssl_cert_file",
    }
    if attr in path_fields and value is not None:
        return Path(str(value))
    if attr == "extra_formats":
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(str(v) for v in value)
    return value


def apply_config_overrides(args: argparse.Namespace) -> argparse.Namespace:
    if args.config is None:
        return args

    cfg = load_config_file(args.config.resolve())
    defaults: dict[str, Any] = {
        "video": None,
        "output": None,
        "model": "base",
        "language": "es",
        "format": "txt",
        "extra_formats": (),
        "timestamps": False,
        "topic_threshold": 0.22,
        "analyze": False,
        "analysis_output": None,
        "generate_notes": False,
        "notes_output": None,
        "notes_title": None,
        "notes_module": "AUTO",
        "notes_author": "Generador automatico",
        "template_file": None,
        "overwrite": False,
        "bundle": False,
        "log_level": "ERROR",
        "ssl_cert_file": None,
        "insecure_ssl": False,
    }

    allowed = set(defaults.keys())
    for key, raw_value in cfg.items():
        if key not in allowed:
            continue
        current = getattr(args, key)
        if current == defaults[key]:
            setattr(args, key, _coerce_value(key, raw_value))

    return args


def apply_bundle_mode(args: argparse.Namespace) -> argparse.Namespace:
    if not args.bundle:
        return args

    args.analyze = True
    args.generate_notes = True
    args.timestamps = True

    formats = set(args.extra_formats)
    formats.add(args.format)
    if "txt" not in formats:
        formats.add("txt")
    if "md" not in formats:
        formats.add("md")

    # Preserva el formato principal solicitado y añade el resto como extras.
    args.extra_formats = tuple(sorted(fmt for fmt in formats if fmt != args.format))
    return args


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


def configure_runtime_noise_suppression() -> None:
    # Evita warning recurrente de Whisper en CPU (FP16 -> FP32).
    warnings.filterwarnings(
        "ignore",
        message="FP16 is not supported on CPU; using FP32 instead",
        category=UserWarning,
    )


@contextlib.contextmanager
def suppress_external_output() -> Any:
    """
    Silencia salida de librerias externas (warnings/barras tqdm de Whisper),
    manteniendo la salida propia del script.
    """
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sink_out = io.StringIO()
    sink_err = io.StringIO()
    try:
        sys.stdout = sink_out
        sys.stderr = sink_err
        yield
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr


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
    topic_changes = detect_topic_changes(segments, threshold=config.topic_threshold)

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
    if args.video is None:
        raise FileNotFoundError("Debes indicar un video por CLI o en --config.")
    video_path = args.video.resolve()
    if not video_path.exists() or not video_path.is_file():
        raise FileNotFoundError(f"No existe el video: {video_path}")

    language = None if str(args.language).strip().lower() == "auto" else str(args.language).strip()
    output_format = str(args.format).strip().lower()
    if output_format not in ALLOWED_FORMATS:
        raise RuntimeError(f"Formato principal no soportado: {output_format}")
    extra_formats = tuple(str(fmt).strip().lower() for fmt in args.extra_formats)
    invalid_formats = [fmt for fmt in extra_formats if fmt not in ALLOWED_FORMATS]
    if invalid_formats:
        raise RuntimeError(f"Formatos extra no soportados: {', '.join(invalid_formats)}")
    if not (0.0 <= float(args.topic_threshold) <= 1.0):
        raise RuntimeError("--topic-threshold debe estar entre 0 y 1.")
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
        topic_threshold=float(args.topic_threshold),
        overwrite=bool(args.overwrite),
    )


def transcribir_video(config: TranscriptionConfig, progress: ProgressTracker) -> None:
    import whisper
    whisper_transcribe_module = importlib.import_module("whisper.transcribe")

    progress.update(35, stage="Carga de modelo", detail=f"Whisper {config.model}")
    with suppress_external_output():
        model = whisper.load_model(config.model)
    progress.update(55, stage="Transcripcion", detail="Procesando audio del video")
    original_tqdm_fn = whisper_transcribe_module.tqdm.tqdm
    whisper_transcribe_module.tqdm.tqdm = (
        lambda iterable=None, *args, **kwargs: _RealtimeTqdm(
            iterable=iterable,
            total=kwargs.get("total"),
            progress=progress,
        )
    )
    try:
        # No se envuelve en suppress_external_output para permitir progreso real del callback.
        result = model.transcribe(
            str(config.video_path),
            language=config.language,
            task="transcribe",
            verbose=False,
        )
    finally:
        whisper_transcribe_module.tqdm.tqdm = original_tqdm_fn
    progress.update(80, stage="Postproceso", detail="Generando salidas")
    output_paths = build_output_paths(config)
    total_outputs = len(output_paths) + (1 if config.analyze and config.analysis_output is not None else 0) + (1 if config.generate_notes and config.notes_output is not None else 0)
    completed_outputs = 0
    for fmt, path in output_paths.items():
        progress.update(
            80 + int((completed_outputs / max(total_outputs, 1)) * 18),
            stage="Escritura",
            detail=f"[{completed_outputs + 1}/{total_outputs}] {fmt} -> {path.name}",
        )
        output_text = render_output(result, config, fmt)
        write_text_atomic(path, output_text)
        completed_outputs += 1

    if config.analyze and config.analysis_output is not None:
        progress.update(
            80 + int((completed_outputs / max(total_outputs, 1)) * 18),
            stage="Escritura",
            detail=f"[{completed_outputs + 1}/{total_outputs}] analysis -> {config.analysis_output.name}",
        )
        analysis_md = build_analysis_markdown(result, config)
        write_text_atomic(config.analysis_output, analysis_md)
        completed_outputs += 1

    if config.generate_notes and config.notes_output is not None:
        progress.update(
            80 + int((completed_outputs / max(total_outputs, 1)) * 18),
            stage="Escritura",
            detail=f"[{completed_outputs + 1}/{total_outputs}] notes -> {config.notes_output.name}",
        )
        notes_md = build_notes_markdown(result, config)
        write_text_atomic(config.notes_output, notes_md)
        completed_outputs += 1

    progress.update(99, stage="Finalizacion", detail="Validando resultados")


def main() -> int:
    args = parse_args()
    args = apply_config_overrides(args)
    args = apply_bundle_mode(args)
    configure_logging(args.log_level)
    configure_runtime_noise_suppression()
    progress = ProgressTracker()
    progress.update(5, stage="Inicio", detail="Preparando ejecucion")

    try:
        progress.update(10, stage="Configuracion", detail="Aplicando SSL")
        configure_ssl(args.ssl_cert_file, args.insecure_ssl)
        progress.update(20, stage="Validacion", detail="Comprobando dependencias")
        ensure_dependencies()
        progress.update(30, stage="Validacion", detail="Construyendo configuracion")
        config = build_config(args)
        outputs_preview = [config.output_format, *config.extra_formats]
        if config.analyze:
            outputs_preview.append("analysis")
        if config.generate_notes:
            outputs_preview.append("notes")
        video_size_mb = config.video_path.stat().st_size / (1024 * 1024)
        progress.update(
            32,
            stage="Configuracion lista",
            detail=(
                f"Video={config.video_path.name} ({video_size_mb:.1f} MB), "
                f"Idioma={config.language or 'auto'}, Salidas={','.join(outputs_preview)}"
            ),
        )
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

    progress.update(100, stage="Completado", detail="Proceso finalizado correctamente")
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
