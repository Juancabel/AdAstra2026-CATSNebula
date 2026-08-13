"""
validar_fase4.py — Las 9 validaciones de aceptación del documents.jsonl.

Ninguna se da por buena razonando: todas se comprueban contra el índice oficial
y contra el archivo real. Devuelve código de salida 1 si alguna falla, para que
sirva en un pipeline.

Uso:
    python scripts/validar_fase4.py [data/documents.jsonl]

El criterio 9 (determinismo) no se puede comprobar desde aquí con un solo
archivo: hace falta una segunda corrida. Este script imprime el sha256 para
compararlo; scripts/corrida_completa.ps1 encadena las dos ejecuciones.
"""

import collections
import hashlib
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from indice_oficial import cargar_indice_oficial

SALIDA = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "data" / "documents.jsonl"
XLSX = RAIZ / "corpus_original" / "Indice_Datos_Codefest.xlsx"

# Vocabulario de `formato` que fijó ADL: la extensión real en minúsculas.
FORMATOS = {"json", "pdf", "pbf", "csv", "jpg", "xlsx", "avif", "txt"}

# Reparto esperado, tomado del inventario oficial.
ESPERADO_POR_FORMATO = {
    "json": 954, "pdf": 759, "pbf": 73, "csv": 26,
    "jpg": 8, "xlsx": 4, "avif": 1, "txt": 1,
}

indice = cargar_indice_oficial(XLSX)
docids_oficiales = {v["doc_id_oficial"] for v in indice.values()}
filas = [json.loads(l) for l in SALIDA.open(encoding="utf-8") if l.strip()]

resultados = []


def check(n, titulo, condicion, detalle=""):
    resultados.append(condicion)
    print(f"  {n}. [{'PASA' if condicion else 'FALLA'}] {titulo}")
    for linea in str(detalle).splitlines():
        if linea:
            print(f"         {linea}")


print("=" * 72)
print(f"FASE 4 — VALIDACIONES DE ACEPTACIÓN")
print(f"archivo: {SALIDA}")
print("=" * 72)

# 1 — doc_id únicos
cuenta = collections.Counter(r["doc_id"] for r in filas)
duplicados = sorted(d for d, n in cuenta.items() if n > 1)
check(1, "doc_id únicos", not duplicados,
      f"{len(filas)} documentos, {len(cuenta)} doc_id distintos"
      + (f"\nduplicados: {duplicados[:5]}" if duplicados else ""))

# 2 — todo doc_id existe en el índice oficial
huerfanos = sorted({r["doc_id"] for r in filas} - docids_oficiales)
check(2, "todo doc_id existe en el índice oficial", not huerfanos,
      f"{len(filas) - len(huerfanos)}/{len(filas)}"
      + (f"\nfuera del índice: {huerfanos[:5]}" if huerfanos else " = 100%"))

# 3 — toda `fuente` casa con una fila del índice
sin_corresp = sorted(r["fuente"] for r in filas if r["fuente"] not in indice)
check(3, "fuente sin correspondencia en el índice", not sin_corresp,
      f"0 de {len(filas)}" if not sin_corresp
      else f"{len(sin_corresp)}:\n" + "\n".join(sin_corresp[:5]))

# 4 — total de documentos
check(4, "total de documentos == inventario", len(filas) == len(indice),
      f"{len(filas)} emitidos / {len(indice)} en el inventario")

# 5 — vocabulario de formato
usados = sorted({r["formato"] for r in filas})
check(5, "formato dentro del vocabulario del reto", not (set(usados) - FORMATOS),
      f"valores usados: {usados}")

# 6 — fenomeno entero en {1,2,3}
malos = [r["fuente"] for r in filas
         if isinstance(r["fenomeno"], bool)
         or not isinstance(r["fenomeno"], int)
         or r["fenomeno"] not in (1, 2, 3)]
check(6, "fenomeno entero en {1,2,3}", not malos,
      f"reparto: {dict(sorted(collections.Counter(r['fenomeno'] for r in filas).items()))}"
      + (f"\nincorrectos: {malos[:5]}" if malos else ""))

# 7 — conteo por formato
real = collections.Counter(r["formato"] for r in filas)
difs = {k: (v, real.get(k, 0)) for k, v in ESPERADO_POR_FORMATO.items()
        if real.get(k, 0) != v}
check(7, "conteo por formato", not difs,
      " | ".join(f"{k} {real.get(k, 0)}" for k in sorted(ESPERADO_POR_FORMATO))
      + (f"\nesperado vs real: {difs}" if difs else ""))

# 8 — text no vacío
vacios = sorted((r["formato"], r["fuente"]) for r in filas if not r["text"].strip())
check(8, "text no vacío", not vacios,
      f"{len(vacios)} documentos sin texto: "
      f"{dict(sorted(collections.Counter(f for f, _ in vacios).items()))}")

# 9 — determinismo: requiere una segunda corrida
sha = hashlib.sha256(SALIDA.read_bytes()).hexdigest()
print(f"  9. [ -- ] determinismo: comparar este sha256 con el de otra corrida")
print(f"         {sha}")

print()
print(f"  {sum(resultados)}/{len(resultados)} validaciones automáticas superadas")

if vacios:
    print("\n" + "=" * 72)
    print("DOCUMENTOS SIN TEXTO (detalle del criterio 8)")
    print("Se emiten igual, con su DOC_ID y su metadata: los organizadores")
    print("pidieron conservarla sin inventar contenido.")
    print("=" * 72)
    for formato, fuente in vacios:
        print(f"  [{formato}] {fuente}")

sys.exit(0 if all(resultados) else 1)
