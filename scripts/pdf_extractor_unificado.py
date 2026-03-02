#!/usr/bin/env python3
"""Extractor profesional de PDF: figuras y tablas como imágenes.

Uso:
    python3 pdf_extractor_unificado.py [archivo.pdf]

Salida:
    <directorio_pdf>/<nombre_pdf>_extraido/
      - media/   figuras y tablas
      - manifest.json  metadatos de extracción
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import re
import unicodedata
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import fitz  # PyMuPDF
from PIL import Image


FIGURE_CAPTION_TERMS = [
    "figura",
    "imagen",
    "ilustracion",
    "grafico",
    "esquema",
    "diagrama",
    "lamina",
    "fotografia",
    "mapa",
    "plano",
    "representacion",
    "infografia",
    "grafico comparativo",
    "grafico de barras",
    "grafico circular",
    "grafico lineal",
]
TABLE_CAPTION_TERMS = [
    "tabla",
    "cuadro",
]

FIGURE_CAPTION_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in FIGURE_CAPTION_TERMS) + r")\b",
    re.IGNORECASE,
)
TABLE_CAPTION_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in TABLE_CAPTION_TERMS) + r")\b",
    re.IGNORECASE,
)
STRICT_FIGURE_PROFILE = True
DEFAULT_MAX_TEXT_RATIO = 0.18
TABLE_MIN_WIDTH_RATIO = 0.20
TABLE_MIN_HEIGHT_RATIO = 0.05
TABLE_MIN_HEIGHT_RATIO_NO_CAPTION = 0.075
OCR_CACHE: Dict[Tuple[str, str], Tuple[int, float]] = {}
OCR_LINE_CACHE: Dict[Tuple[str, str], Optional[Tuple[float, int, int]]] = {}


@dataclass
class ExtractionRecord:
    kind: str
    page: int
    path: str
    method: str
    bbox: Tuple[float, float, float, float]
    text_ratio: Optional[float] = None


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def to_rect(self) -> fitz.Rect:
        return fitz.Rect(self.x0, self.y0, self.x1, self.y1)

    def intersects(self, other: "Box") -> bool:
        return not (self.x1 <= other.x0 or other.x1 <= self.x0 or self.y1 <= other.y0 or other.y1 <= self.y0)

    def intersection_area(self, other: "Box") -> float:
        if not self.intersects(other):
            return 0.0
        x0 = max(self.x0, other.x0)
        y0 = max(self.y0, other.y0)
        x1 = min(self.x1, other.x1)
        y1 = min(self.y1, other.y1)
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)

    def union(self, other: "Box") -> "Box":
        return Box(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))

    def expand(self, dx: float, dy: float, bounds: fitz.Rect) -> "Box":
        return Box(
            max(bounds.x0, self.x0 - dx),
            max(bounds.y0, self.y0 - dy),
            min(bounds.x1, self.x1 + dx),
            min(bounds.y1, self.y1 + dy),
        )


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("PDFExtractorAuto")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extracción automática de figuras y tablas como imagen")
    parser.add_argument(
        "pdf",
        nargs="?",
        default=None,
        help="Ruta del PDF (opcional). Si se omite, se usa el PDF más reciente del directorio actual.",
    )
    parser.add_argument(
        "--strict-content-only",
        dest="strict_content_only",
        action="store_true",
        default=True,
        help="Descarta recortes mixtos (diagrama + texto corrido) cuando no se puedan aislar limpiamente.",
    )
    parser.add_argument(
        "--allow-mixed-content",
        dest="strict_content_only",
        action="store_false",
        help="Permite recortes mixtos para maximizar cobertura.",
    )
    parser.add_argument(
        "--max-text-ratio",
        type=float,
        default=DEFAULT_MAX_TEXT_RATIO,
        help="Máximo ratio de área OCR textual permitido en figuras antes de considerar contenido mixto.",
    )
    return parser.parse_args()


def choose_default_pdf(base_dir: Path, logger: logging.Logger) -> Optional[Path]:
    """Devuelve el PDF más reciente del directorio indicado."""
    pdfs = [p for p in base_dir.glob("*.pdf") if p.is_file()]
    if not pdfs:
        return None
    picked = max(pdfs, key=lambda p: p.stat().st_mtime)
    logger.info("PDF seleccionado automáticamente: %s", picked)
    return picked


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_bytes(path: Path, data: bytes) -> None:
    ensure_dir(path.parent)
    path.write_bytes(data)


def img_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def normalize_spanish(text: str) -> str:
    txt = unicodedata.normalize("NFKD", text or "")
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", txt).strip().lower()


def is_caption_line(
    text: str,
    terms: List[str],
    keyword_re: re.Pattern,
    require_start: bool = False,
) -> bool:
    txt = normalize_spanish(text)
    if not txt:
        return False
    starts = any(txt.startswith(term) for term in terms)
    if starts:
        return True
    if require_start:
        return False
    # Casos tipo "ver figura 2.3" o "gráfico lineal 1".
    if re.search(keyword_re.pattern + r"\s*(n[ºo]\.?\s*)?\d", txt, re.IGNORECASE):
        return True
    return False


def rect_to_box(rect: fitz.Rect) -> Box:
    return Box(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def merge_boxes(boxes: Sequence[Box], x_tol: float = 8.0, y_tol: float = 8.0) -> List[Box]:
    if not boxes:
        return []
    pool = list(boxes)
    changed = True
    while changed:
        changed = False
        out: List[Box] = []
        while pool:
            cur = pool.pop(0)
            i = 0
            while i < len(pool):
                other = pool[i]
                near = (
                    cur.intersects(other)
                    or abs(cur.x1 - other.x0) <= x_tol
                    or abs(other.x1 - cur.x0) <= x_tol
                    or abs(cur.y1 - other.y0) <= y_tol
                    or abs(other.y1 - cur.y0) <= y_tol
                )
                if near:
                    cur = cur.union(other)
                    pool.pop(i)
                    changed = True
                else:
                    i += 1
            out.append(cur)
        pool = out
    return pool


def overlap_ratio(box: Box, others: Sequence[Box]) -> float:
    if box.area <= 0:
        return 0.0
    best = 0.0
    for other in others:
        best = max(best, box.intersection_area(other) / box.area)
    return best


def ocr_metrics(image_bytes: bytes, lang: str = "spa") -> Tuple[int, float]:
    key = (img_hash(image_bytes), lang)
    if key in OCR_CACHE:
        return OCR_CACHE[key]

    try:
        import pytesseract
    except Exception:
        return 0, 0.0
    try:
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang=lang)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        words = [w for w in text.split() if any(ch.isalpha() for ch in w)]
        if not lines:
            return len(words), 0.0
        avg = sum(len([w for w in ln.split() if any(ch.isalpha() for ch in w)]) for ln in lines) / len(lines)
        result = (len(words), avg)
        OCR_CACHE[key] = result
        return result
    except Exception:
        return 0, 0.0


def ocr_engine_available() -> bool:
    try:
        import pytesseract
    except Exception:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_running_text_stats(image_bytes: bytes, lang: str = "spa") -> Optional[Tuple[float, int, int]]:
    """Devuelve (text_area_ratio, long_lines, lower_long_lines) para detectar párrafo corrido."""
    key = (img_hash(image_bytes), lang)
    if key in OCR_LINE_CACHE:
        return OCR_LINE_CACHE[key]

    try:
        import pytesseract
        from pytesseract import Output
    except Exception:
        OCR_LINE_CACHE[key] = None
        return None

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        if w <= 0 or h <= 0:
            OCR_LINE_CACHE[key] = None
            return None

        data = pytesseract.image_to_data(img, lang=lang, output_type=Output.DICT, config="--psm 6")
        n = len(data.get("text", []))
        lines: Dict[Tuple[int, int, int], Dict[str, float]] = {}
        text_area = 0.0

        for i in range(n):
            txt = (data["text"][i] or "").strip()
            if not txt or not any(ch.isalpha() for ch in txt):
                continue
            left = max(0, int(data["left"][i]))
            top = max(0, int(data["top"][i]))
            rw = max(0, int(data["width"][i]))
            rh = max(0, int(data["height"][i]))
            if rw <= 0 or rh <= 0:
                continue
            text_area += float(rw * rh)
            k = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
            if k not in lines:
                lines[k] = {"x0": left, "y0": top, "x1": left + rw, "y1": top + rh, "words": 1.0}
            else:
                ln = lines[k]
                ln["x0"] = min(ln["x0"], left)
                ln["y0"] = min(ln["y0"], top)
                ln["x1"] = max(ln["x1"], left + rw)
                ln["y1"] = max(ln["y1"], top + rh)
                ln["words"] += 1.0

        long_lines = 0
        lower_long_lines = 0
        for ln in lines.values():
            line_w = max(1.0, ln["x1"] - ln["x0"])
            line_mid_y = (ln["y0"] + ln["y1"]) / 2.0
            width_ratio = line_w / float(w)
            # Línea larga tipo párrafo: muchas palabras y bastante ancho.
            if ln["words"] >= 8.0 and width_ratio >= 0.42:
                long_lines += 1
                if line_mid_y >= h * 0.35:
                    lower_long_lines += 1

        stats = (min(1.0, text_area / float(w * h)), long_lines, lower_long_lines)
        OCR_LINE_CACHE[key] = stats
        return stats
    except Exception:
        OCR_LINE_CACHE[key] = None
        return None


def detect_mixed_running_text(
    image_bytes: bytes,
    lang: str = "spa",
    max_text_ratio: float = DEFAULT_MAX_TEXT_RATIO,
) -> Tuple[bool, Optional[float], List[str]]:
    stats = ocr_running_text_stats(image_bytes, lang=lang)
    if stats is None:
        return False, None, []
    text_ratio, long_lines, lower_long_lines = stats
    reasons: List[str] = []
    if text_ratio > max_text_ratio:
        reasons.append("running_text_ratio")
    if lower_long_lines >= 2 or long_lines >= 3:
        reasons.append("long_text_lines")
    elif lower_long_lines >= 1 and text_ratio > (max_text_ratio * 0.75):
        reasons.append("long_text_lines")
    return bool(reasons), text_ratio, reasons


def image_looks_tabular(image_bytes: bytes) -> bool:
    try:
        import cv2
        import numpy as np
    except Exception:
        return False

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return False

    bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
    h, w = bw.shape
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 20), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 20)))
    h_lines = cv2.dilate(cv2.erode(bw, h_kernel), h_kernel)
    v_lines = cv2.dilate(cv2.erode(bw, v_kernel), v_kernel)

    def long_count(mask, min_len: int) -> int:
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = 0
        for cc in cnts:
            x, y, rw, rh = cv2.boundingRect(cc)
            if rw >= min_len or rh >= min_len:
                c += 1
        return c

    hc = long_count(h_lines, int(w * 0.45))
    vc = long_count(v_lines, int(h * 0.18))
    line_ink_ratio = float((h_lines > 0).mean()) + float((v_lines > 0).mean())
    return hc >= 2 and vc >= 2 and line_ink_ratio >= 0.018


def looks_like_running_text(image_bytes: bytes, lang: str = "spa") -> bool:
    words, avg = ocr_metrics(image_bytes, lang=lang)
    return words >= 120 and avg >= 8.0


def likely_figure_content(image_bytes: bytes) -> bool:
    """Heurística visual para favorecer diagramas/fotos frente a páginas de texto."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return True

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return True

    h, w = gray.shape[:2]
    if h < 20 or w < 20:
        return False

    edges = cv2.Canny(gray, 80, 160)
    edge_ratio = float((edges > 0).mean())

    # Ligeramente más estricto cuando el perfil estricto está activo.
    min_edges = 0.010 if STRICT_FIGURE_PROFILE else 0.007
    return edge_ratio >= min_edges


