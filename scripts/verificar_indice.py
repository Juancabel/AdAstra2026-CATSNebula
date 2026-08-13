# -*- coding: utf-8 -*-
"""
verificar_indice.py — comprueba el mapeo 1:1 entre index.faiss y metadata.jsonl.

Se corre en un PROCESO NUEVO, a propósito: el objetivo es no confiar en ninguna
estructura en memoria de encode_index.py. Se cargan los dos artefactos desde
disco como los cargaría el evaluador y se comprueba que el ID interno `i` de
FAISS es la línea `i` del metadata.

Un desalineamiento aquí es el bug silencioso de esta fase: el índice recupera
"algo", con puntajes razonables, y devuelve el texto equivocado. No lo detecta
ninguna prueba de recall, solo esta.

Comprobaciones:
  1. index.ntotal == líneas de metadata.jsonl
  2. dimensión == 1024
  3. todos los vectores L2-normalizados (si no, IndexFlatIP no es coseno)
  4. chunk_id únicos
  5. MAPEO: para una muestra aleatoria con semilla fija, re-encodear
     metadata[i]["texto"] y comparar con index.reconstruct(i)
  6. auto-búsqueda: buscar el vector i debe devolver i como primer resultado

Uso:
    python scripts/verificar_indice.py entrega/base_vectorial/encoder_bge-m3
    python scripts/verificar_indice.py salida_muestra --n 50
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from encode_index import (  # noqa: E402
    DIMENSION,
    MODELO,
    cargar_modelo,
    texto_a_encodear,
)

# Coseno mínimo entre el vector guardado y el recalculado. No es 1.0 exacto:
# reconstruir desde el índice y re-encodear en otro proceso mueve los últimos
# bits. Un desalineamiento real da valores muy por debajo de 0.5, no 0.9998.
UMBRAL_COSENO = 0.999


def verificar(dir_indice: Path, n: int = 30, semilla: int = 20260813,
              dispositivo: str | None = None) -> int:
    import faiss
    import numpy as np

    ruta_index = dir_indice / "index.faiss"
    ruta_meta = dir_indice / "metadata.jsonl"
    manifiesto = dir_indice / "manifiesto.json"

    for p in (ruta_index, ruta_meta):
        if not p.exists():
            print(f"FALLO: no existe {p}")
            return 1

    con_contexto = False
    revision = None
    if manifiesto.exists():
        m = json.loads(manifiesto.read_text(encoding="utf-8"))
        con_contexto = m.get("con_contexto", False)
        revision = m.get("revision")
        if m.get("modelo") != MODELO:
            print(f"FALLO: el manifiesto dice modelo {m.get('modelo')!r}, "
                  f"este verificador usa {MODELO!r}")
            return 1

    index = faiss.read_index(str(ruta_index))
    metadata = [json.loads(l) for l in ruta_meta.open(encoding="utf-8") if l.strip()]

    fallos = []

    # 1-2. Conteo y dimensión
    if index.ntotal != len(metadata):
        fallos.append(f"index.ntotal={index.ntotal} != {len(metadata)} líneas de metadata")
    if index.d != DIMENSION:
        fallos.append(f"dimensión {index.d} != {DIMENSION}")
    print(f"[1] vectores={index.ntotal}  metadata={len(metadata)}  dim={index.d}")

    if index.ntotal == 0:
        print("FALLO: índice vacío")
        return 1

    # 3. Normas
    todos = index.reconstruct_n(0, index.ntotal)
    normas = np.linalg.norm(todos, axis=1)
    if not np.allclose(normas, 1.0, atol=1e-3):
        fallos.append(f"vectores sin normalizar: [{normas.min():.5f}, {normas.max():.5f}]")
    print(f"[2] normas L2 en [{normas.min():.6f}, {normas.max():.6f}]")

    # 4. chunk_id únicos
    ids = [m["chunk_id"] for m in metadata]
    if len(set(ids)) != len(ids):
        fallos.append(f"{len(ids) - len(set(ids))} chunk_id duplicados en metadata")
    print(f"[3] chunk_id únicos: {len(set(ids))}/{len(ids)}")

    # 5. Mapeo 1:1 — re-encodear y comparar
    rng = random.Random(semilla)
    posiciones = sorted(rng.sample(range(index.ntotal), min(n, index.ntotal)))
    print(f"[4] re-encodeando {len(posiciones)} chunks para comprobar el mapeo …")
    modelo = cargar_modelo(dispositivo, revision)
    textos = [texto_a_encodear(metadata[i], con_contexto) for i in posiciones]
    recalculados = modelo.encode(textos, batch_size=8, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False)

    peor = 1.0
    for i, vector_nuevo in zip(posiciones, recalculados):
        guardado = index.reconstruct(i)
        coseno = float(np.dot(guardado, vector_nuevo))
        peor = min(peor, coseno)
        if coseno < UMBRAL_COSENO:
            fallos.append(f"posición {i} ({metadata[i]['chunk_id']}): coseno {coseno:.4f}")
    print(f"    peor coseno: {peor:.6f}  (umbral {UMBRAL_COSENO})")

    # 6. Auto-búsqueda: el vecino más cercano de un vector es él mismo.
    consulta = np.ascontiguousarray(todos[posiciones], dtype="float32")
    _, vecinos = index.search(consulta, 1)
    malos = [(int(p), int(v)) for p, v in zip(posiciones, vecinos[:, 0]) if int(v) != int(p)]
    if malos:
        fallos.append(f"auto-búsqueda: {len(malos)} vectores no se recuperan a sí mismos "
                      f"(p.ej. {malos[:3]})")
    print(f"[5] auto-búsqueda: {len(posiciones) - len(malos)}/{len(posiciones)} correctas")

    if fallos:
        print(f"\nFALLA ({len(fallos)}):")
        for f in fallos[:20]:
            print(f"  - {f}")
        return 1
    print("\nOK: mapeo 1:1 verificado, índice utilizable.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Verifica el índice FAISS y su metadata")
    ap.add_argument("dir_indice", type=Path)
    ap.add_argument("--n", type=int, default=30, help="cuántos chunks re-encodear")
    ap.add_argument("--dispositivo", default=None)
    args = ap.parse_args()
    raise SystemExit(verificar(args.dir_indice, args.n, dispositivo=args.dispositivo))
