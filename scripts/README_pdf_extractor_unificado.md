# PDF Extractor Unificado

Script único para extraer **imágenes**, **tablas** y **texto OCR** desde un PDF.

Archivo principal:
- `scripts/pdf_extractor_unificado.py`

## 1. Requisitos

### 1.1. Python
- Python 3.10 o superior recomendado.

### 1.2. Dependencias de Python
Instalación rápida:

```bash
python3 -m pip install pymupdf pillow camelot-py pandas openpyxl pytesseract
```

Si vas a usar Camelot con `lattice`, puede necesitar OpenCV/Ghostscript según el sistema.

### 1.3. Dependencias del sistema
- **Tesseract OCR** (obligatorio para OCR):

```bash
# macOS
brew install tesseract
brew install tesseract-lang
```

- **Ghostscript** (recomendado para extracción de tablas con Camelot):

```bash
# macOS
brew install ghostscript
```

## 2. Uso básico

```bash
python3 scripts/pdf_extractor_unificado.py "ruta/al/archivo.pdf" --output "salida"
```

Esto genera:
- `salida/images/` (imágenes extraídas)
- `salida/tables/` (tablas extraídas)
- `salida/ocr_text.txt` (solo si se ejecuta OCR)

## 3. Opciones principales

- `--ocr auto|force|off`
  - `auto`: OCR solo si el PDF no tiene texto nativo.
  - `force`: OCR siempre.
  - `off`: nunca ejecutar OCR.

- `--ocr-lang spa`
  - Idioma OCR de Tesseract.

- `--ocr-dpi 200`
  - Resolución de rasterizado para OCR.

- `--tables-flavor auto|lattice|stream`
  - Estrategia de Camelot para detectar tablas.

- `--tables-format xlsx|csv|both|md|all|img`
  - Formato de salida de tablas.
  - `md`: exporta cada tabla como Markdown.
  - `all`: exporta en `xlsx`, `csv` y `md`.
  - `img`: exporta tablas como imágenes de página (`table_page_XXX.png`).

- `--skip-images`
  - No extraer imágenes.

- `--image-strategy embedded|page|both`
  - `embedded`: extrae imágenes incrustadas.
  - `page`: genera una imagen completa por página.
  - `both`: guarda ambas.

- `--no-merge-page-images`
  - Desactiva la generación de imagen unificada por página cuando detecta varios fragmentos embebidos.

- `--allow-text-images`
  - Permite guardar imágenes que parecen bloques de texto.

- `--text-image-threshold 40`
  - Umbral de palabras OCR para filtrar imágenes de texto.

- `--text-image-line-threshold 8.0`
  - Umbral de media de palabras por línea OCR para detectar bloques de párrafo.

- `--image-pages "12,22,24,29,31"`
  - Limita extracción de imágenes a páginas concretas.

- `--table-pages "5,6"`
  - Limita extracción de tablas a páginas concretas.

- `--skip-tables`
  - No extraer tablas.

- `--save-native-text`
  - Guarda texto nativo en `native_text.txt` cuando exista.

- `--verbose`
  - Activa logs detallados.

## 4. Ejemplos

### 4.1. Extracción completa automática

```bash
python3 scripts/pdf_extractor_unificado.py "documento.pdf" --output "out_pdf" --ocr auto --tables-format both
```

### 4.2. Solo tablas en CSV

```bash
python3 scripts/pdf_extractor_unificado.py "documento.pdf" --output "out_tablas" --skip-images --ocr off --tables-format csv
```

### 4.3. OCR forzado en español

```bash
python3 scripts/pdf_extractor_unificado.py "escaneado.pdf" --output "out_ocr" --ocr force --ocr-lang spa --skip-tables
```

### 4.4. Evitar tablas partidas en varias imágenes

```bash
python3 scripts/pdf_extractor_unificado.py "documento.pdf" --output "out_img" --image-strategy embedded
```

Por defecto, cuando una página contiene varios fragmentos de imagen, el script genera además:
- `page_XXX_merged_images.png`

Así puedes subir una única imagen completa al repositorio aunque el PDF internamente tenga la tabla fragmentada.

### 4.5. Extraer tablas en Markdown

```bash
python3 scripts/pdf_extractor_unificado.py "documento.pdf" --output "out_md" --skip-images --tables-format md
```

Genera ficheros como:
- `out_md/tables/table_001.md`
- `out_md/tables/table_002.md`

Si no hay tabla estructurada detectable, el script intenta un fallback OCR y genera:
- `out_md/tables/table_ocr_fallback.md`

### 4.6. Caso dirigido por páginas (figuras y tabla partida)

```bash
python3 scripts/pdf_extractor_unificado.py "documento.pdf" \
  --output "out_dirigido" \
  --image-pages "12,22,24,29,31" \
  --table-pages "5,6" \
  --tables-format img \
  --ocr off
```

## 5. Errores comunes

- `Falta dependencia 'pymupdf'`:
  - Instalar con `pip install pymupdf`.

- `Faltan dependencias para tablas`:
  - Instalar `camelot-py`, `pandas`, `openpyxl` y revisar Ghostscript.

- OCR no funciona:
  - Verificar instalación de Tesseract y paquete de idioma (`spa`).

## 6. Ayuda rápida

```bash
python3 scripts/pdf_extractor_unificado.py --help
```