def looks_like_dense_text_block(image_bytes: bytes) -> bool:
    """Heurística CV para detectar recortes dominados por texto corrido sin OCR."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return False

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return False
    h, w = gray.shape[:2]
    if h < 40 or w < 40:
        return False

    try:
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 15)
        ink_ratio = float((bw > 0).mean())
        row_density = (bw > 0).mean(axis=1)
        dense_rows_ratio = float((row_density > 0.10).mean())
        n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    except Exception:
        return False

    total = 0
    glyph = 0
    structural = 0
    min_area = 8
    for i in range(1, n):
        x, y, rw, rh, area = [int(v) for v in stats[i]]
        if area < min_area:
            continue
        total += 1
        if 3 <= rw <= 45 and 5 <= rh <= 35 and area <= 350:
            glyph += 1
        if rw > int(w * 0.22) or rh > int(h * 0.08) or area > int(w * h * 0.002):
            structural += 1

    if total < 60:
        return False
    glyph_ratio = glyph / float(max(1, total))
    # Texto denso típico: mucho "grano" de glifos y muy poca estructura gráfica.
    return ink_ratio >= 0.08 and dense_rows_ratio >= 0.24 and glyph_ratio >= 0.84 and structural <= 1


def has_large_graphic_blocks(image_bytes: bytes) -> bool:
    """Detecta estructuras gráficas grandes (cajas/diagramas) en una imagen escaneada."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return False

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return False
    h, w = gray.shape[:2]
    if h < 80 or w < 80:
        return False

    try:
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 15)
        n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    except Exception:
        return False

    large = 0
    min_area = max(50, int(w * h * 0.00025))
    for i in range(1, n):
        x, y, rw, rh, area = [int(v) for v in stats[i]]
        if area < min_area:
            continue
        # Bloques gráficos no típicos de glifos de texto.
        if rw >= int(w * 0.12) and rh >= int(h * 0.02):
            large += 1
            if large >= 2:
                return True
    return False


