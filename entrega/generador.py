# -*- coding: utf-8 -*-
"""
generador.py — CATSNebula · CODEFEST Ad Astra 2026 (Etapa 1)

Lee las 50 consultas, usa el indice FAISS ya entregado (NO lo reconstruye),
recupera fragmentos y documentos, y escribe resultados.jsonl con EXACTAMENTE
50 lineas conforme al esquema de la Seccion 9 del reto.

Requisitos duros del reto (criterio de ELIMINACION):
  - Corre en CPU, Python >= 3.9.5.
  - USA el index.faiss entregado; no regenera vectores.
  - resultados.jsonl con exactamente 50 lineas (q001..q050).
  - Cada consulta: exactamente 3 documentos y 10 fragmentos.
  - Ningun fragmento supera 250 palabras.
  - 100% reproducible: sin aleatoriedad, fp32, sin dependencias externas que
    fallen (el modelo se carga desde disco local, no desde HuggingFace).

Uso:
  python generador.py
  python generador.py --consultas ruta/otro_consultas.jsonl --salida ruta/out.jsonl

Rutas por defecto (relativas a la ubicacion de ESTE archivo = entrega/):
  base_vectorial/encoder_bge-m3/index.faiss
  base_vectorial/encoder_bge-m3/metadata.jsonl
  modelos/bge-m3/            (pesos del encoder, shippeados)
  consultas.jsonl           (las 50 consultas ya parseadas del PDF oficial)
  resultados.jsonl          (salida)
"""

from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

# Sentinel de version: permite a verificar_generador.py confirmar que cargo
# ESTE archivo y no un generador.py viejo/ajeno que lo tape en la ruta.
GENERADOR_VERSION = "1.0-baseline"

# ----------------------------------------------------------------------------
# CONFIGURACION  (perillas de tuning; los defaults dan un baseline valido)
# ----------------------------------------------------------------------------
RUTA_BASE = Path(__file__).resolve().parent  # = entrega/

RUTA_INDICE   = RUTA_BASE / "base_vectorial" / "encoder_bge-m3" / "index.faiss"
RUTA_METADATA = RUTA_BASE / "base_vectorial" / "encoder_bge-m3" / "metadata.jsonl"
RUTA_MODELO   = RUTA_BASE / "modelos" / "bge-m3"   # pesos locales shippeados
MODELO_HF     = "BAAI/bge-m3"                       # fallback si no hay pesos locales
REVISION_HF   = "5617a9f61b028005a4858fdac845db406aefb181"
MAX_SEQ_LENGTH = 8192   # DEBE coincidir con encode_index.py; si no, los chunks
                        # largos se truncan distinto y el verificador falla

RUTA_CONSULTAS = RUTA_BASE / "consultas.jsonl"
RUTA_SALIDA    = RUTA_BASE / "resultados.jsonl"

N_DOCS = 3            # documentos por consulta (fijo por el reto)
N_FRAGS = 10          # fragmentos por consulta (fijo por el reto)
MAX_PALABRAS = 250    # tope duro por fragmento (Seccion 9.2.1)
TOP_K = 200           # profundidad de busqueda: holgada para dedup + agregacion

# Agregacion fragmento -> documento para F1@3. Es la MAYOR palanca documental.
# "max" es el default robusto; cambiar a "sum"/"mean" solo con dev set que lo
# justifique (medir, no adivinar).
AGREGACION = "max"    # {"max", "sum", "mean"}

# Campo de texto en metadata.jsonl. OJO: es "texto" (espanol); la salida del
# reto exige la llave "text" (ingles). El mapeo se hace al construir la salida.
CAMPO_TEXTO = "texto"


# ----------------------------------------------------------------------------
# UTILIDADES DE TEXTO
# ----------------------------------------------------------------------------
def contar_palabras(texto: str) -> int:
    """Unidad de conteo = split por espacios en blanco (interpretacion
    conservadora de 'palabra', la mas probable del evaluador)."""
    return len(texto.split())


