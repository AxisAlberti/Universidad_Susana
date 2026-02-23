#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Une ficheros GIFT de la raiz en un unico fichero."
    )
    parser.add_argument(
        "-r",
        "--root",
        type=Path,
        default=None,
        help="Directorio raiz donde buscar los .gift (por defecto: raiz del repo).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Fichero de salida (por defecto: <raiz_repo>/export_total.gift).",
    )
    parser.add_argument(
        "-p",
        "--pattern",
        default="*.gift",
        help="Patron de busqueda de entrada (por defecto: *.gift).",
    )
    return parser.parse_args()


def list_input_files(root: Path, pattern: str, output_path: Path) -> list[Path]:
    output_abs = output_path.resolve()
    files = sorted(
        file
        for file in root.iterdir()
        if file.is_file() and file.match(pattern) and file.resolve() != output_abs
    )
    return files


def merge_files(input_files: list[Path], output_path: Path) -> None:
    has_existing_content = output_path.exists() and output_path.stat().st_size > 0
    mode = "a" if has_existing_content else "w"

    with output_path.open(mode, encoding="utf-8", newline="\n") as out:
        if has_existing_content:
            with output_path.open("rb") as current:
                current.seek(-1, 2)
                if current.read(1) != b"\n":
                    out.write("\n")
            out.write("\n")
        else:
            out.write("// Export GIFT combinado\n")
            out.write("// Cada bloque mantiene la cabecera y contenido original\n\n")

        for index, input_file in enumerate(input_files, start=1):
            content = input_file.read_text(encoding="utf-8")
            out.write(f"// ===== INICIO: {input_file.name} =====\n")
            out.write(content)
            if not content.endswith("\n"):
                out.write("\n")
            out.write(f"// ===== FIN: {input_file.name} =====\n")
            if index != len(input_files):
                out.write("\n")


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    root = args.root.resolve() if args.root else repo_root
    output_path = args.output.resolve() if args.output else (root / "export_total.gift")

    if not root.is_dir():
        print(f"Error: el directorio no existe: {root}")
        return 1

    input_files = list_input_files(root, args.pattern, output_path)
    if not input_files:
        print(f"No se encontraron ficheros con patron '{args.pattern}' en {root}")
        return 1

    merge_files(input_files, output_path)
    print(f"Creado: {output_path}")
    print(f"Ficheros unidos: {len(input_files)}")
    for file in input_files:
        print(f"- {file.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