def isolate_top_diagram_block(image_bytes: bytes) -> Tuple[bytes, Optional[Tuple[int, int, int, int]], Optional[Tuple[int, int]]]:
    """Recorta diagramas en banda superior (p. ej., paneles conectados) en escaneos de página."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return image_bytes, None, None

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return image_bytes, None, None

    h, w = gray.shape[:2]
    if h < 120 or w < 120:
        return image_bytes, None, None

    try:
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 15)
        closed = cv2.morphologyEx(
            bw,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
            iterations=1,
        )
        cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    except Exception:
        return image_bytes, None, None

    img_area = float(w * h)
    candidates: List[Tuple[int, int, int, int, float]] = []
    for c in cnts:
        peri = cv2.arcLength(c, True)
        if peri <= 0:
            continue
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if len(approx) < 4 or len(approx) > 8:
            continue
        x, y, rw, rh = cv2.boundingRect(c)
        area = float(rw * rh)
        area_ratio = area / img_area
        if area_ratio < 0.010 or area_ratio > 0.28:
            continue
        ar = rw / float(max(1, rh))
        if ar < 0.55 or ar > 4.2:
            continue
        if y > int(h * 0.78):
            continue
        candidates.append((x, y, x + rw, y + rh, area))

    if len(candidates) < 2:
        return image_bytes, None, None

    # Elimina cajas casi totalmente contenidas en otras mayores.
    candidates = sorted(candidates, key=lambda b: b[4], reverse=True)
    filtered: List[Tuple[int, int, int, int, float]] = []
    for b in candidates:
        x0, y0, x1, y1, area = b
        inside = False
        for k in filtered:
            kx0, ky0, kx1, ky1, karea = k
            inter = max(0, min(x1, kx1) - max(x0, kx0)) * max(0, min(y1, ky1) - max(y0, ky0))
            if area > 0 and (inter / area) >= 0.92 and karea >= area * 1.15:
                inside = True
                break
        if not inside:
            filtered.append(b)

    if len(filtered) < 2:
        return image_bytes, None, None

    # Busca una banda horizontal de paneles principales.
    top = sorted(filtered, key=lambda b: b[4], reverse=True)[:8]
    centers = np.array([(b[1] + b[3]) / 2.0 for b in top], dtype=np.float32)
    median_y = float(np.median(centers))
    band = [b for b in filtered if abs(((b[1] + b[3]) / 2.0) - median_y) <= h * 0.14]
    if len(band) < 2:
        return image_bytes, None, None

    x0 = min(b[0] for b in band)
    y0 = min(b[1] for b in band)
    x1 = max(b[2] for b in band)
    y1 = max(b[3] for b in band)
    width_ratio = (x1 - x0) / float(w)
    height_ratio = (y1 - y0) / float(h)
    if width_ratio < 0.45 or height_ratio > 0.42:
        return image_bytes, None, None

    pad_x = max(8, int(w * 0.025))
    pad_y = max(8, int(h * 0.02))
    x0 = max(0, x0 - pad_x)
    y0 = max(0, y0 - pad_y)
    x1 = min(w, x1 + pad_x)
    y1 = min(h, y1 + pad_y)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return image_bytes, None, None

    crop_bw = bw[y0:y1, x0:x1]
    if crop_bw.size > 0:
        row_density = (crop_bw > 0).mean(axis=1)
        dense_rows = float((row_density > 0.14).mean())
        if dense_rows > 0.55:
            return image_bytes, None, None

    crop = gray[y0:y1, x0:x1]
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        return image_bytes, None, None
    return encoded.tobytes(), (x0, y0, x1, y1), (w, h)


def figure_visual_score(image_bytes: bytes) -> float:
    """Puntuación visual simple para elegir mejor recorte de figura."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return 0.0

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return 0.0
    edges = cv2.Canny(gray, 80, 160)
    return float((edges > 0).mean())


def isolate_visual_core(image_bytes: bytes) -> Tuple[bytes, Optional[Tuple[int, int, int, int]], Optional[Tuple[int, int]]]:
    """Recorta al núcleo visual (diagrama/gráfico) evitando bloques grandes de párrafo."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return image_bytes, None, None

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return image_bytes, None, None

    h, w = gray.shape[:2]
    if h < 30 or w < 30:
        return image_bytes, None, None

    edges = cv2.Canny(gray, 70, 170)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    merged = cv2.dilate(edges, kernel, iterations=1)
    cnts, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = max(250.0, float(w * h) * 0.0012)
    boxes: List[Tuple[int, int, int, int]] = []
    for c in cnts:
        x, y, rw, rh = cv2.boundingRect(c)
        area = float(rw * rh)
        if area < min_area:
            continue
        # Filtra líneas finas típicas de texto corrido.
        if rh < int(h * 0.03) and rw > int(w * 0.45):
            continue
        if rw < int(w * 0.08) and rh < int(h * 0.08):
            continue
        boxes.append((x, y, x + rw, y + rh))

    if not boxes:
        return image_bytes, None, None

    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)

    # Margen pequeño de seguridad.
    pad_x = max(4, int(w * 0.01))
    pad_y = max(4, int(h * 0.01))
    x0 = max(0, x0 - pad_x)
    y0 = max(0, y0 - pad_y)
    x1 = min(w, x1 + pad_x)
    y1 = min(h, y1 + pad_y)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return image_bytes, None, None

    kept_ratio = float((x1 - x0) * (y1 - y0)) / float(w * h)
    if kept_ratio >= 0.97:
        return image_bytes, None, None
    if kept_ratio <= 0.10:
        return image_bytes, None, None

    crop = gray[y0:y1, x0:x1]
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        return image_bytes, None, None
    return encoded.tobytes(), (x0, y0, x1, y1), (w, h)


def map_inner_crop_to_box(
    outer_box: Box,
    crop_box_px: Tuple[int, int, int, int],
    source_size_px: Tuple[int, int],
) -> Box:
    sw, sh = source_size_px
    if sw <= 0 or sh <= 0:
        return outer_box
    x0, y0, x1, y1 = crop_box_px
    sx = outer_box.width / float(sw)
    sy = outer_box.height / float(sh)
    return Box(
        outer_box.x0 + x0 * sx,
        outer_box.y0 + y0 * sy,
        outer_box.x0 + x1 * sx,
        outer_box.y0 + y1 * sy,
    )


def apply_content_only_filter(
    image_bytes: bytes,
    clip_box: Optional[Box],
    lang: str,
    strict_content_only: bool,
    max_text_ratio: float,
) -> Tuple[Optional[bytes], Optional[Box], Optional[float], List[str]]:
    mixed, text_ratio, reasons = detect_mixed_running_text(image_bytes, lang=lang, max_text_ratio=max_text_ratio)
    if not strict_content_only or not mixed:
        return image_bytes, clip_box, text_ratio, []

    isolated, crop_box_px, src_size = isolate_visual_core(image_bytes)
    if crop_box_px is None or src_size is None:
        return None, clip_box, text_ratio, ["failed_visual_isolation", *reasons]

    new_box = clip_box
    if clip_box is not None:
        new_box = map_inner_crop_to_box(clip_box, crop_box_px, src_size)

    mixed2, text_ratio2, reasons2 = detect_mixed_running_text(isolated, lang=lang, max_text_ratio=max_text_ratio)
    if mixed2:
        return None, new_box, text_ratio2, ["failed_visual_isolation", *reasons2]
    return isolated, new_box, text_ratio2, []


def trim_bottom_dense_text(image_bytes: bytes, lang: str = "spa") -> Tuple[bytes, float]:
    """Recorta texto corrido en la parte inferior de un recorte de figura.

    Devuelve (bytes_recortados, ratio_altura_conservada).
    """
    try:
        import pytesseract
        from pytesseract import Output
    except Exception:
        return image_bytes, 1.0

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        data = pytesseract.image_to_data(img, lang=lang, output_type=Output.DICT, config="--psm 6")
        n = len(data.get("text", []))
        lines: Dict[Tuple[int, int, int], List[int]] = {}
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
            lines.setdefault(key, []).append(i)

        long_lines: List[Tuple[int, int]] = []
        for key in sorted(lines.keys(), key=lambda k: min(int(data["top"][i]) for i in lines[k])):
            idxs = lines[key]
            words = [(data["text"][i] or "").strip() for i in idxs]
            words = [wrd for wrd in words if any(ch.isalpha() for ch in wrd)]
            y0 = min(int(data["top"][i]) for i in idxs)
            y1 = max(int(data["top"][i]) + int(data["height"][i]) for i in idxs)
            x0 = min(int(data["left"][i]) for i in idxs)
            x1 = max(int(data["left"][i]) + int(data["width"][i]) for i in idxs)
            line_w = x1 - x0
            # Solo evaluamos líneas en la mitad inferior para evitar cortar leyendas de eje.
            if y0 <= int(h * 0.55):
                continue
            if len(words) >= 8 and line_w >= int(w * 0.50):
                long_lines.append((y0, y1))

        # Recortamos solo si existe un bloque de texto corrido (>=2 líneas cercanas).
        if len(long_lines) < 2:
            return image_bytes, 1.0

        long_lines.sort(key=lambda t: t[0])
        clusters: List[Tuple[int, int, int]] = []
        c_start, c_end = long_lines[0]
        c_count = 1
        max_gap = max(10, int(h * 0.06))
        for y0, y1 in long_lines[1:]:
            if y0 - c_end <= max_gap:
                c_end = max(c_end, y1)
                c_count += 1
            else:
                clusters.append((c_start, c_end, c_count))
                c_start, c_end, c_count = y0, y1, 1
        clusters.append((c_start, c_end, c_count))
        clusters = [c for c in clusters if c[2] >= 2]
        if not clusters:
            return image_bytes, 1.0

        cut_y = max(10, clusters[0][0] - 8)
        if cut_y <= int(h * 0.55) or cut_y >= h - 8:
            return image_bytes, 1.0

        crop = img.crop((0, 0, w, cut_y))
        buff = io.BytesIO()
        crop.save(buff, format="PNG")
        return buff.getvalue(), float(cut_y) / float(h)
    except Exception:
        return image_bytes, 1.0


def trim_top_non_visual_preface(image_bytes: bytes) -> Tuple[bytes, Optional[Tuple[int, int, int, int]], Optional[Tuple[int, int]]]:
    """Recorta cabecera textual en imágenes escaneadas grandes.

    Devuelve (bytes_recortados, crop_box_px, size_origen_px).
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return image_bytes, None, None

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return image_bytes, None, None

    h, w = gray.shape[:2]
    if h < 120 or w < 120:
        return image_bytes, None, None

    try:
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 15)
        n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    except Exception:
        return image_bytes, None, None

    min_area = max(120, int(w * h * 0.0006))
    structure_top: Optional[int] = None
    for i in range(1, n):
        x, y, rw, rh, area = [int(v) for v in stats[i]]
        if area < min_area:
            continue
        # Componentes estructurales (cajas, líneas, nodos) frente a glifos sueltos.
        is_structural = rw > int(w * 0.22) or rh > int(h * 0.07) or area > int(w * h * 0.002)
        if not is_structural:
            continue
        if structure_top is None or y < structure_top:
            structure_top = y

    if structure_top is None:
        return image_bytes, None, None
    # Solo recorta si la estructura empieza claramente por debajo del inicio.
    if structure_top <= int(h * 0.22):
        return image_bytes, None, None

    y0 = max(0, structure_top - max(24, int(h * 0.10)))
    if y0 <= 0 or y0 >= int(h * 0.45):
        return image_bytes, None, None

    crop = gray[y0:h, 0:w]
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        return image_bytes, None, None
    return encoded.tobytes(), (0, y0, w, h), (w, h)


