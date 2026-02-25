#!/usr/bin/env python3
"""Extractor profesional de PDF: figuras y tablas como imágenes.

Uso:
    python3 pdf_extractor_unificado.py "archivo.pdf"

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
OCR_CACHE: Dict[str, Tuple[int, float]] = {}


@dataclass
class ExtractionRecord:
    kind: str
    page: int
    path: str
    method: str
    bbox: Tuple[float, float, float, float]


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
    parser.add_argument("pdf", help="Ruta del PDF")
    return parser.parse_args()


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
    key = img_hash(image_bytes)
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
    return hc >= 2 and vc >= 2


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

        cut_y = None
        for key in sorted(lines.keys(), key=lambda k: min(int(data["top"][i]) for i in lines[k])):
            idxs = lines[key]
            words = [(data["text"][i] or "").strip() for i in idxs]
            words = [wrd for wrd in words if any(ch.isalpha() for ch in wrd)]
            y0 = min(int(data["top"][i]) for i in idxs)
            x0 = min(int(data["left"][i]) for i in idxs)
            x1 = max(int(data["left"][i]) + int(data["width"][i]) for i in idxs)
            line_w = x1 - x0
            if y0 <= int(h * 0.45):
                continue
            if len(words) >= 8 and line_w >= int(w * 0.45):
                cut_y = max(10, y0 - 8)
                break

        if cut_y is None or cut_y >= h - 8:
            return image_bytes, 1.0
        if cut_y <= int(h * 0.35):
            return image_bytes, 1.0

        crop = img.crop((0, 0, w, cut_y))
        buff = io.BytesIO()
        crop.save(buff, format="PNG")
        return buff.getvalue(), float(cut_y) / float(h)
    except Exception:
        return image_bytes, 1.0


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
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 20), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 20)))
    h_lines = cv2.dilate(cv2.erode(bw, h_kernel), h_kernel)
    v_lines = cv2.dilate(cv2.erode(bw, v_kernel), v_kernel)
    grid = cv2.bitwise_or(h_lines, v_lines)
    cnts, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    sx = page.rect.width / float(w)
    sy = page.rect.height / float(h)
    boxes: List[Box] = []
    for c in cnts:
        x, y, rw, rh = cv2.boundingRect(c)
        if rw < int(0.25 * w) or rh < int(0.06 * h):
            continue
        if rw * rh < int(0.015 * w * h):
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


def detect_table_regions(pdf_path: Path, logger: logging.Logger) -> Dict[int, List[Box]]:
    regions: Dict[int, List[Box]] = {}
    with fitz.open(pdf_path) as doc:
        for pidx, page in enumerate(doc, start=1):
            boxes: List[Box] = []

            boxes.extend(detect_grid_boxes(page))
            boxes.extend(detect_columnar_boxes(page))

            # Refuerzo por caption de tabla.
            table_caps = extract_caption_boxes(page, TABLE_CAPTION_TERMS, TABLE_CAPTION_RE, require_start=True)
            if table_caps and boxes:
                anchored: List[Box] = []
                for cap in table_caps:
                    cap_zone = Box(page.rect.x0, max(page.rect.y0, cap.y0 - 20), page.rect.x1, min(page.rect.y1, cap.y1 + page.rect.height * 0.65))
                    for b in boxes:
                        if b.intersects(cap_zone):
                            anchored.append(b)
                if anchored:
                    boxes = anchored

            merged = [b for b in merge_boxes(boxes, x_tol=10, y_tol=10) if b.width >= page.rect.width * 0.22 and b.height >= page.rect.height * 0.06]
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


def extract_figures(
    pdf_path: Path,
    out_dir: Path,
    logger: logging.Logger,
    table_regions: Dict[int, List[Box]],
    lang: str = "spa",
) -> Tuple[int, List[ExtractionRecord]]:
    ensure_dir(out_dir)
    records: List[ExtractionRecord] = []
    seen_hashes: set[str] = set()

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

            # 1) Imágenes embebidas.
            for iidx, img in enumerate(page.get_images(full=True), start=1):
                xref = img[0]
                img_rects = [rect_to_box(r) for r in page.get_image_rects(xref)]
                if img_rects and max(overlap_ratio(r, page_tables) for r in img_rects) >= 0.20:
                    continue

                base = doc.extract_image(xref)
                data = base.get("image")
                ext = base.get("ext", "png")
                if not data:
                    continue

                if image_looks_tabular(data):
                    continue
                if looks_like_running_text(data, lang=lang) and not fig_caps:
                    continue
                if STRICT_FIGURE_PROFILE and not fig_caps and not likely_figure_content(data):
                    continue

                if img_rects:
                    b = img_rects[0]
                    for rr in img_rects[1:]:
                        b = b.union(rr)
                    page_area = page.rect.width * page.rect.height
                    # En PDFs escaneados, evita guardar la página completa como figura
                    # cuando ya hay captions de figura detectados.
                    if fig_caps and b.area >= page_area * 0.75:
                        continue
                    if b.area >= page_area * 0.80 and looks_like_running_text(data, lang=lang):
                        continue
                    if STRICT_FIGURE_PROFILE and b.area >= page_area * 0.65 and not fig_caps:
                        continue

                h = img_hash(data)
                if h in seen_hashes:
                    continue

                out = out_dir / f"figure_page_{pidx:03d}_img_{iidx:02d}.{ext}"
                save_bytes(out, data)
                seen_hashes.add(h)
                bbox = img_rects[0] if img_rects else rect_to_box(page.rect)
                records.append(ExtractionRecord("figure", pidx, str(out), "embedded", (bbox.x0, bbox.y0, bbox.x1, bbox.y1)))
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

                best: Tuple[float, Box, bytes] | None = None
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
                        data, keep_ratio = trim_bottom_dense_text(data, lang=lang)
                        if keep_ratio < 1.0:
                            new_h = clip.height * keep_ratio
                            clip = Box(clip.x0, clip.y0, clip.x1, clip.y0 + new_h)
                        words, avg = ocr_metrics(data, lang=lang)
                        if words >= 220 and avg >= 8.0:
                            continue
                        score = figure_visual_score(data)
                        if STRICT_FIGURE_PROFILE and score < 0.007:
                            continue
                        # Leve preferencia por recortes más compactos.
                        score = score - (clip.area / (page.rect.width * page.rect.height)) * 0.03
                        if best is None or score > best[0]:
                            best = (score, clip, data)
                    # Si encontramos candidato abajo, no exploramos arriba.
                    if best is not None and bucket is down_candidates:
                        break

                if best is None:
                    continue

                _, clip, data = best
                h = img_hash(data)
                if h in seen_hashes:
                    continue

                out = out_dir / f"figure_page_{pidx:03d}_cap_{cidx:02d}.png"
                save_bytes(out, data)
                seen_hashes.add(h)
                records.append(ExtractionRecord("figure", pidx, str(out), "caption-guided", (clip.x0, clip.y0, clip.x1, clip.y1)))
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

                    h = img_hash(data)
                    if h in seen_hashes:
                        continue

                    out = out_dir / f"figure_page_{pidx:03d}_draw_{didx:02d}.png"
                    save_bytes(out, data)
                    seen_hashes.add(h)
                    records.append(ExtractionRecord("figure", pidx, str(out), "vector-fallback", (clip.x0, clip.y0, clip.x1, clip.y1)))

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


def extract_tables_as_images(
    pdf_path: Path,
    out_dir: Path,
    logger: logging.Logger,
    table_regions: Dict[int, List[Box]],
) -> Tuple[int, List[ExtractionRecord]]:
    ensure_dir(out_dir)
    records: List[ExtractionRecord] = []

    with fitz.open(pdf_path) as doc:
        pages = sorted(table_regions.keys())
        groups = group_consecutive(pages)
        for group in groups:
            if len(group) > 1:
                parts: List[Image.Image] = []
                union_box: Optional[Box] = None
                for p in group:
                    page = doc[p - 1]
                    rects = table_regions.get(p, [])
                    if rects:
                        box = rects[0]
                        for rr in rects[1:]:
                            box = box.union(rr)
                    else:
                        box = rect_to_box(page.rect)
                    box = box.expand(6, 6, page.rect)
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
                rects = table_regions.get(p, [])
                if rects:
                    box = rects[0]
                    for rr in rects[1:]:
                        box = box.union(rr)
                else:
                    box = rect_to_box(page.rect)
                box = box.expand(6, 6, page.rect)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=box.to_rect(), alpha=False)
                out = out_dir / f"table_page_{p:03d}.png"
                save_bytes(out, pix.tobytes("png"))
                records.append(ExtractionRecord("table", p, str(out), "region", (box.x0, box.y0, box.x1, box.y1)))

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

    pdf_path = Path(args.pdf).expanduser().resolve()
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
        fig_count, fig_records = extract_figures(pdf_path, media_dir, logger, table_regions=table_regions, lang="spa")
        tab_count, tab_records = extract_tables_as_images(pdf_path, media_dir, logger, table_regions=table_regions)
        all_records = filter_redundant_figures([*fig_records, *tab_records])
        ordered_records = renumber_media_files(all_records, media_dir)
        write_manifest(manifest_path, ordered_records, pdf_path)
        logger.info("Resumen -> figuras: %d | tablas(img): %d | salida: %s", fig_count, tab_count, out_dir)
        return 0
    except Exception as exc:
        logger.exception("Error durante el procesamiento: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
