# Transcribir video a texto (`transcribir_video.py`)

Este script permite transcribir vídeos con Whisper y generar salidas profesionales en varios formatos, incluyendo análisis temático y apuntes estructurados.

## Características principales

- Transcripción automática con Whisper.
- Salidas múltiples: `txt`, `md`, `srt`, `vtt`, `json`.
- Generación de análisis (`.analysis.md`) con:
  - palabras clave,
  - puntos clave,
  - segmentos con marcas de tiempo,
  - detección de cambios de tema.
- Generación de apuntes académicos (`.apuntes.md`) usando plantilla (`scripts/formato.md`).
- Progreso en porcentaje durante la ejecución.
- Soporte de SSL corporativo (`--ssl-cert-file`).
- Configuración por archivo (`--config` en JSON/YAML).
- Modo todo en uno (`--bundle`).

## Requisitos

- Python 3.10+
- `ffmpeg` instalado en el sistema.
- Dependencias Python:

```bash
pip install openai-whisper
```

Si usas archivo de configuración YAML:

```bash
pip install pyyaml
```

## Uso rápido

```bash
python3 scripts/transcribir_video.py /ruta/video.mp4 --overwrite
```

## Modo profesional recomendado (todo en uno)

```bash
python3 scripts/transcribir_video.py /ruta/video.mp4 \
  --bundle \
  --notes-title "Título del tema" \
  --notes-module "ORG" \
  --notes-author "Nombre Apellido" \
  --overwrite
```

Este modo genera en una pasada:

- `video.txt`
- `video.md`
- `video.analysis.md`
- `video.apuntes.md`

## Opciones clave

- `--config`: carga parámetros desde `.json/.yaml/.yml`.
- `--topic-threshold`: sensibilidad de cambios de tema (0-1).
- `--analyze`: genera análisis temático.
- `--generate-notes`: genera apuntes en Markdown.
- `--template-file`: plantilla para apuntes (por defecto `scripts/formato.md`).
- `--extra-formats`: formatos adicionales de salida.
- `--timestamps`: incluye marcas temporales.
- `--ssl-cert-file`: certificado PEM corporativo.
- `--insecure-ssl`: desactiva verificación SSL (solo último recurso).

## Ejemplos

### 1) Transcripción con dos formatos

```bash
python3 scripts/transcribir_video.py /ruta/video.mp4 \
  -f txt --extra-formats md \
  --timestamps --overwrite
```

### 2) Análisis con umbral de tema personalizado

```bash
python3 scripts/transcribir_video.py /ruta/video.mp4 \
  --analyze --topic-threshold 0.18 --overwrite
```

### 3) Configuración por archivo

```bash
python3 scripts/transcribir_video.py --config scripts/config_transcripcion.yaml --overwrite
```

Ejemplo mínimo de `scripts/config_transcripcion.yaml`:

```yaml
video: /ruta/video.mp4
model: base
language: es
format: txt
extra_formats: [md]
timestamps: true
analyze: true
generate_notes: true
notes_title: "Tema de ejemplo"
notes_module: ORG
notes_author: "Nombre Apellido"
topic_threshold: 0.22
```

## Salida y calidad

- El script escribe resultados de forma atómica para evitar archivos incompletos.
- Si no se usa `--overwrite`, no sobrescribe salidas existentes.
- Si hay error SSL, usar `--ssl-cert-file` con certificado PEM corporativo.

## Archivos relacionados

- Script principal: `scripts/transcribir_video.py`
- Plantilla de apuntes: `scripts/formato.md`

**Fecha de actualización:** 22/02/2026