def extract_caption_boxes(
    page: fitz.Page,
    terms: List[str],
    regex: re.Pattern,
    require_start: bool = False,
) -> List[Box]:
    boxes: List[Box] = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            if is_caption_line(text, terms, regex, require_start=require_start):
                x0, y0, x1, y1 = line.get("bbox", block.get("bbox"))
                boxes.append(Box(float(x0), float(y0), float(x1), float(y1)))
    return merge_boxes(boxes, x_tol=4, y_tol=3)


def extract_caption_boxes_ocr(
    page: fitz.Page,
    terms: List[str],
    regex: re.Pattern,
    lang: str = "spa",
    require_start: bool = False,
) -> List[Box]:
    """Detecta captions por OCR para PDFs escaneados."""
    try:
        import pytesseract
        from pytesseract import Output
    except Exception:
        return []

    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    w_px, h_px = img.size
    sx = page.rect.width / float(w_px)
    sy = page.rect.height / float(h_px)

    try:
        data = pytesseract.image_to_data(img, lang=lang, output_type=Output.DICT, config="--psm 6")
    except Exception:
        return []

    lines: Dict[Tuple[int, int, int], List[int]] = {}
    n = len(data.get("text", []))
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
        lines.setdefault(key, []).append(i)

    out: List[Box] = []
    for idxs in lines.values():
        line_text = " ".join((data["text"][i] or "").strip() for i in idxs).strip()
        if not line_text:
            continue
        if not is_caption_line(line_text, terms, regex, require_start=require_start):
            continue
        xs0 = [int(data["left"][i]) for i in idxs]
        ys0 = [int(data["top"][i]) for i in idxs]
        xs1 = [int(data["left"][i]) + int(data["width"][i]) for i in idxs]
        ys1 = [int(data["top"][i]) + int(data["height"][i]) for i in idxs]
        out.append(Box(min(xs0) * sx, min(ys0) * sy, max(xs1) * sx, max(ys1) * sy))

    return merge_boxes(out, x_tol=4, y_tol=3)


def detect_pymupdf_table_boxes(page: fitz.Page) -> List[Box]:
    boxes: List[Box] = []
    try:
        finder = page.find_tables()
    except Exception:
        return []

    tables = getattr(finder, "tables", None) or []
    for t in tables:
        bbox = getattr(t, "bbox", None)
        if not bbox or len(bbox) != 4:
            continue
        try:
            x0, y0, x1, y1 = [float(v) for v in bbox]
        except Exception:
            continue
        b = Box(x0, y0, x1, y1)
        if b.width < page.rect.width * TABLE_MIN_WIDTH_RATIO:
            continue
        if b.height < page.rect.height * TABLE_MIN_HEIGHT_RATIO:
            continue
        boxes.append(b)
    return merge_boxes(boxes, x_tol=8, y_tol=8)


def detect_grid_boxes(page: fitz.Page) -> List[Box]:
    try:
        import cv2
        import numpy as np
    except Exception:
        return []

    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    arr = np.frombuffer(pix.tobytes("png"), dtype=np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return []

    bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
    h, w = bw.shape
    h_long = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 20), 1))
    v_long = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 20)))
    h_short = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, w // 35), 1))
    v_short = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, h // 35)))
    h_lines = cv2.bitwise_or(
        cv2.dilate(cv2.erode(bw, h_long), h_long),
        cv2.dilate(cv2.erode(bw, h_short), h_short),
    )
    v_lines = cv2.bitwise_or(
        cv2.dilate(cv2.erode(bw, v_long), v_long),
        cv2.dilate(cv2.erode(bw, v_short), v_short),
    )
    grid = cv2.bitwise_or(h_lines, v_lines)
    cnts, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    sx = page.rect.width / float(w)
    sy = page.rect.height / float(h)
    boxes: List[Box] = []
    for c in cnts:
        x, y, rw, rh = cv2.boundingRect(c)
        if rw < int(TABLE_MIN_WIDTH_RATIO * w) or rh < int(TABLE_MIN_HEIGHT_RATIO * h):
            continue
        if rw * rh < int(0.010 * w * h):
            continue
        boxes.append(Box(x * sx, y * sy, (x + rw) * sx, (y + rh) * sy))
    return merge_boxes(boxes, x_tol=10, y_tol=10)


