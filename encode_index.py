# -*- coding: utf-8 -*-
"""
encode_index.py — chunks.jsonl -> index.faiss + metadata.jsonl. Fase de embeddings.

Entrada : data/chunks.jsonl (Contrato 2, salida de chunk.py 1.2.0)
Salida  : entrega/base_vectorial/encoder_bge-m3/{index.faiss, metadata.jsonl}
          + manifiesto.json (no lo pide el spec; es la trazabilidad de la corrida)

DECISIONES FIJADAS (ver brief de la fase; no reabrir sin medir)
---------------------------------------------------------------
* Encoder BAAI/bge-m3, modo dense. MIT, 1024-dim, contexto 8192 (ningún chunk
  del corpus se trunca: el más largo son ~1.800 palabras), y el corpus tiene
  15 idiomas detectados, no 3.
* IndexFlatIP sobre vectores L2-normalizados == coseno. Búsqueda exacta.
  86.046 x 1024 x 4 B = 336 MB: sub-segundo en CPU, que es donde evalúan.
  IVF/HNSW no aportan a este tamaño y SÍ introducen no-determinismo (orden de
  inserción, semillas de clustering). Eso es criterio de eliminación.
* Sin prefijos query:/passage:. BGE-M3 dense es simétrico. (Si algún día se
  suma multilingual-e5-large como segundo encoder, ESE sí los necesita.)

MAPEO 1:1 — EL REQUISITO 6
--------------------------
El ID interno de FAISS `i` tiene que ser la línea `i` de metadata.jsonl. FAISS
asigna los IDs por orden de `add()`, así que la única garantía real es emitir
vector y metadato en el mismo bucle, en un orden explícito y estable.

El orden es `(doc_id, posicion)`, NO el string `chunk_id`: hay documentos con
3.826 chunks, así que conviven sufijos `_c999` y `_c1000` y el orden
lexicográfico los intercala mal. Tampoco se usa el orden del archivo: si
alguien regenera chunks.jsonl con otro orden de lectura, el índice cambiaría
sin que nadie lo note.

Verificación en proceso nuevo: scripts/verificar_indice.py.

DETERMINISMO
------------
Para uso interno (poder reconstruir la corrida), no para la evaluación: los
organizadores confirmaron que no reconstruyen el índice, cargan el nuestro.
  * model.eval() y sin gradientes.
  * fp32 puro. Nada de fp16/bf16 ni TF32: cambian resultados entre CPU y GPU y
    entre GPUs distintas.
  * Orden de chunks explícito (arriba).
  * batch_size CONSTANTE: la composición del batch determina el padding, y el
    padding cambia el agrupamiento de las operaciones de punto flotante. Al
    reanudar se exige el mismo batch_size y se retrocede al múltiplo de batch
    anterior, para no partir un batch por la mitad.
  * El manifiesto guarda revisión exacta del modelo en HuggingFace + versiones
    de torch/transformers/sentence-transformers/faiss/numpy + el dispositivo.

num_tokens
----------
chunks.jsonl lo trae ESTIMADO (palabras x 1.9). Aquí se recalcula con el
tokenizador real de BGE-M3, que ya está cargado para encodear, y se sobrescribe
al escribir metadata.jsonl. La estimación no sobrevive a esta fase.

catalogo_masivo
---------------
Se añade a metadata.jsonl como bandera booleana con el mismo criterio que el
reporte de chunk.py (formato de registro y num_words > MAXIMO_PALABRAS): 8.750
chunks. Se encodean como cualquier otro; la bandera existe para que D detecte
sin recontar palabras cuáles necesitan subdividir_para_salida() al responder.

USO
---
    # 1. muestra de validación (§4 del brief): mide y proyecta, no toca la entrega
    python encode_index.py data/chunks.jsonl salida_muestra --muestra 500

    # 2. corrida completa
    python encode_index.py data/chunks.jsonl entrega/base_vectorial/encoder_bge-m3

    # 3. si se corta, se reanuda desde el último batch completo
    python encode_index.py data/chunks.jsonl entrega/base_vectorial/encoder_bge-m3 --reanudar
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

# --- Configuración fijada ---------------------------------------------------

MODELO = "BAAI/bge-m3"
DIMENSION = 1024

# Contexto máximo del modelo. Explícito y no heredado de la config del repo de
# HF: si upstream lo cambiara, el truncado cambiaría en silencio.
MAX_SEQ_LENGTH = 8192

# Constante, no derivada del hardware disponible (ver DETERMINISMO). 16 entra
# holgado en CPU incluso con los chunks de catálogo (~3.400 tokens).
BATCH_SIZE = 16

# Revisión exacta del modelo en HuggingFace, resuelta el 2026-08-13. El nombre
# del modelo no basta como pin: HuggingFace puede mover el `main` del repo y los
# pesos cambiarían sin que cambie ninguna versión de requirements.txt.
REVISION: str | None = "5617a9f61b028005a4858fdac845db406aefb181"

# Mismo criterio que el reporte de chunk.py — mantener sincronizado con
# chunk.FORMATOS_REGISTRO y chunk.MAXIMO_PALABRAS.
FORMATOS_REGISTRO = frozenset({"pbf", "xlsx", "csv"})
MAXIMO_PALABRAS = 250

# Cada cuántos batches se vuelca el parcial a disco (para poder reanudar).
CADA_N_BATCHES = 50


# ---------------------------------------------------------------------------
# Lectura ordenada de chunks
# ---------------------------------------------------------------------------

def indice_ordenado(ruta: Path) -> list[tuple[str, int, int]]:
    """
    Primera pasada: (doc_id, posicion, offset_en_bytes) de cada línea, ordenado.

    Se guardan offsets en vez de los objetos enteros porque chunks.jsonl pesa
    234 MB y materializarlo entero como dicts de Python multiplica esa cifra.
    """
    entradas = []
    with ruta.open("rb") as f:
        offset = 0
        for linea in f:
            largo = len(linea)
            if linea.strip():
                obj = json.loads(linea.decode("utf-8"))
                entradas.append((obj["doc_id"], obj["posicion"], offset))
            offset += largo
    entradas.sort(key=lambda e: (e[0], e[1]))
    return entradas


def leer_en_offset(f, offset: int) -> dict:
    f.seek(offset)
    return json.loads(f.readline().decode("utf-8"))


def muestrear_estratificado(entradas, chunks_ruta: Path, n: int, semilla: int = 20260813):
    """Submuestra ~n entradas repartidas proporcionalmente por fenómeno."""
    por_fenomeno: dict[object, list] = {}
    with chunks_ruta.open("rb") as f:
        for e in entradas:
            fen = leer_en_offset(f, e[2]).get("fenomeno")
            por_fenomeno.setdefault(fen, []).append(e)

    rng = random.Random(semilla)
    elegidas = []
    total = len(entradas)
    for fen in sorted(por_fenomeno, key=str):
        grupo = por_fenomeno[fen]
        cuota = max(1, round(n * len(grupo) / total))
        elegidas.extend(rng.sample(grupo, min(cuota, len(grupo))))
    elegidas.sort(key=lambda e: (e[0], e[1]))
    return elegidas


def huella_orden(entradas) -> str:
    """sha256 del orden exacto. Detecta que chunks.jsonl cambió al reanudar."""
    h = hashlib.sha256()
    for doc_id, posicion, _ in entradas:
        h.update(f"{doc_id}\x1f{posicion}\n".encode("utf-8"))
    return h.hexdigest()


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

def cargar_modelo(dispositivo: str | None, revision: str | None):
    """Carga BGE-M3 en fp32, en modo evaluación y con TF32 desactivado."""
    import torch
    from sentence_transformers import SentenceTransformer

    # TF32 recorta la mantisa en las matmul de GPUs Ampere+. Silencioso y
    # suficiente para que dos corridas no coincidan.
    if hasattr(torch.backends, "cuda"):
        torch.backends.cuda.matmul.allow_tf32 = False
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.allow_tf32 = False
    torch.manual_seed(0)

    modelo = SentenceTransformer(
        MODELO,
        revision=revision,
        device=dispositivo,
        model_kwargs={"torch_dtype": torch.float32},
    )
    modelo.eval()
    modelo.max_seq_length = MAX_SEQ_LENGTH

    # No basta con pedir fp32 por model_kwargs: el nombre del argumento cambió
    # entre versiones de transformers y una versión que lo ignore cargaría en
    # el dtype por defecto sin decir nada. Se comprueba sobre los pesos reales.
    dtypes = {p.dtype for p in modelo.parameters()}
    if dtypes != {torch.float32}:
        raise SystemExit(f"El modelo no está en fp32 puro: {dtypes}. "
                         f"fp16/bf16 dan resultados distintos entre CPU y GPU.")
    return modelo


def resolver_revision(revision: str | None) -> str | None:
    """Commit exacto del modelo en HuggingFace, para el manifiesto."""
    if revision:
        return revision
    try:
        from huggingface_hub import model_info
        return model_info(MODELO).sha
    except Exception as exc:  # sin red, o hub caído: no es motivo para abortar
        print(f"AVISO: no se pudo resolver la revisión de {MODELO}: {exc}")
        return None


def texto_a_encodear(chunk: dict, con_contexto: bool) -> str:
    """
    El texto que ve el encoder. NO es lo que se guarda en metadata.jsonl.

    Con --con-contexto se antepone el header (título + fenómeno) como sustituto
    compliant de los headers generados por LLM, que el reto prohíbe. Está
    apagado por defecto: puede homogeneizar los vectores de un mismo documento,
    y eso se decide con A/B sobre el dev set, no por intuición.
    """
    if not con_contexto:
        return chunk["texto"]
    partes = [p for p in (chunk.get("contexto") or "").strip().split("\n") if p]
    fen = chunk.get("fenomeno")
    if fen is not None:
        partes.append(f"fenómeno {fen}")
    header = " — ".join(partes)
    return f"{header}\n\n{chunk['texto']}" if header else chunk["texto"]


def es_catalogo_masivo(chunk: dict) -> bool:
    return (chunk.get("formato") in FORMATOS_REGISTRO
            and chunk.get("num_words", 0) > MAXIMO_PALABRAS)


# ---------------------------------------------------------------------------
# Estado parcial (reanudable)
# ---------------------------------------------------------------------------

class Parcial:
    """
    Vectores y metadatos a medio escribir, alineados al batch.

    La corrida completa son ~86k chunks; en CPU eso son horas. Perder todo por
    un corte a mitad no es aceptable, y reanudar a mitad de batch cambiaría la
    composición del batch (y por tanto los vectores), así que se retrocede
    siempre al último múltiplo de batch_size.
    """

    def __init__(self, dir_salida: Path):
        self.dir = dir_salida / ".parcial"
        self.vectores = self.dir / "vectores.f32"
        self.metadata = self.dir / "metadata.jsonl"
        self.estado = self.dir / "estado.json"

    def crear(self, estado: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.vectores.write_bytes(b"")
        self.metadata.write_bytes(b"")
        self.estado.write_text(json.dumps(estado, ensure_ascii=False, indent=2),
                               encoding="utf-8")

    def reanudar(self, estado: dict, batch_size: int) -> int:
        """Valida que la corrida sea la misma y devuelve cuántos vectores valen."""
        if not self.estado.exists():
            raise SystemExit(f"No hay corrida parcial en {self.dir} para reanudar.")
        previo = json.loads(self.estado.read_text(encoding="utf-8"))
        for clave in ("modelo", "revision", "con_contexto", "batch_size",
                      "huella_orden", "dispositivo"):
            if previo.get(clave) != estado.get(clave):
                raise SystemExit(
                    f"No se puede reanudar: '{clave}' cambió "
                    f"({previo.get(clave)!r} -> {estado.get(clave)!r}). "
                    f"Los vectores no serían comparables; borra {self.dir} y "
                    f"vuelve a empezar."
                )

        n_vec = self.vectores.stat().st_size // (DIMENSION * 4)
        n_meta = sum(1 for _ in self.metadata.open("rb"))
        n = (min(n_vec, n_meta) // batch_size) * batch_size

        # Truncar los dos archivos exactamente a n: el corte pudo dejar medio
        # vector o media línea escritos.
        with self.vectores.open("r+b") as f:
            f.truncate(n * DIMENSION * 4)
        offsets = []
        with self.metadata.open("rb") as f:
            pos = 0
            for linea in f:
                offsets.append(pos)
                pos += len(linea)
                if len(offsets) > n:
                    break
        corte = offsets[n] if len(offsets) > n else self.metadata.stat().st_size
        with self.metadata.open("r+b") as f:
            f.truncate(corte)
        return n

    def limpiar(self) -> None:
        for p in (self.vectores, self.metadata, self.estado):
            if p.exists():
                p.unlink()
        if self.dir.exists() and not any(self.dir.iterdir()):
            self.dir.rmdir()


# ---------------------------------------------------------------------------
# Construcción
# ---------------------------------------------------------------------------

def construir(chunks_ruta: Path, dir_salida: Path, *, muestra: int | None = None,
              batch_size: int = BATCH_SIZE, dispositivo: str | None = None,
              con_contexto: bool = False, reanudar: bool = False,
              revision: str | None = REVISION) -> dict:
    import faiss
    import numpy as np

    dir_salida.mkdir(parents=True, exist_ok=True)

    print(f"Leyendo {chunks_ruta} …")
    entradas = indice_ordenado(chunks_ruta)
    print(f"  {len(entradas)} chunks")
    if muestra:
        entradas = muestrear_estratificado(entradas, chunks_ruta, muestra)
        print(f"  muestra estratificada por fenómeno: {len(entradas)} chunks")
    total = len(entradas)

    revision_resuelta = resolver_revision(revision)
    print(f"Cargando {MODELO} (revision={revision_resuelta or 'sin resolver'}) …")
    modelo = cargar_modelo(dispositivo, revision)
    tokenizador = modelo.tokenizer
    disp = str(modelo.device)
    print(f"  dispositivo: {disp}")

    estado = {
        "modelo": MODELO,
        "revision": revision_resuelta,
        "con_contexto": con_contexto,
        "batch_size": batch_size,
        "dispositivo": disp,
        "huella_orden": huella_orden(entradas),
        "total": total,
    }

    parcial = Parcial(dir_salida)
    if reanudar:
        hechos = parcial.reanudar(estado, batch_size)
        print(f"Reanudando desde el chunk {hechos} de {total}.")
    else:
        parcial.crear(estado)
        hechos = 0

    # Estadísticas de num_tokens: la comparación estimado-vs-real es el chequeo
    # (3) del brief. Si difieren en orden de magnitud es un bug, no imprecisión.
    ratios: list[float] = []
    truncados: list[str] = []
    n_catalogo = 0
    t0 = time.perf_counter()

    with chunks_ruta.open("rb") as f_chunks, \
            parcial.vectores.open("ab") as f_vec, \
            parcial.metadata.open("ab") as f_meta:

        for inicio in range(hechos, total, batch_size):
            lote = entradas[inicio:inicio + batch_size]
            chunks = [leer_en_offset(f_chunks, e[2]) for e in lote]
            textos = [texto_a_encodear(c, con_contexto) for c in chunks]

            vectores = modelo.encode(
                textos,
                batch_size=batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ).astype("float32", copy=False)

            for chunk, vector in zip(chunks, vectores):
                ids = tokenizador(chunk["texto"], add_special_tokens=True,
                                  truncation=False)["input_ids"]
                estimado = chunk["num_tokens"]
                real = len(ids)
                if estimado:
                    ratios.append(real / estimado)
                if real > MAX_SEQ_LENGTH:
                    truncados.append(chunk["chunk_id"])

                salida = dict(chunk)
                salida["num_tokens"] = real          # el estimado no sobrevive
                salida["num_tokens_estimado"] = estimado
                salida["catalogo_masivo"] = es_catalogo_masivo(chunk)
                n_catalogo += salida["catalogo_masivo"]

                # Vector y metadato en el mismo bucle, mismo orden: ESTO es el
                # mapeo 1:1 del requisito 6.
                f_vec.write(np.ascontiguousarray(vector, dtype="float32").tobytes())
                f_meta.write((json.dumps(salida, ensure_ascii=False) + "\n").encode("utf-8"))

            lotes_hechos = (inicio - hechos) // batch_size + 1
            if lotes_hechos % CADA_N_BATCHES == 0 or inicio + batch_size >= total:
                f_vec.flush()
                f_meta.flush()
                os.fsync(f_vec.fileno())
                os.fsync(f_meta.fileno())
                hechos_ahora = min(inicio + batch_size, total)
                transcurrido = time.perf_counter() - t0
                por_chunk = transcurrido / max(hechos_ahora - hechos, 1)
                restantes = (total - hechos_ahora) * por_chunk
                print(f"  {hechos_ahora}/{total}  "
                      f"{por_chunk * 1000:.1f} ms/chunk  "
                      f"ETA {restantes / 60:.1f} min", flush=True)

    segundos = time.perf_counter() - t0
    procesados = total - hechos

    # --- Índice ------------------------------------------------------------
    print("Construyendo IndexFlatIP …")
    vectores = np.fromfile(parcial.vectores, dtype="float32").reshape(-1, DIMENSION)
    if len(vectores) != total:
        raise SystemExit(f"Vectores ({len(vectores)}) != chunks ({total}).")

    normas = np.linalg.norm(vectores, axis=1)
    if not np.allclose(normas, 1.0, atol=1e-3):
        raise SystemExit(
            f"Vectores sin normalizar: norma en [{normas.min():.4f}, "
            f"{normas.max():.4f}]. IndexFlatIP dejaría de ser coseno."
        )

    index = faiss.IndexFlatIP(DIMENSION)
    index.add(np.ascontiguousarray(vectores))
    if index.ntotal != total:
        raise SystemExit(f"index.ntotal ({index.ntotal}) != chunks ({total}).")
    faiss.write_index(index, str(dir_salida / "index.faiss"))

    destino_meta = dir_salida / "metadata.jsonl"
    if destino_meta.exists():
        destino_meta.unlink()
    parcial.metadata.replace(destino_meta)

    n_meta = sum(1 for _ in destino_meta.open("rb"))
    if n_meta != total:
        raise SystemExit(f"metadata.jsonl tiene {n_meta} líneas, index {total}.")

    reporte = {
        **estado,
        "dimension": DIMENSION,
        "tipo_index": "IndexFlatIP (coseno sobre vectores L2-normalizados)",
        "max_seq_length": MAX_SEQ_LENGTH,
        "dtype": "float32",
        "muestra": muestra,
        "chunks_entrada": str(chunks_ruta),
        "sha256_chunks_entrada": sha256_archivo(chunks_ruta),
        "sha256_index": sha256_archivo(dir_salida / "index.faiss"),
        "sha256_metadata": sha256_archivo(destino_meta),
        "catalogo_masivo": n_catalogo,
        "chunks_sobre_max_seq_length": truncados,
        "segundos_encodeando": round(segundos, 1),
        "ms_por_chunk": round(segundos * 1000 / max(procesados, 1), 2),
        "versiones": versiones(),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if ratios:
        ratios.sort()
        reporte["num_tokens_real_sobre_estimado"] = {
            "min": round(ratios[0], 3),
            "p50": round(ratios[len(ratios) // 2], 3),
            "max": round(ratios[-1], 3),
            "media": round(sum(ratios) / len(ratios), 3),
        }
    (dir_salida / "manifiesto.json").write_text(
        json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    parcial.limpiar()

    return reporte


def versiones() -> dict:
    import importlib
    out = {"python": sys.version.split()[0]}
    for mod in ("torch", "transformers", "sentence_transformers", "faiss", "numpy"):
        try:
            out[mod] = getattr(importlib.import_module(mod), "__version__", "?")
        except Exception:
            out[mod] = None
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Encodea chunks.jsonl y construye el índice FAISS")
    ap.add_argument("chunks", type=Path, help="data/chunks.jsonl")
    ap.add_argument("salida", type=Path, help="directorio de salida (encoder_bge-m3)")
    ap.add_argument("--muestra", type=int, default=None,
                    help="encodea solo N chunks (estratificados por fenómeno) para medir")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE,
                    help=f"constante por determinismo; por defecto {BATCH_SIZE}")
    ap.add_argument("--dispositivo", default=None, help="cpu | cuda (por defecto: autodetecta)")
    ap.add_argument("--con-contexto", action="store_true",
                    help="antepone el header al texto ENCODEADO (metadata.jsonl no cambia)")
    ap.add_argument("--reanudar", action="store_true",
                    help="continúa una corrida cortada desde el último batch completo")
    ap.add_argument("--revision", default=REVISION, help="commit exacto del modelo en HuggingFace")
    args = ap.parse_args()

    rep = construir(args.chunks, args.salida, muestra=args.muestra,
                    batch_size=args.batch_size, dispositivo=args.dispositivo,
                    con_contexto=args.con_contexto, reanudar=args.reanudar,
                    revision=args.revision)

    print(f"\nVectores            : {rep['total']}")
    print(f"Dimensión           : {rep['dimension']}")
    print(f"Catálogo masivo     : {rep['catalogo_masivo']}")
    print(f"ms/chunk            : {rep['ms_por_chunk']}")
    if rep.get("muestra"):
        proyeccion = rep["ms_por_chunk"] * 86046 / 1000 / 60
        print(f"Proyección a 86.046 : {proyeccion:.0f} min ({proyeccion / 60:.1f} h)")
    if rep.get("num_tokens_real_sobre_estimado"):
        r = rep["num_tokens_real_sobre_estimado"]
        print(f"tokens real/estimado: p50 {r['p50']}  [{r['min']}, {r['max']}]")
    if rep["chunks_sobre_max_seq_length"]:
        print(f"TRUNCADOS           : {len(rep['chunks_sobre_max_seq_length'])} "
              f"chunks superan {MAX_SEQ_LENGTH} tokens")
    print(f"Revisión del modelo : {rep['revision']}  <- pegar en REVISION")
    print(f"\nManifiesto: {args.salida / 'manifiesto.json'}")
    print(f"Verificar : python scripts/verificar_indice.py {args.salida}")


if __name__ == "__main__":
    main()