# Fronteras de corte ordenadas de MAS a MENOS linguistica. Se prueba la primera
# que exista; si una unidad resultante sigue excediendo el tope, se re-parte con
# la siguiente frontera. El corte por palabra es el caso base (garantiza el tope
# duro), reservado a blobs de registros sin ninguna frontera natural.
_FRONTERAS = [
    r'(?<=[.!?…。！？])\s+',       # fin de oracion (incl. CJK)
    r'\n+',                        # saltos de linea (registros/filas)
    r'\s*;\s*',                    # punto y coma
    r'\s*\|\s*',                   # barra vertical
    r'\s+/\s+',                    # barra CON espacios (registro real, no "mg/dl")
    r'(?<=\))/(?=[A-ZÁÉÍÓÚÑ])',    # ")/Palabra": patron tipico de catalogo CSV
    r'\t+',                        # tabulador
]


def _empacar(unidades, maxp):
    """Empaca unidades consecutivas en piezas de <= maxp palabras."""
    piezas, actual = [], ""
    for u in unidades:
        u = u.strip()
        if not u:
            continue
        cand = (actual + " " + u).strip() if actual else u
        if contar_palabras(cand) <= maxp:
            actual = cand
        else:
            if actual:
                piezas.append(actual)
            actual = u
    if actual:
        piezas.append(actual)
    return piezas


def _corte_duro_palabras(texto, maxp):
    """Ultimo recurso: partir por palabras. Solo para blobs sin fronteras."""
    ps = texto.split()
    return [" ".join(ps[i:i + maxp]) for i in range(0, len(ps), maxp)]


def subdividir(texto: str, maxp: int = MAX_PALABRAS):
    """Divide un texto en piezas de <= maxp palabras respetando, en lo posible,
    fronteras linguisticas/de registro. Garantiza el tope duro. Cada sub-fragmento
    conserva el mismo chunk_id (se asigna afuera): es trazabilidad, no
    emparejamiento (Seccion 9.2.1)."""
    if contar_palabras(texto) <= maxp:
        return [texto]
    for patron in _FRONTERAS:
        partes = [p for p in re.split(patron, texto) if p.strip()]
        if len(partes) <= 1:
            continue
        expandidas = []
        for p in partes:
            if contar_palabras(p) <= maxp:
                expandidas.append(p)
            else:
                expandidas.extend(subdividir(p, maxp))  # recursion: fronteras mas finas
        piezas = _empacar(expandidas, maxp)
        if all(contar_palabras(x) <= maxp for x in piezas):
            return piezas
    return _corte_duro_palabras(texto, maxp)


# ----------------------------------------------------------------------------
# CARGA
# ----------------------------------------------------------------------------
def cargar_metadata(ruta: Path):
    """metadata.jsonl -> lista alineada 1:1 con los IDs internos de FAISS
    (linea i <-> id i). Falla ruidoso si algo no cuadra."""
    metas = []
    with ruta.open("r", encoding="utf-8") as f:
        for n, linea in enumerate(f):
            linea = linea.strip()
            if not linea:
                continue
            obj = json.loads(linea)
            if CAMPO_TEXTO not in obj or "doc_id" not in obj or "chunk_id" not in obj:
                raise ValueError(f"metadata.jsonl linea {n}: faltan campos obligatorios")
            metas.append(obj)
    if not metas:
        raise ValueError("metadata.jsonl vacio")
    return metas


def cargar_indice(ruta: Path):
    import faiss  # se importa aca para no exigirlo en tests de logica pura
    idx = faiss.read_index(str(ruta))
    return idx


def cargar_consultas(ruta: Path):
    """Lee las 50 consultas. Tolerante a un par de formatos plausibles por si el
    organizador entrega su propio archivo:
      - JSONL con {"query_id", "texto"|"text"|"consulta"|"pregunta"}
      - TXT plano, una consulta por linea (se numeran q001..)
    Devuelve lista de (query_id, texto) ordenada por query_id."""
    consultas = []
    texto_crudo = ruta.read_text(encoding="utf-8")
    lineas = [l for l in texto_crudo.splitlines() if l.strip()]

    def _extraer_texto(obj):
        for k in ("texto", "text", "consulta", "pregunta", "query"):
            if k in obj and isinstance(obj[k], str) and obj[k].strip():
                return obj[k].strip()
        return None

    parseado_jsonl = True
    for i, l in enumerate(lineas):
        try:
            obj = json.loads(l)
        except json.JSONDecodeError:
            parseado_jsonl = False
            break
        if not isinstance(obj, dict):
            parseado_jsonl = False
            break
        qid = obj.get("query_id") or f"q{i + 1:03d}"
        txt = _extraer_texto(obj)
        if txt is None:
            parseado_jsonl = False
            break
        consultas.append((qid, txt))

    if not parseado_jsonl:
        consultas = [(f"q{i + 1:03d}", l.strip()) for i, l in enumerate(lineas)]

    consultas.sort(key=lambda c: c[0])
    return consultas