def detect_columnar_boxes(page: fitz.Page) -> List[Box]:
    words = page.get_text("words")
    if not words:
        return []

    rows: Dict[int, List[Tuple[float, float, float, float, str]]] = {}
    for w in words:
        x0, y0, x1, y1, txt = w[:5]
        key = int(((y0 + y1) / 2) // 7)
        rows.setdefault(key, []).append((x0, y0, x1, y1, txt))

    table_words: List[Tuple[float, float, float, float]] = []
    for items in rows.values():
        s = sorted(items, key=lambda t: t[0])
        if len(s) < 6:
            continue
        groups = 1
        prev_x1 = s[0][2]
        for x0, _, x1, _, _ in s[1:]:
            if x0 - prev_x1 > 18:
                groups += 1
            prev_x1 = x1
        if groups >= 3:
            table_words.extend((x0, y0, x1, y1) for x0, y0, x1, y1, _ in s)

    if len(table_words) < 30:
        return []

    xs0 = [w[0] for w in table_words]
    ys0 = [w[1] for w in table_words]
    xs1 = [w[2] for w in table_words]
    ys1 = [w[3] for w in table_words]
    box = Box(min(xs0), min(ys0), max(xs1), max(ys1))

    if box.width < page.rect.width * 0.35 or box.height < page.rect.height * 0.10:
        return []
    return [box]


def get_table_caption_boxes(page: fitz.Page, lang: str = "spa") -> List[Box]:
    native = extract_caption_boxes(page, TABLE_CAPTION_TERMS, TABLE_CAPTION_RE, require_start=True)
    ocr = extract_caption_boxes_ocr(
        page,
        TABLE_CAPTION_TERMS,
        TABLE_CAPTION_RE,
        lang=lang,
        require_start=True,
    )
    return merge_boxes([*native, *ocr], x_tol=4, y_tol=3)


def build_caption_anchor_zones(page: fitz.Page, caps: Sequence[Box]) -> List[Box]:
    if not caps:
        return []
    caps_sorted = sorted(caps, key=lambda c: (c.y0, c.x0))
    zones: List[Box] = []
    for idx, cap in enumerate(caps_sorted):
        next_y = caps_sorted[idx + 1].y0 if idx + 1 < len(caps_sorted) else page.rect.y1 - 6
        y0 = min(page.rect.y1, max(page.rect.y0, cap.y1 + 2))
        y1 = min(page.rect.y1, max(y0 + 20, next_y - 4))
        if y1 - y0 < page.rect.height * 0.04:
            continue
        zones.append(Box(page.rect.x0 + 4, y0, page.rect.x1 - 4, y1))
    return zones


def clip_box_png_bytes(page: fitz.Page, box: Box) -> Optional[bytes]:
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=box.to_rect(), alpha=False)
    except Exception:
        return None
    data = pix.tobytes("png")
    return data if data else None


def caption_fallback_boxes(page: fitz.Page, zones: Sequence[Box]) -> List[Box]:
    fallback: List[Box] = []
    for z in zones:
        clip = clip_box_png_bytes(page, z)
        if clip is None:
            continue
        if not image_looks_tabular(clip):
            continue

        words = page.get_text("words", clip=z.to_rect())
        if words:
            xs0 = [float(w[0]) for w in words]
            ys0 = [float(w[1]) for w in words]
            xs1 = [float(w[2]) for w in words]
            ys1 = [float(w[3]) for w in words]
            b = Box(min(xs0), min(ys0), max(xs1), max(ys1)).expand(8, 8, page.rect)
        else:
            b = z.expand(4, 4, page.rect)
        fallback.append(b)
    return merge_boxes(fallback, x_tol=8, y_tol=8)


def is_top_continuation_candidate(page: fitz.Page, box: Box) -> bool:
    top_limit = page.rect.y0 + page.rect.height * 0.22
    return (
        box.y0 <= top_limit
        and box.width >= page.rect.width * 0.45
        and box.height >= page.rect.height * TABLE_MIN_HEIGHT_RATIO
    )


def filter_table_boxes(
    page: fitz.Page,
    boxes: Sequence[Box],
    caption_zones: Sequence[Box],
) -> List[Box]:
    out: List[Box] = []
    for b in boxes:
        wr = b.width / max(1.0, page.rect.width)
        hr = b.height / max(1.0, page.rect.height)
        if wr < TABLE_MIN_WIDTH_RATIO or hr < TABLE_MIN_HEIGHT_RATIO:
            continue
        near_caption = any(b.intersects(z) for z in caption_zones)
        if hr < TABLE_MIN_HEIGHT_RATIO_NO_CAPTION and not near_caption:
            continue
        out.append(b.expand(4, 4, page.rect))
    return merge_boxes(out, x_tol=8, y_tol=8)


def detect_table_regions(pdf_path: Path, logger: logging.Logger) -> Dict[int, List[Box]]:
    regions: Dict[int, List[Box]] = {}
    with fitz.open(pdf_path) as doc:
        for pidx, page in enumerate(doc, start=1):
            boxes: List[Box] = []
            table_caps = get_table_caption_boxes(page, lang="spa")
            cap_zones = build_caption_anchor_zones(page, table_caps)

            boxes.extend(detect_pymupdf_table_boxes(page))
            boxes.extend(detect_grid_boxes(page))
            boxes.extend(detect_columnar_boxes(page))

            if cap_zones:
                anchored = [b for b in boxes if any(b.intersects(z) for z in cap_zones)]
                # Conserva también posibles continuaciones en cabecera de página.
                top_cont = [b for b in boxes if is_top_continuation_candidate(page, b)]
                fallback = caption_fallback_boxes(page, cap_zones)
                boxes = merge_boxes([*anchored, *top_cont, *fallback], x_tol=10, y_tol=10)

            merged = filter_table_boxes(page, merge_boxes(boxes, x_tol=10, y_tol=10), cap_zones)
            if merged:
                regions[pidx] = merged

    logger.info("Paginas de tabla detectadas: %s", sorted(regions.keys()))
    return regions


def dense_text_cut_y(page: fitz.Page, start_y: float) -> Optional[float]:
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            line_text = "".join(s.get("text", "") for s in spans).strip()
            if not line_text:
                continue
            words = [w for w in re.split(r"\s+", line_text) if w]
            x0, y0, x1, y1 = line.get("bbox", block.get("bbox"))
            if y0 <= start_y + 30:
                continue
            if len(words) >= 9 and (x1 - x0) >= page.rect.width * 0.45:
                return float(y0)
    return None


def add_discard_reasons(counter: Dict[str, int], reasons: Sequence[str]) -> None:
    for r in set(reasons):
        counter[r] = counter.get(r, 0) + 1


