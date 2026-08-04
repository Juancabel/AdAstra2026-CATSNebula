"""
inventory_corpus.py — Inventario del corpus (tarea del Día 1 de B).

Recorre el corpus descargado y reporta:
  - archivos por formato
  - archivos por fenómeno
  - archivos que no se pueden clasificar (para revisar a mano)
  - rutas paginadas (page_NN) y tiles de mapa detectados, con conteo

Uso:
    python inventory_corpus.py /ruta/al/corpus
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from corpus_paths import (
    infer_fenomeno,
    infer_formato,
    looks_like_map_tile,
    looks_like_paginated_report,
)


def inventariar(raiz: str) -> None:
    raiz = Path(raiz)
    archivos = [p for p in raiz.rglob("*") if p.is_file()]

    por_formato = Counter()
    por_fenomeno = Counter()
    por_fenomeno_y_formato = Counter()
    sin_fenomeno = []
    sin_formato = []
    paginados = Counter()  # carpeta de reporte -> num de páginas encontradas
    tiles = 0

    for p in archivos:
        ruta_rel = str(p.relative_to(raiz))

        try:
            fenomeno = infer_fenomeno(ruta_rel)
        except ValueError:
            sin_fenomeno.append(ruta_rel)
            fenomeno = None

        try:
            formato = infer_formato(ruta_rel)
        except ValueError:
            sin_formato.append(ruta_rel)
            formato = None

        if formato:
            por_formato[formato] += 1
        if fenomeno:
            por_fenomeno[fenomeno] += 1
        if fenomeno and formato:
            por_fenomeno_y_formato[(fenomeno, formato)] += 1

        if looks_like_paginated_report(ruta_rel):
            # agrupar por la carpeta padre del "page_NN" para contar páginas por reporte
            partes = ruta_rel.replace("\\", "/").split("/")
            for i, seg in enumerate(partes):
                if seg.lower().startswith(("page_", "pagina_")):
                    carpeta_reporte = "/".join(partes[:i])
                    paginados[carpeta_reporte] += 1
                    break

        if looks_like_map_tile(ruta_rel):
            tiles += 1

    print(f"{'='*60}")
    print(f"INVENTARIO DEL CORPUS — {raiz}")
    print(f"{'='*60}")
    print(f"\nTotal de archivos: {len(archivos)}\n")

    print("Por formato:")
    for formato, n in por_formato.most_common():
        print(f"  {formato:<10} {n:>5}")

    print("\nPor fenómeno:")
    for fenomeno, n in sorted(por_fenomeno.items()):
        print(f"  F{fenomeno}  {n:>5}")

    print("\nPor fenómeno x formato:")
    for (fenomeno, formato), n in sorted(por_fenomeno_y_formato.items()):
        print(f"  F{fenomeno} / {formato:<10} {n:>5}")

    if paginados:
        print(f"\nReportes paginados detectados ({len(paginados)} reportes):")
        for carpeta, n_paginas in sorted(paginados.items()):
            print(f"  {carpeta}  ({n_paginas} páginas)")
        print(
            "  -> DECISIÓN PENDIENTE DEL EQUIPO: concatenar en un solo\n"
            "     documento por reporte, o tratar cada página como\n"
            "     documento independiente. Ver discusión del equipo."
        )

    if tiles:
        print(f"\nTiles de mapa detectados: {tiles}")
        print(
            "  -> Recordar: el mismo elemento se repite en cada nivel de\n"
            "     zoom (spec §2.1). Deduplicar antes de indexar."
        )

    if sin_fenomeno:
        print(f"\n[ALERTA] {len(sin_fenomeno)} archivos sin fenómeno detectable:")
        for r in sin_fenomeno[:10]:
            print(f"  {r}")
        if len(sin_fenomeno) > 10:
            print(f"  ... y {len(sin_fenomeno) - 10} más")

    if sin_formato:
        print(f"\n[ALERTA] {len(sin_formato)} archivos con extensión no reconocida:")
        for r in sin_formato[:10]:
            print(f"  {r}")
        if len(sin_formato) > 10:
            print(f"  ... y {len(sin_formato) - 10} más")

    if not sin_fenomeno and not sin_formato:
        print("\nTodos los archivos se clasificaron correctamente.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python inventory_corpus.py /ruta/al/corpus")
        sys.exit(1)
    inventariar(sys.argv[1])