def cargar_encoder(dispositivo="cpu"):
    """Carga bge-m3 replicando EXACTAMENTE el camino de encode_index.py:
    SentenceTransformer, fp32 puro, eval(), max_seq_length=8192. bge-m3 dense es
    simetrico: NO usa prefijos query:/passage:, query y passage se encodean igual.
    Prioriza pesos locales shippeados; cae a HF solo si no estan."""
    import torch
    from sentence_transformers import SentenceTransformer
    if hasattr(torch.backends, "cuda"):
        torch.backends.cuda.matmul.allow_tf32 = False
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.allow_tf32 = False
    torch.manual_seed(0)

    if RUTA_MODELO.exists():
        modelo = SentenceTransformer(
            str(RUTA_MODELO), device=dispositivo,
            model_kwargs={"torch_dtype": torch.float32})
    else:
        # Fallback (requiere internet; NO deseable en la maquina del organizador).
        modelo = SentenceTransformer(
            MODELO_HF, revision=REVISION_HF, device=dispositivo,
            model_kwargs={"torch_dtype": torch.float32})
    modelo.eval()
    modelo.max_seq_length = MAX_SEQ_LENGTH
    return modelo


def encodear(modelo, textos):
    """Encodea a fp32 con norma L2 unitaria (coseno = producto interno, que es
    lo que hace IndexFlatIP). Determinista en CPU/fp32."""
    import numpy as np
    vecs = modelo.encode(
        textos,
        normalize_embeddings=True,   # norma L2 -> coseno bajo IndexFlatIP
        convert_to_numpy=True,
        batch_size=16,
        show_progress_bar=False,
    )
    return np.asarray(vecs, dtype="float32")


# ----------------------------------------------------------------------------
# RECUPERACION Y CONSTRUCCION DE LA RESPUESTA
# ----------------------------------------------------------------------------
def construir_documentos(hits, metas):
    """hits: lista de (score, faiss_id) ordenada por score desc.
    Agrupa por doc_id, agrega el score, devuelve los 3 doc_id distintos top.
    Dedup natural por doc_id (el reto empareja documento por doc_id oficial)."""
    from collections import defaultdict
    scores = defaultdict(list)
    for score, fid in hits:
        scores[metas[fid]["doc_id"]].append(score)

    def agrega(vs):
        if AGREGACION == "max":
            return max(vs)
        if AGREGACION == "sum":
            return sum(vs)
        if AGREGACION == "mean":
            return sum(vs) / len(vs)
        raise ValueError(f"AGREGACION invalida: {AGREGACION}")

    # Orden determinista: score agregado desc, y doc_id asc como desempate.
    ranking = sorted(scores.items(), key=lambda kv: (-agrega(kv[1]), kv[0]))
    top = ranking[:N_DOCS]
    return [{"rank": i + 1, "doc_id": doc_id} for i, (doc_id, _) in enumerate(top)]


def construir_fragmentos(hits, metas):
    """Toma los mejores hits, uno por chunk, y arma exactamente N_FRAGS
    fragmentos <= 250 palabras. Chunk grande -> se subdivide y se toma la primera
    pieza (baseline; conserva el chunk_id original)."""
    fragmentos = []
    for score, fid in hits:
        meta = metas[fid]
        texto = meta[CAMPO_TEXTO]
        if not texto or not texto.strip():
            continue  # los ~6 docs sin texto no aportan fragmento util
        piezas = subdividir(texto, MAX_PALABRAS)
        fragmentos.append({
            "rank": len(fragmentos) + 1,
            "chunk_id": meta["chunk_id"],
            "doc_id": meta["doc_id"],
            "text": piezas[0],   # baseline: primera pieza <=250
        })
        if len(fragmentos) == N_FRAGS:
            break
    return fragmentos