def extract_figures(
    pdf_path: Path,
    out_dir: Path,
    logger: logging.Logger,
    table_regions: Dict[int, List[Box]],
    lang: str = "spa",
    strict_content_only: bool = True,
    max_text_ratio: float = DEFAULT_MAX_TEXT_RATIO,
) -> Tuple[int, List[ExtractionRecord]]:
    ensure_dir(out_dir)
    records: List[ExtractionRecord] = []
    seen_hashes: set[str] = set()
    discard_stats: Dict[str, int] = {}

    with fitz.open(pdf_path) as doc:
        for pidx, page in enumerate(doc, start=1):
            page_tables = table_regions.get(pidx, [])
            fig_caps_native = extract_caption_boxes(page, FIGURE_CAPTION_TERMS, FIGURE_CAPTION_RE, require_start=True)
            fig_caps_ocr = extract_caption_boxes_ocr(
                page,
                FIGURE_CAPTION_TERMS,
                FIGURE_CAPTION_RE,
                lang=lang,
                require_start=True,
            )
            fig_caps = merge_boxes([*fig_caps_native, *fig_caps_ocr], x_tol=6, y_tol=4)
            saved_page = 0
            page_area = page.rect.width * page.rect.height

            image_entries: List[Tuple[int, List[Box], Box]] = []
            for img in page.get_images(full=True):
                xref = int(img[0])
                rects = [rect_to_box(r) for r in page.get_image_rects(xref)]
                if rects:
                    ub = rects[0]
                    for rr in rects[1:]:
                        ub = ub.union(rr)
                else:
                    ub = rect_to_box(page.rect)
                image_entries.append((xref, rects, ub))

            # 1) Imágenes embebidas.
            for iidx, (xref, img_rects, b) in enumerate(image_entries, start=1):
                # Descarta overlays pequeños contenidos casi totalmente en otra imagen grande.
                is_nested_overlay = False
                has_nested_children = False
                for oxref, _, ob in image_entries:
                    if oxref == xref:
                        continue
                    if b.area <= 0:
                        continue
                    inter = b.intersection_area(ob)
                    if ob.area >= b.area * 3.0 and (inter / b.area) >= 0.95:
                        is_nested_overlay = True
                        break
                    if b.area >= ob.area * 3.0 and ob.area > 0 and (inter / ob.area) >= 0.95:
                        has_nested_children = True
                if is_nested_overlay:
                    continue

                if page_tables:
                    table_overlap = (
                        max(overlap_ratio(r, page_tables) for r in img_rects)
                        if img_rects
                        else overlap_ratio(b, page_tables)
                    )
                    overlap_limit = 0.20
                    # En imágenes de página casi completa bajamos umbral:
                    # evita clasificar como figura páginas de tabla + texto.
                    if b.area >= page_area * 0.58:
                        overlap_limit = 0.12
                    if table_overlap >= overlap_limit:
                        continue

                ext = "png"
                data: Optional[bytes] = None
                # Renderiza desde la página solo si la imagen contiene overlays hijos.
                # Evita perder fragmentos al extraer bytes "crudos" de la imagen base.
                if img_rects and has_nested_children:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=b.to_rect(), alpha=False)
                    data = pix.tobytes("png")
                else:
                    base = doc.extract_image(xref)
                    data = base.get("image")
                    ext = base.get("ext", "png")
                if not data:
                    continue

                if not fig_caps and img_rects and b.area >= page_area * 0.58:
                    # Intenta aislar diagramas en cabecera y evita guardar página completa.
                    cropped, crop_box_px, src_size = isolate_top_diagram_block(data)
                    if crop_box_px is not None and src_size is not None:
                        data = cropped
                        b = map_inner_crop_to_box(b, crop_box_px, src_size)

                if image_looks_tabular(data):
                    continue
                if not fig_caps and looks_like_dense_text_block(data):
                    continue
                if looks_like_running_text(data, lang=lang) and not fig_caps:
                    continue
                if STRICT_FIGURE_PROFILE and not fig_caps and not likely_figure_content(data):
                    continue

                if img_rects:
                    # En PDFs escaneados, evita guardar la página completa como figura
                    # cuando ya hay captions de figura detectados.
                    if fig_caps and b.area >= page_area * 0.75:
                        continue
                    if b.area >= page_area * 0.80 and looks_like_running_text(data, lang=lang):
                        continue
                    if STRICT_FIGURE_PROFILE and b.area >= page_area * 0.65 and not fig_caps:
                        # En páginas escaneadas grandes sin caption, exigimos señal visual
                        # de diagrama para evitar extraer texto corrido completo.
                        if not has_large_graphic_blocks(data):
                            continue
                    # Si es una imagen escaneada grande sin caption detectable, limpia cabecera textual.
                    if not fig_caps and has_nested_children and b.area >= page_area * 0.45:
                        trimmed, crop_box_px, src_size = trim_top_non_visual_preface(data)
                        if crop_box_px is not None and src_size is not None:
                            data = trimmed
                            b = map_inner_crop_to_box(b, crop_box_px, src_size)

                filtered, filtered_box, text_ratio, discard_reasons = apply_content_only_filter(
                    data,
                    clip_box=b,
                    lang=lang,
                    strict_content_only=strict_content_only,
                    max_text_ratio=max_text_ratio,
                )
                if filtered is None:
                    add_discard_reasons(discard_stats, discard_reasons)
                    continue
                if filtered_box is not None and filtered_box != b:
                    ext = "png"
                    b = filtered_box
                data = filtered

                h = img_hash(data)
                if h in seen_hashes:
                    continue

                out = out_dir / f"figure_page_{pidx:03d}_img_{iidx:02d}.{ext}"
                save_bytes(out, data)
                seen_hashes.add(h)
                records.append(ExtractionRecord("figure", pidx, str(out), "embedded", (b.x0, b.y0, b.x1, b.y1), text_ratio=text_ratio))
                saved_page += 1

            # 2) Figuras guiadas por caption (incluye vectoriales sin imagen embebida).
            for cidx, cap in enumerate(fig_caps, start=1):
                down_candidates: List[Box] = []
                up_candidates: List[Box] = []

                # Candidato 1: figura sobre el caption (caso más habitual).
                up_start = max(page.rect.y0 + 20, cap.y0 - page.rect.height * 0.55)
                up_end = cap.y0 - 8
                if up_end - up_start > 40:
                    up_candidates.append(Box(page.rect.x0 + 18, up_start, page.rect.x1 - 18, up_end))

                # Candidato 2: figura bajo el caption.
                down_start = cap.y1 + 6
                down_end = min(page.rect.y1 - 20, cap.y1 + page.rect.height * 0.45)
                cut = dense_text_cut_y(page, down_start)
                if cut is not None and cut < down_end:
                    down_end = cut - 8
                if down_end - down_start > 40:
                    down_candidates.append(Box(page.rect.x0 + 18, down_start, page.rect.x1 - 18, down_end))

                best: Tuple[float, Box, bytes, Optional[float]] | None = None
                for bucket in (down_candidates, up_candidates):
                    for clip in bucket:
                        if overlap_ratio(clip, page_tables) >= 0.25:
                            continue
                        if clip.area >= page.rect.width * page.rect.height * 0.75:
                            continue
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip.to_rect(), alpha=False)
                        data = pix.tobytes("png")
                        if image_looks_tabular(data):
                            continue
                        if not fig_caps and looks_like_dense_text_block(data):
                            continue
                        data, keep_ratio = trim_bottom_dense_text(data, lang=lang)
                        if keep_ratio < 1.0:
                            new_h = clip.height * keep_ratio
                            clip = Box(clip.x0, clip.y0, clip.x1, clip.y0 + new_h)
                        filtered, filtered_clip, text_ratio, discard_reasons = apply_content_only_filter(
                            data,
                            clip_box=clip,
                            lang=lang,
                            strict_content_only=strict_content_only,
                            max_text_ratio=max_text_ratio,
                        )
                        if filtered is None:
                            add_discard_reasons(discard_stats, discard_reasons)
                            continue
                        data = filtered
                        if filtered_clip is not None:
                            clip = filtered_clip
                        words, avg = ocr_metrics(data, lang=lang)
                        if words >= 220 and avg >= 8.0:
                            continue
                        score = figure_visual_score(data)
                        if STRICT_FIGURE_PROFILE and score < 0.007:
                            continue
                        # Leve preferencia por recortes más compactos.
                        score = score - (clip.area / (page.rect.width * page.rect.height)) * 0.03
                        if best is None or score > best[0]:
                            best = (score, clip, data, text_ratio)
                    # Si encontramos candidato abajo, no exploramos arriba.
                    if best is not None and bucket is down_candidates:
                        break

                if best is None:
                    continue

                _, clip, data, text_ratio = best
                h = img_hash(data)
                if h in seen_hashes:
                    continue

                out = out_dir / f"figure_page_{pidx:03d}_cap_{cidx:02d}.png"
                save_bytes(out, data)
                seen_hashes.add(h)
                records.append(ExtractionRecord("figure", pidx, str(out), "caption-guided", (clip.x0, clip.y0, clip.x1, clip.y1), text_ratio=text_ratio))
                saved_page += 1

            # 3) Fallback vectorial si no salió nada en la página.
            if saved_page == 0:
                draw_boxes: List[Box] = []
                for d in page.get_drawings():
                    rect = d.get("rect")
                    if not isinstance(rect, fitz.Rect) or rect.is_empty:
                        continue
                    b = rect_to_box(rect)
                    if b.width < page.rect.width * 0.18 or b.height < page.rect.height * 0.10:
                        continue
                    if b.area < page.rect.width * page.rect.height * 0.012:
                        continue
                    if overlap_ratio(b, page_tables) >= 0.22:
                        continue
                    draw_boxes.append(b)

                for didx, b in enumerate(merge_boxes(draw_boxes, x_tol=6, y_tol=6), start=1):
                    if b.area >= page.rect.width * page.rect.height * 0.65 and not fig_caps:
                        continue
                    clip = b.expand(8, 8, page.rect)
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip.to_rect(), alpha=False)
                    data = pix.tobytes("png")
                    if image_looks_tabular(data):
                        continue
                    if looks_like_running_text(data, lang=lang) and not fig_caps:
                        continue
                    if STRICT_FIGURE_PROFILE and not fig_caps and not likely_figure_content(data):
                        continue
                    filtered, filtered_clip, text_ratio, discard_reasons = apply_content_only_filter(
                        data,
                        clip_box=clip,
                        lang=lang,
                        strict_content_only=strict_content_only,
                        max_text_ratio=max_text_ratio,
                    )
                    if filtered is None:
                        add_discard_reasons(discard_stats, discard_reasons)
                        continue
                    data = filtered
                    if filtered_clip is not None:
                        clip = filtered_clip

                    h = img_hash(data)
                    if h in seen_hashes:
                        continue

                    out = out_dir / f"figure_page_{pidx:03d}_draw_{didx:02d}.png"
                    save_bytes(out, data)
                    seen_hashes.add(h)
                    records.append(ExtractionRecord("figure", pidx, str(out), "vector-fallback", (clip.x0, clip.y0, clip.x1, clip.y1), text_ratio=text_ratio))

    if strict_content_only and discard_stats:
        details = ", ".join(f"{k}={v}" for k, v in sorted(discard_stats.items()))
        logger.info("Descartes por contenido mixto: %s", details)
    logger.info("Figuras extraidas: %d", len(records))
    return len(records), records


