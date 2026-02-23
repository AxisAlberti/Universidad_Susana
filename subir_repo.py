#!/usr/bin/env python3
import subprocess
import sys


def ejecutar(comando):
    return subprocess.run(comando, check=False, text=True, capture_output=True)


def salida_error(resultado):
    texto = (resultado.stderr or "").strip()
    if not texto:
        texto = (resultado.stdout or "").strip()
    return texto


def ejecutar_obligatorio(comando, contexto):
    resultado = ejecutar(comando)
    if resultado.returncode != 0:
        print(f"Error en {contexto}:")
        print(salida_error(resultado))
        sys.exit(resultado.returncode)
    return (resultado.stdout or "").strip()


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

    ejecutar_obligatorio(["git", "add", "."], "git add")

    commit_result = ejecutar(["git", "commit", "-m", mensaje])
    if commit_result.returncode != 0:
        error_commit = salida_error(commit_result)
        if "nothing to commit" in error_commit.lower():
            print("No hay cambios para confirmar. Intentando push por si hay commits pendientes...")
        else:
            print("Error en git commit:")
            print(error_commit)
            sys.exit(commit_result.returncode)

    push_result = ejecutar(["git", "push"])
    if push_result.returncode == 0:
        print(f"OK: commit '{mensaje}' enviado a remoto.")
        return

    error_push = salida_error(push_result)
    fetch_first = (
        "fetch first" in error_push.lower()
        or "failed to push some refs" in error_push.lower()
        or "non-fast-forward" in error_push.lower()
    )

    if not fetch_first:
        print("Error en git push:")
        print(error_push)
        sys.exit(push_result.returncode)

    print("Aviso: el remoto tiene cambios nuevos. Aplicando metodo alternativo (pull --rebase + push)...")
    pull_result = ejecutar(["git", "pull", "--rebase", "--autostash"])
    if pull_result.returncode != 0:
        error_pull = salida_error(pull_result)
        print("Error en git pull --rebase --autostash:")
        print(error_pull)
        print("No se pudo completar la subida automaticamente. Revisa conflictos y vuelve a ejecutar.")
        sys.exit(pull_result.returncode)

    retry_push = ejecutar(["git", "push"])
    if retry_push.returncode != 0:
        print("Error en segundo intento de git push:")
        print(salida_error(retry_push))
        sys.exit(retry_push.returncode)

    print(f"OK: commit '{mensaje}' enviado a remoto.")


if __name__ == "__main__":
    main()
