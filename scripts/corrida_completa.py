"""
corrida_completa.py — Los cuatro pasos de la corrida, encadenados.

    1. Ingesta con OCR            -> data/documents.jsonl   + sha256
    2. Ingesta con OCR (2ª vez)   -> archivo temporal       + sha256
    3. detectar_idioma.py sobre el resultado del paso 1
    4. Las 9 validaciones de la Fase 4

Todo sale por pantalla EN VIVO y se guarda además en data/corrida_<marca>.log.

POR QUÉ ESTO ES PYTHON Y NO POWERSHELL
El primer intento repartía la salida con `Tee-Object`, y en Windows eso no
funciona de forma fiable por dos motivos, ambos verificados a mano:

  - Codificación. Cuando Python escribe a una CONSOLA usa la API Unicode de
    Windows y los acentos salen bien aunque la consola esté en cp850. Cuando
    escribe a una TUBERÍA cae a la codificación local, y PowerShell la decodifica
    con otra distinta: de ahí el mojibake ("Índice" -> "═ndice", "mín" -> "mÝn").
    Ninguna combinación de chcp y PYTHONIOENCODING lo arregla en los dos sentidos
    a la vez, porque PowerShell decide la decodificación por su cuenta.
    `Start-Transcript` tampoco sirve: en PS 5.1 no captura la salida de
    ejecutables nativos.

  - stderr. Con $ErrorActionPreference = 'Stop', CUALQUIER línea que un .exe
    escriba en stderr se convierte en NativeCommandError y aborta el script.
    PyMuPDF emite un aviso de deprecación nada más importarse, así que la
    corrida moría al primer PDF sin que hubiera fallado nada.

Aquí los dos extremos de la tubería son Python: se fija la codificación del
hijo y la del lector, y stderr es solo texto más.

Uso:
    python scripts/corrida_completa.py
    python scripts/corrida_completa.py --saltar-segunda-corrida
"""

import argparse
import hashlib
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


class Salida:
    """Escribe a la vez por pantalla y al log, ambos en UTF-8."""

    def __init__(self, ruta_log: Path):
        self.log = ruta_log.open("w", encoding="utf-8", newline="\n")

    def __call__(self, texto: str = "") -> None:
        print(texto, flush=True)
        self.log.write(texto + "\n")
        self.log.flush()   # flush en cada línea: si se corta, no se pierde nada

    def cerrar(self) -> None:
        self.log.close()


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def paso(escribir: Salida, titulo: str, argumentos: list[str]) -> int:
    """Lanza un script del repo volcando su salida línea a línea."""
    escribir("")
    escribir("=" * 72)
    escribir(f"{titulo}    [{datetime.now():%H:%M:%S}]")
    escribir("=" * 72)

    entorno = dict(os.environ)
    # El hijo escribe a una tubería: hay que decirle explícitamente en qué
    # codificación, porque si no usa la local y se pierden los acentos.
    entorno["PYTHONIOENCODING"] = "utf-8"

    t0 = time.time()
    proceso = subprocess.Popen(
        [PYTHON, "-u"] + argumentos,
        cwd=RAIZ,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,   # stderr es texto más, no un error
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    for linea in proceso.stdout:
        escribir(linea.rstrip("\n"))
    proceso.wait()

    escribir(f"[terminado en {(time.time() - t0) / 60:.1f} min, "
             f"código {proceso.returncode}]")
    return proceso.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--saltar-segunda-corrida", action="store_true",
                    help="omite el paso 2; deja el criterio 9 sin comprobar")
    args = ap.parse_args()

    salida = RAIZ / "data" / "documents.jsonl"
    salida2 = Path(os.environ.get("TEMP", "/tmp")) / "documents_run2.jsonl"
    temporal_lang = Path(os.environ.get("TEMP", "/tmp")) / "documents_lang.jsonl"

    (RAIZ / "data").mkdir(parents=True, exist_ok=True)
    ruta_log = RAIZ / "data" / f"corrida_{datetime.now():%Y%m%d-%H%M%S}.log"
    escribir = Salida(ruta_log)
    reloj = time.time()

    try:
        escribir(f"Corrida completa — {datetime.now():%Y-%m-%d %H:%M:%S}")
        escribir(f"repo   : {RAIZ}")
        escribir(f"python : {PYTHON}")
        escribir(f"log    : {ruta_log}")

        # --- Paso 1 ---
        if paso(escribir, "PASO 1/4 — Ingesta con OCR",
                ["src/ingest_data.py", "corpus_original", str(salida)]):
            escribir("\nPASO 1 FALLÓ. Se detiene aquí.")
            return 1
        sha_1 = sha256(salida)
        escribir(f"\nsha256 corrida 1 (antes de lang): {sha_1}")

        # --- Paso 2 ---
        sha_2 = None
        if args.saltar_segunda_corrida:
            escribir("")
            escribir("PASO 2/4 — OMITIDO. El criterio 9 queda SIN comprobar.")
        else:
            if paso(escribir, "PASO 2/4 — Segunda ingesta (criterio 9)",
                    ["src/ingest_data.py", "corpus_original", str(salida2)]):
                escribir("\nPASO 2 FALLÓ. Se detiene aquí.")
                return 1
            sha_2 = sha256(salida2)
            escribir(f"\nsha256 corrida 2 (antes de lang): {sha_2}")
            escribir(f"CRITERIO 9: "
                     f"{'PASA — byte-idénticas' if sha_1 == sha_2 else 'FALLA — DIFIEREN'}")

        # --- Paso 3 ---
        if paso(escribir, "PASO 3/4 — Detección de idioma",
                ["detectar_idioma.py", str(salida), str(temporal_lang),
                 "--reporte", "data/reporte_idioma.json"]):
            escribir("\nPASO 3 FALLÓ. documents.jsonl queda sin el campo lang.")
            return 1
        os.replace(temporal_lang, salida)
        sha_final = sha256(salida)
        escribir(f"\nsha256 final (con lang): {sha_final}")

        # --- Paso 4 ---
        codigo_validacion = paso(escribir, "PASO 4/4 — Validaciones de la Fase 4",
                                 ["scripts/validar_fase4.py", str(salida)])

        # --- Resumen ---
        escribir("")
        escribir("=" * 72)
        escribir("RESUMEN")
        escribir("=" * 72)
        escribir(f"duración total          : {(time.time() - reloj) / 60:.1f} min")
        escribir(f"sha256 corrida 1        : {sha_1}")
        if sha_2 is not None:
            escribir(f"sha256 corrida 2        : {sha_2}")
            escribir(f"criterio 9 determinismo : "
                     f"{'PASA' if sha_1 == sha_2 else 'FALLA'}")
        else:
            escribir("criterio 9 determinismo : sin comprobar")
        escribir(f"sha256 final (con lang) : {sha_final}")
        escribir(f"validaciones 1-8        : "
                 f"{'todas PASAN' if codigo_validacion == 0 else 'hay FALLOS, ver detalle arriba'}")
        escribir(f"salida                  : {salida}")
        escribir(f"log                     : {ruta_log}")
        return codigo_validacion
    finally:
        escribir.cerrar()


if __name__ == "__main__":
    sys.exit(main())