def group_consecutive(values: Sequence[int]) -> List[List[int]]:
    if not values:
        return []
    groups: List[List[int]] = [[values[0]]]
    for v in values[1:]:
        if v == groups[-1][-1] + 1:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def union_table_box(page: fitz.Page, rects: List[Box]) -> Box:
    if rects:
        box = rects[0]
        for rr in rects[1:]:
            box = box.union(rr)
    else:
        box = rect_to_box(page.rect)
    return box.expand(6, 6, page.rect)


def boxes_overlap_strong(a: Box, b: Box, ratio: float = 0.90) -> bool:
    min_area = max(1.0, min(a.area, b.area))
    return (a.intersection_area(b) / min_area) >= ratio


def choose_primary_table_box(page: fitz.Page, boxes: Sequence[Box], caption_boxes: Sequence[Box]) -> Box:
    if not boxes:
        return rect_to_box(page.rect)
    if len(boxes) == 1:
        return boxes[0]

    zones = build_caption_anchor_zones(page, caption_boxes)
    page_area = max(1.0, page.rect.width * page.rect.height)
    scored: List[Tuple[float, Box]] = []
    for b in boxes:
        area_ratio = b.area / page_area
        cap_ratio = 0.0
        if zones:
            cap_ratio = max((b.intersection_area(z) / max(1.0, b.area)) for z in zones)
        top_bonus = 0.10 if is_top_continuation_candidate(page, b) else 0.0
        score = area_ratio + (0.65 * cap_ratio) + top_bonus
        scored.append((score, b))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def page_has_table_caption(page: fitz.Page, lang: str = "spa") -> bool:
    return bool(get_table_caption_boxes(page, lang=lang))


def should_merge_table_pages(
    prev_page: fitz.Page,
    prev_box: Box,
    next_page: fitz.Page,
    next_box: Box,
    next_has_caption: bool,
) -> bool:
    # Si la siguiente página tiene caption propio de tabla, iniciamos tabla nueva.
    if next_has_caption:
        return False

    prev_bottom_gap = prev_page.rect.y1 - prev_box.y1
    next_top_gap = next_box.y0 - next_page.rect.y0
    prev_h = max(1.0, prev_page.rect.height)
    next_h = max(1.0, next_page.rect.height)

    # Continuidad vertical típica: la anterior cae al pie y la siguiente arranca arriba.
    if prev_bottom_gap > prev_h * 0.18:
        return False
    if next_top_gap > next_h * 0.20:
        return False

    prev_wr = prev_box.width / max(1.0, prev_page.rect.width)
    next_wr = next_box.width / max(1.0, next_page.rect.width)
    if abs(prev_wr - next_wr) > 0.15:
        return False

    overlap_x = max(0.0, min(prev_box.x1, next_box.x1) - max(prev_box.x0, next_box.x0))
    min_w = max(1.0, min(prev_box.width, next_box.width))
    if (overlap_x / min_w) < 0.65:
        return False

    return True


def build_table_merge_groups(
    doc: fitz.Document,
    table_regions: Dict[int, List[Box]],
    lang: str = "spa",
) -> Tuple[List[List[int]], Dict[int, Box]]:
    pages = sorted(table_regions.keys())
    if not pages:
        return [], {}

    page_boxes: Dict[int, Box] = {}
    has_caption: Dict[int, bool] = {}
    for p in pages:
        page = doc[p - 1]
        caption_boxes = get_table_caption_boxes(page, lang=lang)
        has_caption[p] = bool(caption_boxes)
        page_boxes[p] = choose_primary_table_box(page, table_regions.get(p, []), caption_boxes).expand(4, 4, page.rect)

    groups: List[List[int]] = []
    idx = 0
    while idx < len(pages):
        group = [pages[idx]]
        j = idx
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            prev_p = pages[j]
            next_p = pages[j + 1]
            if should_merge_table_pages(
                doc[prev_p - 1],
                page_boxes[prev_p],
                doc[next_p - 1],
                page_boxes[next_p],
                has_caption[next_p],
            ):
                group.append(next_p)
                j += 1
            else:
                break
        groups.append(group)
        idx = j + 1

    return groups, page_boxes