def procesar_consulta(qid, qvec, index, metas):
    import numpy as np
    D, I = index.search(np.asarray([qvec], dtype="float32"), TOP_K)
    # Empareja score con id, descarta -1 (relleno de FAISS cuando faltan vecinos).
    hits = [(float(s), int(i)) for s, i in zip(D[0], I[0]) if i != -1]
    # Orden determinista: score desc, id asc como desempate.
    hits.sort(key=lambda h: (-h[0], h[1]))

    documents = construir_documentos(hits, metas)
    fragments = construir_fragmentos(hits, metas)
    return {"query_id": qid, "documents": documents, "fragments": fragments}


# ----------------------------------------------------------------------------
# VALIDACION DE SALIDA  (falla ruidoso ANTES de entregar)
# ----------------------------------------------------------------------------
def validar_salida(lineas):
    if len(lineas) != 50:
        raise AssertionError(f"resultados.jsonl debe tener 50 lineas, tiene {len(lineas)}")
    for obj in lineas:
        if set(obj.keys()) != {"query_id", "documents", "fragments"}:
            raise AssertionError(f"{obj.get('query_id')}: llaves de nivel superior invalidas")
        docs, frags = obj["documents"], obj["fragments"]
        if len(docs) != N_DOCS:
            raise AssertionError(f"{obj['query_id']}: {len(docs)} documentos (deben ser {N_DOCS})")
        if len(frags) != N_FRAGS:
            raise AssertionError(f"{obj['query_id']}: {len(frags)} fragmentos (deben ser {N_FRAGS})")
        for i, d in enumerate(docs):
            if set(d.keys()) != {"rank", "doc_id"} or d["rank"] != i + 1:
                raise AssertionError(f"{obj['query_id']}: documento[{i}] mal formado")
        for i, fr in enumerate(frags):
            if set(fr.keys()) != {"rank", "chunk_id", "doc_id", "text"} or fr["rank"] != i + 1:
                raise AssertionError(f"{obj['query_id']}: fragmento[{i}] mal formado")
            n = contar_palabras(fr["text"])
            if n > MAX_PALABRAS:
                raise AssertionError(f"{obj['query_id']}: fragmento[{i}] con {n} palabras (> {MAX_PALABRAS})")
    # query_ids en orden q001..q050
    esperados = [f"q{i + 1:03d}" for i in range(50)]
    if [o["query_id"] for o in lineas] != esperados:
        raise AssertionError("query_ids no son q001..q050 en orden")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Genera resultados.jsonl (CODEFEST Ad Astra 2026).")
    ap.add_argument("--consultas", type=Path, default=RUTA_CONSULTAS)
    ap.add_argument("--salida", type=Path, default=RUTA_SALIDA)
    ap.add_argument("--dispositivo", default="cpu")
    args = ap.parse_args()

    print("[1/5] Cargando metadata...", file=sys.stderr)
    metas = cargar_metadata(RUTA_METADATA)

    print("[2/5] Cargando indice FAISS...", file=sys.stderr)
    index = cargar_indice(RUTA_INDICE)
    if index.ntotal != len(metas):
        raise AssertionError(
            f"Desalineamiento: index.ntotal={index.ntotal} != metadata={len(metas)}")

    print("[3/5] Cargando encoder y consultas...", file=sys.stderr)
    consultas = cargar_consultas(args.consultas)
    if len(consultas) != 50:
        raise AssertionError(f"Se esperaban 50 consultas, hay {len(consultas)}")
    modelo = cargar_encoder(args.dispositivo)
    qvecs = encodear(modelo, [txt for _, txt in consultas])

    print("[4/5] Recuperando...", file=sys.stderr)
    lineas = [procesar_consulta(qid, qvecs[i], index, metas)
              for i, (qid, _) in enumerate(consultas)]

    print("[5/5] Validando y escribiendo...", file=sys.stderr)
    validar_salida(lineas)
    with args.salida.open("w", encoding="utf-8") as f:
        for obj in lineas:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"OK -> {args.salida}  ({len(lineas)} lineas)", file=sys.stderr)


if __name__ == "__main__":
    main()