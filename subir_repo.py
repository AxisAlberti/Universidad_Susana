#!/usr/bin/env python3
import subprocess
import sys


def ejecutar(comando):
    resultado = subprocess.run(comando, check=False, text=True, capture_output=True)
    if resultado.returncode != 0:
        if resultado.stderr:
            print(resultado.stderr.strip())
        else:
            print(resultado.stdout.strip())
        sys.exit(resultado.returncode)
    return resultado.stdout.strip()


def normalizar_mensaje(mensaje):
    mensaje = " ".join(mensaje.strip().split())
    return mensaje.replace(" ", "_")


def main():
    if len(sys.argv) < 2:
        print("Uso: python subir_repo.py \"mensaje_commit\"")
        sys.exit(1)

    mensaje = normalizar_mensaje(" ".join(sys.argv[1:]))
    if not mensaje:
        print("Error: el mensaje de commit no puede estar vacio.")
        sys.exit(1)

    ejecutar(["git", "add", "."])
    ejecutar(["git", "commit", "-m", mensaje])
    ejecutar(["git", "push"])
    print(f"OK: commit '{mensaje}' enviado a remoto.")


if __name__ == "__main__":
    main()