def extract_tables_as_images(
    pdf_path: Path,
    out_dir: Path,
    logger: logging.Logger,
    table_regions: Dict[int, List[Box]],
) -> Tuple[int, List[ExtractionRecord]]:
    ensure_dir(out_dir)
    records: List[ExtractionRecord] = []

    with fitz.open(pdf_path) as doc:
        groups, page_boxes = build_table_merge_groups(doc, table_regions, lang="spa")
        logger.info("Grupos de tablas a exportar: %s", groups)
        for group in groups:
            if len(group) > 1:
                parts: List[Image.Image] = []
                union_box: Optional[Box] = None
                for p in group:
                    page = doc[p - 1]
                    box = page_boxes.get(p, union_table_box(page, table_regions.get(p, [])))
                    union_box = box if union_box is None else union_box.union(box)
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=box.to_rect(), alpha=False)
                    parts.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))

                if parts:
                    max_w = max(im.width for im in parts)
                    total_h = sum(im.height for im in parts)
                    canvas = Image.new("RGB", (max_w, total_h), (255, 255, 255))
                    y = 0
                    for im in parts:
                        canvas.paste(im, (0, y))
                        y += im.height
                    out = out_dir / f"table_pages_{group[0]:03d}-{group[-1]:03d}.png"
                    canvas.save(out)
                    bbox = union_box if union_box is not None else rect_to_box(doc[group[0] - 1].rect)
                    records.append(ExtractionRecord("table", group[0], str(out), "merged-pages", (bbox.x0, bbox.y0, bbox.x1, bbox.y1)))
            else:
                p = group[0]
                page = doc[p - 1]
                box = page_boxes.get(p, union_table_box(page, table_regions.get(p, [])))
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=box.to_rect(), alpha=False)
                out = out_dir / f"table_page_{p:03d}.png"
                save_bytes(out, pix.tobytes("png"))
                records.append(ExtractionRecord("table", p, str(out), "region", (box.x0, box.y0, box.x1, box.y1)))

        # Exporta cajas adicionales por página cuando hay varias tablas distintas.
        for p, boxes in sorted(table_regions.items()):
            if not boxes:
                continue
            page = doc[p - 1]
            primary = page_boxes.get(p)
            extra_idx = 0
            for b in sorted(boxes, key=lambda bb: (bb.y0, bb.x0)):
                if primary is not None and boxes_overlap_strong(primary, b, ratio=0.90):
                    continue
                extra_idx += 1
                clip = b.expand(4, 4, page.rect)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip.to_rect(), alpha=False)
                out = out_dir / f"table_page_{p:03d}_extra_{extra_idx:02d}.png"
                save_bytes(out, pix.tobytes("png"))
                records.append(ExtractionRecord("table", p, str(out), "extra-region", (clip.x0, clip.y0, clip.x1, clip.y1)))

    logger.info("Tablas extraidas como imagen: %d", len(records))
    return len(records), records


def write_manifest(path: Path, records: Iterable[ExtractionRecord], pdf_path: Path) -> None:
    data = {
        "source_pdf": str(pdf_path),
        "items": [asdict(r) for r in records],
    }
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_previous_media(media_dir: Path) -> None:
    ensure_dir(media_dir)
    for p in media_dir.iterdir():
        if not p.is_file():
            continue
        if p.name.startswith("media_") or p.name.startswith("__tmp_"):
            p.unlink()


def renumber_media_files(records: List[ExtractionRecord], media_dir: Path) -> List[ExtractionRecord]:
    """Renombra salidas de forma consecutiva según orden de lectura."""
    if not records:
        return records

    # Orden textual: página -> y superior -> x izquierda.
    ordered = sorted(records, key=lambda r: (r.page, r.bbox[1], r.bbox[0]))

    # Paso 1: nombres temporales para evitar colisiones durante el rename.
    for idx, rec in enumerate(ordered, start=1):
        src = Path(rec.path)
        if not src.exists():
            continue
        tmp = media_dir / f"__tmp_{idx:04d}{src.suffix.lower()}"
        src.rename(tmp)
        rec.path = str(tmp)

    # Paso 2: nombres finales consecutivos.
    for idx, rec in enumerate(ordered, start=1):
        src = Path(rec.path)
        kind = "figura" if rec.kind == "figure" else "tabla"
        dst = media_dir / f"media_{idx:03d}_{kind}{src.suffix.lower()}"
        src.rename(dst)
        rec.path = str(dst)

    return ordered


def filter_redundant_figures(records: List[ExtractionRecord]) -> List[ExtractionRecord]:
    """Elimina figuras embebidas que contienen casi por completo una figura caption-guided."""
    by_page: Dict[int, List[ExtractionRecord]] = {}
    for r in records:
        by_page.setdefault(r.page, []).append(r)

    kept: List[ExtractionRecord] = []
    for _, page_items in by_page.items():
        guided = [r for r in page_items if r.kind == "figure" and r.method == "caption-guided"]
        page_kept: List[ExtractionRecord] = []
        for r in page_items:
            if r.kind == "figure" and r.method == "embedded" and guided:
                rb = Box(*r.bbox)
                drop = False
                for g in guided:
                    gb = Box(*g.bbox)
                    inter = rb.intersection_area(gb)
                    if gb.area > 0 and (inter / gb.area) >= 0.60 and rb.area >= gb.area * 1.3:
                        drop = True
                        break
                if drop:
                    continue
            page_kept.append(r)
        kept.extend(page_kept)
    return kept


def main() -> int:
    warnings.filterwarnings("ignore")
    logger = setup_logger()
    args = parse_args()
    if args.max_text_ratio <= 0.0 or args.max_text_ratio >= 1.0:
        logger.error("Parametro invalido --max-text-ratio=%s (debe estar entre 0 y 1).", args.max_text_ratio)
        return 1
    if args.strict_content_only and not ocr_engine_available():
        logger.warning("OCR no disponible: 'strict-content-only' no podra detectar texto corrido con fiabilidad.")
    if args.pdf:
        pdf_path = Path(args.pdf).expanduser().resolve()
    else:
        auto_pdf = choose_default_pdf(Path.cwd(), logger)
        if auto_pdf is None:
            logger.error("No se encontró ningún PDF en el directorio actual: %s", Path.cwd())
            return 1
        pdf_path = auto_pdf.resolve()

    if not pdf_path.exists() or not pdf_path.is_file():
        logger.error("No se encuentra el PDF: %s", pdf_path)
        return 1

    out_dir = (pdf_path.parent / f"{pdf_path.stem}_extraido").resolve()
    media_dir = out_dir / "media"
    manifest_path = out_dir / "manifest.json"
    ensure_dir(out_dir)

    logger.info("Inicio de proceso: %s", pdf_path)
    try:
        clear_previous_media(media_dir)
        table_regions = detect_table_regions(pdf_path, logger)
        fig_count, fig_records = extract_figures(
            pdf_path,
            media_dir,
            logger,
            table_regions=table_regions,
            lang="spa",
            strict_content_only=args.strict_content_only,
            max_text_ratio=float(args.max_text_ratio),
        )
        tab_count, tab_records = extract_tables_as_images(pdf_path, media_dir, logger, table_regions=table_regions)
        all_records = filter_redundant_figures([*fig_records, *tab_records])
        ordered_records = renumber_media_files(all_records, media_dir)
        write_manifest(manifest_path, ordered_records, pdf_path)
        logger.info(
            "Resumen -> figuras: %d | tablas(img): %d | strict_content_only: %s | max_text_ratio: %.3f | salida: %s",
            fig_count,
            tab_count,
            "on" if args.strict_content_only else "off",
            args.max_text_ratio,
            out_dir,
        )
        return 0
    except Exception as exc:
        logger.exception("Error durante el procesamiento: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
