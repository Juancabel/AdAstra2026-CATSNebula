"""
medir_encabezados_supervivientes.py — ¿Cuánto pesa la doble serialización?

EL PROBLEMA QUE MIDE
PyMuPDF no siempre parte el texto igual en todas las páginas. En
AIINDEX_ai-index-2024-ch1, el mismo encabezado visual sale como UNA línea
("Chapter 1: Research and Development") en 27 de 51 páginas, y partido en TRES
("CHAPTER 1:" / "Research and" / "Development") en otras 4. La variante mayoritaria
supera el umbral del 30% y se elimina; la minoritaria se queda en el 7,8% y
sobrevive.

Bajar el umbral hasta el 8% no es opción: arrastraría contenido real. La
pregunta es si el residuo afecta a un puñado de documentos —y entonces es
ruido— o a cientos, y entonces vale la pena cambiar de estrategia (evaluar el
bloque completo de cabecera como una sola candidata, en vez de línea a línea).

CÓMO LO DETECTA
Para cada PDF, una forma normalizada "sobrevive por doble serialización" si:
  - aparece en >=2 páginas pero por debajo del umbral (no se elimina), y
  - es subcadena de alguna forma que SÍ se eliminó (>=30%), y
  - tiene al menos 2 palabras (para no contar fragmentos triviales).

Uso:
    python scripts/medir_encabezados_supervivientes.py
    python scripts/medir_encabezados_supervivientes.py --limite 100
"""

import argparse
import collections
import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from extractores_datos import (  # noqa: E402
    UMBRAL_REPETICION_PAGINA,
    _es_numeracion,
    _indices_ventana,
    _normalizar_linea,
)

MIN_PALABRAS_RESIDUO = 2


def analizar(ruta_pdf: Path) -> list[tuple[str, int, str]]:
    """Devuelve [(forma_superviviente, n_paginas, forma_padre_eliminada)]."""
    import fitz

    doc = fitz.open(ruta_pdf.as_posix())
    try:
        paginas = [p.get_text("text") for p in doc]
    finally:
        doc.close()

    por_pagina = [[l.strip() for l in pg.splitlines() if l.strip()] for pg in paginas]
    # Mismo preproceso que limpiar_boilerplate_paginas: numeración primero.
    sin_numeracion = []
    for lineas in por_pagina:
        ventana = _indices_ventana(len(lineas))
        sin_numeracion.append(
            [l for i, l in enumerate(lineas)
             if not (i in ventana and _es_numeracion(l))]
        )

    conteo = collections.Counter()
    for lineas in sin_numeracion:
        if not lineas:
            continue
        for forma in {_normalizar_linea(lineas[i]) for i in _indices_ventana(len(lineas))}:
            if forma:
                conteo[forma] += 1

    n_paginas = sum(1 for p in sin_numeracion if p)
    if n_paginas < 2:
        return []
    minimo = max(2, int(n_paginas * UMBRAL_REPETICION_PAGINA))

    eliminadas = [f for f, c in conteo.items() if c >= minimo]
    supervivientes = []
    for forma, c in conteo.items():
        if c >= minimo or c < 2:
            continue
        if len(forma.split()) < MIN_PALABRAS_RESIDUO:
            continue
        for padre in eliminadas:
            if forma != padre and forma.lower() in padre.lower():
                supervivientes.append((forma, c, padre))
                break
    return supervivientes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--documentos", type=Path,
                    default=RAIZ / "data" / "documents.jsonl")
    ap.add_argument("--limite", type=int, default=None,
                    help="analizar solo los primeros N PDF (para probar)")
    args = ap.parse_args()

    pdfs = []
    with args.documentos.open(encoding="utf-8") as f:
        for linea in f:
            r = json.loads(linea)
            if r["formato"] == "pdf":
                pdfs.append((r["doc_id"], r["fuente"], r["nombre_archivo"]))
    if args.limite:
        pdfs = pdfs[:args.limite]

    print(f"PDF a analizar: {len(pdfs)}", flush=True)
    t0 = time.time()
    afectados = []
    errores = 0
    for i, (doc_id, fuente, nombre) in enumerate(pdfs, 1):
        try:
            residuos = analizar(RAIZ / "corpus_original" / fuente)
        except Exception:  # noqa: BLE001
            errores += 1
            continue
        if residuos:
            afectados.append((doc_id, nombre, residuos))
        if i % 150 == 0:
            print(f"  {i}/{len(pdfs)} — {time.time() - t0:.0f}s", flush=True)

    print("\n" + "=" * 70)
    print("ENCABEZADOS QUE SOBREVIVEN POR DOBLE SERIALIZACIÓN")
    print("=" * 70)
    print(f"PDF analizados            : {len(pdfs) - errores}")
    print(f"PDF con algún residuo     : {len(afectados)} "
          f"({len(afectados) / max(len(pdfs) - errores, 1) * 100:.1f}%)")
    print(f"líneas residuales totales : {sum(len(r) for _, _, r in afectados)}")
    if errores:
        print(f"PDF que no se pudieron abrir: {errores}")

    afectados.sort(key=lambda x: -sum(c for _, c, _ in x[2]))
    print("\nLos 15 más afectados (páginas con residuo):")
    for doc_id, nombre, residuos in afectados[:15]:
        total = sum(c for _, c, _ in residuos)
        print(f"  {total:>5} págs  {doc_id:<16} {nombre[:44]}")
        for forma, c, padre in residuos[:3]:
            print(f"          {c:>4}x {forma[:40]!r}  <- de {padre[:34]!r}")


if __name__ == "__main__":
    main()
