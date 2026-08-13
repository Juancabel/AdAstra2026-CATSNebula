# -*- coding: utf-8 -*-
"""
chunk.py — Fragmentación de documents.jsonl -> chunks.jsonl. Módulo de C.

Contrato 2. Campos obligatorios (Tabla 1 del spec): doc_id, chunk_id, fuente,
formato, fenomeno, posicion, num_tokens, texto. El equipo añade num_words,
lang y contexto.

TRES RAMAS DE SEGMENTACIÓN
--------------------------
El §3.3 prohíbe cortar oraciones. Pero un tercio del corpus no tiene oraciones,
así que "unidad atómica" significa cosas distintas según el formato:

1. PROSA (json de artículo, pdf, html, md, txt, imagen)
   Unidad = la oración, detectada con pysbd.

2. REGISTRO (pbf, xlsx, csv)
   Unidad = la línea. El texto es "campo: valor | campo: valor", una fila por
   línea, y no contiene oraciones: un terminador cada 123 palabras en pbf.
   Partir una línea deja un fragmento con un campo mutilado, que es justo lo
   que el §3.3 quiere evitar. La interpretación documentada es que el registro
   hace aquí el papel de la oración.

3. LISTA (dentro de prosa)
   Los 363 archivos de Alertas_Tempranas traen un campo `municipios` con
   cientos de topónimos separados por ';' y ningún punto. pysbd lo ve como UNA
   oración de hasta 582 palabras, por encima del tope de 250 de salida.
   No es una oración: es una lista, y cada elemento es una unidad completa.

DOS FASES SEPARADAS
-------------------
segmentar_documento() es caro (pysbd tarda ~48 s sobre el corpus de prosa) y
NO depende del tamaño de chunk. empaquetar() es barato y sí depende.
Separarlas permite cachear la segmentación y barrer tamaños en el Día 5 sin
volver a segmentar en cada iteración.

CATÁLOGOS MASIVOS (registro con miles de filas)
------------------------------------------------
Medido sobre el corpus real: 3 CSV bibliométricos de AI_Index superan las
100.000 filas (PMID | Title | Authors | Citation, ~70 palabras/fila). Con el
objetivo normal de 200 palabras eso da ~40.000 chunks de UN documento —
92.608 solo entre los 26 CSV del corpus, dominando el índice completo por
volumen frente a los ~1.826 documentos totales.

Bajar esto solo con `maximo` no alcanza: a 250 palabras entran ~3.5 filas en
vez de ~2.8, una reducción marginal. El problema es de granularidad, no de
tamaño de chunk. Por eso, para documentos de registro cuyo número de
unidades supera UMBRAL_UNIDADES_CATALOGO, `chunkear_documento()` empaqueta
con OBJETIVO_PALABRAS_CATALOGO en vez del objetivo normal — sigue siendo
segmentación por registro (nunca corta una fila), solo agrupa más filas por
chunk. No toca segmentar_documento() ni la caché.

Los chunks resultantes (~1.800 palabras) superan el límite de salida de 250
palabras (§9.2.1), pero eso se resuelve al generar resultados.jsonl, no
aquí: subdividir_para_salida() corta por fila (nunca a mitad de registro) y
todos los sub-fragmentos comparten el chunk_id original, exactamente como
permite el §9.2.1 para cualquier chunk largo.

Es una decisión deliberada, no solo de cómputo: una fila individual de cita
bibliográfica ("PMID: X | Title: Y") es poco probable que sea, por sí sola,
la granularidad que una consulta de evaluación busca; agrupar por tema
mantiene la recuperabilidad temática sin generar decenas de miles de
vectores casi indistinguibles entre sí.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CHUNKER_VERSION = "1.2.0"

# Formatos serializados como registros: la línea es la unidad atómica.
FORMATOS_REGISTRO = frozenset({"pbf", "xlsx", "csv"})

# Objetivo y tope, en palabras. El tope se queda bajo las 250 de la regla de
# salida (§9.2): así ningún chunk necesita subdividirse al responder, y el
# esquema de sub-fragmentos no llega a usarse nunca.
OBJETIVO_PALABRAS = 200
MAXIMO_PALABRAS = 250

# A partir de cuántas unidades atómicas un documento de registro se trata
# como "catálogo masivo" (ver docstring del módulo). El corte real medido en
# el corpus: el CSV más chico de este grupo tiene 4.369 filas; el más grande
# fuera del grupo, 1.343 — una banda vacía de 3.25x entre ambos. 2.000 queda
# cómodo en el medio.
UMBRAL_UNIDADES_CATALOGO = 2000

# Objetivo de empaquetado para catálogos masivos. Deliberadamente grande
# frente a OBJETIVO_PALABRAS, pero lejos del límite de contexto de BGE-M3
# (8192 tokens ≈ 4300 palabras estimadas): ~1.800 palabras reduce el conteo
# de chunks en ~9x sin acercarse al techo del encoder. Estos chunks superan
# el límite de salida de 250 palabras; ver subdividir_para_salida().
OBJETIVO_PALABRAS_CATALOGO = 1800

# Una "oración" es en realidad una lista si tiene bastantes ';' y estos
# dominan claramente sobre los puntos. Exigir CERO puntos era demasiado
# estricto: un bloque de 268 municipios con un solo punto se escapaba.
MIN_PUNTOYCOMA_LISTA = 3
FACTOR_DOMINIO_LISTA = 3

# Ratio palabras -> tokens de XLM-R cuando no hay tokenizador real disponible.
# Conservador: el peor caso medido para lenguas romances ronda 1.9.
RATIO_TOKENS_ESTIMADO = 1.9

# pysbd no soporta 'pt'. Español comparte puntuación, abreviaturas y decimales,
# que es donde se rompe un segmentador, así que es el sustituto correcto.
IDIOMA_PYSBD = {
    "es": "es", "pt": "es", "en": "en", "fr": "fr",
    "zh": "zh", "ja": "ja", "it": "it", "de": "de", "ru": "ru",
}
IDIOMA_PYSBD_POR_DEFECTO = "es"

# pysbd degrada en entradas muy largas: sobre una celda de 6.043 palabras
# devuelve UN solo segmento, mientras que sobre los primeros 3.000 caracteres
# del mismo texto devuelve 22. Se segmenta por ventanas cortadas en espacio.
VENTANA_SEGMENTACION = 2000

_segmentadores: dict[str, object] = {}


def _segmentador(lang: str | None):
    """Devuelve (y cachea) el segmentador de pysbd del idioma."""
    import pysbd

    codigo = IDIOMA_PYSBD.get((lang or "").lower(), IDIOMA_PYSBD_POR_DEFECTO)
    if codigo not in _segmentadores:
        _segmentadores[codigo] = pysbd.Segmenter(language=codigo, clean=False)
    return _segmentadores[codigo]


def _segmentar_texto(texto: str, lang: str | None) -> list[str]:
    """Segmenta en oraciones, por ventanas si el texto es muy largo."""
    seg = _segmentador(lang)
    if len(texto) <= VENTANA_SEGMENTACION:
        return [o.strip() for o in seg.segment(texto) if o.strip()]

    salida, ini = [], 0
    while ini < len(texto):
        fin = min(ini + VENTANA_SEGMENTACION, len(texto))
        if fin < len(texto):
            corte = texto.rfind(" ", ini, fin)
            if corte > ini:
                fin = corte
        salida.extend(o.strip() for o in seg.segment(texto[ini:fin]) if o.strip())
        ini = fin
    return salida


def _palabras(texto: str) -> int:
    return len(texto.split())


def _es_lista(texto: str) -> bool:
    """Distingue una lista con ';' de una oración larga de verdad."""
    pyc = texto.count(";")
    return (pyc >= MIN_PUNTOYCOMA_LISTA
            and pyc > max(texto.count("."), 1) * FACTOR_DOMINIO_LISTA)


def _partir_por_puntoycoma(u: str) -> list[str] | None:
    if not _es_lista(u):
        return None
    piezas = [p.strip() for p in u.split(";") if p.strip()]
    return piezas if len(piezas) > 1 else None


def _partir_por_campo(u: str) -> list[str] | None:
    piezas = [p.strip() for p in u.split("|") if p.strip()]
    return piezas if len(piezas) > 1 else None


def _partir_por_linea(u: str) -> list[str] | None:
    piezas = [l.strip() for l in u.splitlines() if l.strip()]
    return piezas if len(piezas) > 1 else None


def _partir_por_oracion(u: str, lang: str | None) -> list[str] | None:
    """
    Último recurso estructural. Conserva el prefijo "campo:" en cada trozo:
    una celda de XLSX puede acumular cientos de títulos de artículo, y sin el
    prefijo los trozos 2..n dejarían de ser registros válidos.
    """
    m = re.match(r"^([\w\u00C0-\u024F\.\- ]{1,40}:)\s*", u)
    prefijo, cuerpo = (m.group(1), u[m.end():]) if m else ("", u)
    oraciones = _segmentar_texto(cuerpo, lang)
    if len(oraciones) <= 1:
        return None
    return [f"{prefijo} {o}".strip() for o in oraciones]


def _partir_unidad_larga(unidad: str, maximo: int, lang: str | None = None) -> list[str]:
    """
    Trocea una unidad que excede el tope, SIN romper una oración real.

    Escalera RECURSIVA, de más a menos estructural. Cada estrategia se aplica
    solo a las piezas que siguen pasadas del tope, porque partir una vez no
    garantiza nada: un registro "pmid: X | title: <6043 palabras>" se parte por
    '|' y una de las dos mitades sigue siendo enorme.

      1. Lista con ';'      -> cada elemento es completo
      2. Registro con '|'   -> cada campo es completo
      3. Varias líneas      -> cada línea es completa
      4. Oraciones (pysbd)  -> corte legal por el §3.3
      5. Se deja entera     -> es una oración real más larga que el tope; el
         §3.3 manda sobre la regla de 250 y el reporte la registra.
    """
    estrategias = [
        _partir_por_puntoycoma,
        _partir_por_campo,
        _partir_por_linea,
        lambda u: _partir_por_oracion(u, lang),
    ]

    piezas = [unidad]
    for partir in estrategias:
        if all(_palabras(p) <= maximo for p in piezas):
            break
        nuevas = []
        for pieza in piezas:
            if _palabras(pieza) <= maximo:
                nuevas.append(pieza)
                continue
            sub = partir(pieza)
            nuevas.extend(sub if sub else [pieza])
        piezas = nuevas

    return _agrupar(piezas, " ", maximo)


def _agrupar(piezas: list[str], sep: str, maximo: int) -> list[str]:
    """Junta piezas hasta el tope sin partir ninguna."""
    grupos, actual, n = [], [], 0
    for pieza in piezas:
        p = _palabras(pieza)
        if actual and n + p > maximo:
            grupos.append(sep.join(actual))
            actual, n = [], 0
        actual.append(pieza)
        n += p
    if actual:
        grupos.append(sep.join(actual))
    return grupos


def subdividir_para_salida(texto: str, maximo: int = MAXIMO_PALABRAS,
                           formato: str | None = None) -> list[str]:
    """
    Para D (generador.py), NO para la ingesta.

    Divide un chunk recuperado que supera el límite de salida de §9.2.1 (250
    palabras) en sub-fragmentos que no lo superan, sin cortar oraciones ni
    registros. Necesaria sobre todo para los chunks de catálogo masivo
    (~1.800 palabras, ver docstring del módulo), pero sirve para cualquier
    chunk largo.

    - Si `formato` es un formato de registro (o, a falta de dato, el texto
      contiene '\\n'): se corta por fila. Un registro es la unidad atómica
      aquí, igual que en segmentar_documento().
    - Si no: se corta por oración con pysbd.

    Todos los sub-fragmentos comparten el chunk_id original (§9.2.1) — eso
    lo arma quien llama a esta función al construir el objeto de salida, no
    esta función.
    """
    if _palabras(texto) <= maximo:
        return [texto]

    es_registro = (formato in FORMATOS_REGISTRO) if formato else ("\n" in texto)
    if es_registro:
        piezas = _partir_por_linea(texto)
        sep = "\n"
    else:
        piezas = _segmentar_texto(texto, None)
        sep = " "

    if not piezas or len(piezas) <= 1:
        return [texto]  # unidad real más larga que el tope; nada legal que cortar
    return _agrupar(piezas, sep, maximo)


def _unir_line_wraps(texto: str) -> str:
    """
    Une los saltos de línea que son 'line-wrap' del PDF (una oración
    partida por el ancho de página) y respeta los que son fin de oración,
    título o ítem de índice.

    Motivo: el extractor conserva el salto de línea visual del PDF. pysbd,
    al ver "...51 notable\nmachine learning...", toma ese \n como fin de
    oración y parte la oración en dos unidades; el corte sobrevive al
    empaquetado y deja chunks que terminan a mitad de frase.

    Regla determinista: se une SOLO si el carácter previo NO es terminador
    (.!?:;…) y el carácter tras el \n es una letra minúscula (con caja).
    islower() es Unicode: cubre latín, cirílico, griego y acentos por igual.
    Si tras el \n hay mayúscula o dígito (título, nueva oración, ítem de TOC
    "Patents 12"), se respeta el salto. Las escrituras sin caja (CJK, árabe)
    nunca son islower(), así que quedan intactas (no llevan espacio).
    """
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    # guion de corte de palabra:  "develop-\nment" -> "development"
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)

    def _unir(m):
        return (f"{m.group(1)} {m.group(2)}"
                if m.group(2).islower() else m.group(0))

    # char previo no-terminador + salto + letra: se une si la letra es
    # minúscula (wrap real); si no, se conserva el salto.
    return re.sub(r"([^\.\!\?\:\;\u2026\n])\n(\w)", _unir, texto)


def segmentar_documento(doc: dict, maximo: int = MAXIMO_PALABRAS) -> list[str]:
    """
    Fase 1 (cara, cacheable): parte el documento en unidades atómicas.

    Ninguna unidad se partirá después al empaquetar. Es el único punto donde
    se decide qué es "indivisible".
    """
    texto = (doc.get("text") or "").strip()
    if not texto:
        return []

    formato = (doc.get("formato") or "").lower()

    # Rama 2: datos de registro. La línea es la unidad; no pasa por pysbd.
    # Una línea puede aun así superar el tope (celdas que absorbieron mucho
    # texto al aplanar: la mayor del corpus tiene 6.050 palabras), así que se
    # aplica la misma escalera que en prosa antes de darla por atómica.
    if formato in FORMATOS_REGISTRO:
        unidades = []
        for linea in texto.splitlines():
            linea = linea.strip()
            if not linea:
                continue
            if _palabras(linea) > maximo:
                unidades.extend(_partir_unidad_larga(linea, maximo, doc.get("lang")))
            else:
                unidades.append(linea)
        return unidades

    # Rama 1: prosa. Se segmenta párrafo a párrafo para no perder la
    # estructura y para que pysbd trabaje sobre trozos manejables.
    unidades: list[str] = []
    for parrafo in re.split(r"\n\s*\n", texto):
        parrafo = parrafo.strip()
        if not parrafo:
            continue
        # Une los line-wraps del PDF antes de pysbd (ver _unir_line_wraps):
        # sin esto, pysbd parte oraciones en el salto de línea del ancho de
        # página y quedan chunks cortados a mitad de frase.
        parrafo = _unir_line_wraps(parrafo)
        for oracion in _segmentar_texto(parrafo, doc.get("lang")):
            # Rama 3: lo que pysbd cree una oración pero no lo es.
            if _palabras(oracion) > maximo:
                unidades.extend(_partir_unidad_larga(oracion, maximo, doc.get("lang")))
            else:
                unidades.append(oracion)
    return unidades


def empaquetar(unidades: list[str], objetivo: int = OBJETIVO_PALABRAS,
               maximo: int = MAXIMO_PALABRAS, separador: str = " ") -> list[str]:
    """
    Fase 2 (barata, se repite en el barrido): junta unidades en chunks.

    Cierra el chunk en cuanto añadir la siguiente unidad pasaría del objetivo,
    que es exactamente el "retroceder al último límite completo" del §3.3.
    Sin solapamiento: duplicar texto gasta puestos del top-10 y distorsiona la
    agregación a nivel documento.
    """
    chunks, actual, n = [], [], 0
    for unidad in unidades:
        p = _palabras(unidad)
        if actual and n + p > objetivo:
            chunks.append(separador.join(actual))
            actual, n = [], 0
        actual.append(unidad)
        n += p
        # Una sola unidad ya pasada del objetivo cierra chunk por sí misma.
        if n >= objetivo:
            chunks.append(separador.join(actual))
            actual, n = [], 0
    if actual:
        chunks.append(separador.join(actual))
    return chunks


def construir_contexto(doc: dict) -> str:
    """
    Cabecera que se antepone a CADA chunk al codificar.

    NO entra en `texto`: el spec define ese campo como "texto original del
    fragmento, sin modificaciones". Va aparte para que encode_index.py decida
    si la usa, y para que sea un interruptor del A/B del Día 5.

    Fuentes, por orden: título del documento, y para datos de registro el
    nombre de hoja o capa, que en el texto solo aparece en la primera línea y
    por tanto solo llegaría al chunk c000.
    """
    partes = []
    titulo = (doc.get("title") or "").strip()
    if titulo:
        partes.append(titulo)

    extra = doc.get("extra") or {}
    for clave in ("hojas_utiles", "capas"):
        valores = extra.get(clave)
        if valores:
            partes.extend(str(v).strip() for v in valores if str(v).strip())

    return " — ".join(dict.fromkeys(p for p in partes if p))


def _contar_tokens(texto: str, tokenizador=None) -> tuple[int, bool]:
    """Devuelve (num_tokens, es_estimacion)."""
    if tokenizador is not None:
        return len(tokenizador(texto)), False
    return int(round(_palabras(texto) * RATIO_TOKENS_ESTIMADO)), True


def chunkear_documento(doc: dict, objetivo: int = OBJETIVO_PALABRAS,
                       maximo: int = MAXIMO_PALABRAS, tokenizador=None,
                       unidades: list[str] | None = None) -> list[dict]:
    """
    Convierte un documento del Contrato 1 en una lista de objetos del Contrato 2.

    Args:
        unidades: segmentación ya calculada (de la caché). Si es None se
            calcula aquí.
    """
    if unidades is None:
        unidades = segmentar_documento(doc, maximo)
    if not unidades:
        return []

    formato = (doc.get("formato") or "").lower()
    # Los registros se unen con salto de línea: cada uno sigue siendo una fila
    # legible y el chunk conserva la forma tabular del original.
    separador = "\n" if formato in FORMATOS_REGISTRO else " "

    # Catálogo masivo (ver docstring del módulo): mismo separador, mismas
    # unidades ya segmentadas, solo cambia cuánto junta empaquetar(). No
    # afecta la caché de segmentación.
    es_catalogo = formato in FORMATOS_REGISTRO and len(unidades) > UMBRAL_UNIDADES_CATALOGO
    objetivo_pack = OBJETIVO_PALABRAS_CATALOGO if es_catalogo else objetivo

    doc_id = doc.get("doc_id")
    contexto = construir_contexto(doc)

    salida = []
    for posicion, texto in enumerate(empaquetar(unidades, objetivo_pack, maximo, separador)):
        num_tokens, _ = _contar_tokens(texto, tokenizador)
        salida.append({
            "doc_id": doc_id,
            "chunk_id": f"{doc_id}_c{posicion:03d}",
            "fuente": doc.get("fuente"),
            "formato": doc.get("formato"),
            "fenomeno": doc.get("fenomeno"),
            "posicion": posicion,
            "num_tokens": num_tokens,
            "texto": texto,
            "num_words": _palabras(texto),
            "lang": doc.get("lang"),
            "contexto": contexto,
        })
    return salida


# ---------------------------------------------------------------------------
# Caché de segmentación
# ---------------------------------------------------------------------------

def construir_cache(entrada: Path, cache: Path, maximo: int = MAXIMO_PALABRAS) -> dict:
    """
    Segmenta todo el corpus UNA vez y guarda {doc_id: [unidades]}.

    pysbd tarda ~48 s sobre el corpus de prosa. Sin caché, cada iteración del
    barrido de tamaños del Día 5 paga ese minuto para nada: la segmentación no
    depende del tamaño de chunk.
    """
    datos, n = {}, 0
    with entrada.open(encoding="utf-8") as f:
        for linea in f:
            if not linea.strip():
                continue
            doc = json.loads(linea)
            datos[doc["doc_id"]] = segmentar_documento(doc, maximo)
            n += 1
    payload = {"chunker_version": CHUNKER_VERSION, "maximo": maximo, "unidades": datos}
    cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"documentos": n, "unidades": sum(len(v) for v in datos.values())}


def chunkear_jsonl(entrada: Path, salida: Path, objetivo: int = OBJETIVO_PALABRAS,
                   maximo: int = MAXIMO_PALABRAS, cache: Path | None = None,
                   tokenizador=None) -> dict:
    """Lee documents.jsonl y escribe chunks.jsonl. Devuelve un reporte."""
    unidades_cache = {}
    if cache and cache.exists():
        _cache_raw = json.loads(cache.read_text(encoding="utf-8"))
        # Caché versionada. Si se generó con otra versión del chunker (p. ej.
        # antes del fix de line-wraps), la segmentación guardada es obsoleta:
        # se ignora en vez de re-chunkear sobre unidades viejas.
        if _cache_raw.get("chunker_version") == CHUNKER_VERSION:
            unidades_cache = _cache_raw.get("unidades", {})
        else:
            print(f"AVISO: caché de segmentación versión "
                  f"{_cache_raw.get('chunker_version')!r} != {CHUNKER_VERSION!r}; "
                  f"se ignora y se re-segmenta.")

    rep = {
        "chunker_version": CHUNKER_VERSION,
        "objetivo": objetivo, "maximo": maximo,
        "documentos": 0, "documentos_sin_chunks": [],
        "chunks": 0, "por_formato": {}, "chunks_por_doc": [],
        "sobre_maximo": [], "catalogo_masivo": [],
        "num_tokens_estimado": tokenizador is None,
    }

    with entrada.open(encoding="utf-8") as f_in, salida.open("w", encoding="utf-8") as f_out:
        for linea in f_in:
            if not linea.strip():
                continue
            doc = json.loads(linea)
            rep["documentos"] += 1

            chunks = chunkear_documento(
                doc, objetivo, maximo, tokenizador,
                unidades=unidades_cache.get(doc["doc_id"]),
            )
            if not chunks:
                rep["documentos_sin_chunks"].append(doc.get("fuente"))
                continue

            fmt = doc.get("formato")
            rep["por_formato"][fmt] = rep["por_formato"].get(fmt, 0) + len(chunks)
            rep["chunks"] += len(chunks)
            rep["chunks_por_doc"].append((len(chunks), doc.get("fuente")))

            for c in chunks:
                if c["num_words"] > maximo:
                    entrada = {"chunk_id": c["chunk_id"], "fuente": c["fuente"],
                               "num_words": c["num_words"]}
                    if fmt in FORMATOS_REGISTRO:
                        # Esperado: empaquetado ampliado de catálogo masivo.
                        # Requiere subdividir_para_salida() en generador.py.
                        rep["catalogo_masivo"].append(entrada)
                    else:
                        # Inesperado salvo una oración real más larga que el
                        # tope: el §3.3 manda sobre la regla de 250.
                        rep["sobre_maximo"].append(entrada)
                f_out.write(json.dumps(c, ensure_ascii=False) + "\n")

    rep["chunks_por_doc"].sort(reverse=True)
    return rep


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fragmenta documents.jsonl")
    ap.add_argument("entrada", type=Path)
    ap.add_argument("salida", type=Path)
    ap.add_argument("--objetivo", type=int, default=OBJETIVO_PALABRAS)
    ap.add_argument("--maximo", type=int, default=MAXIMO_PALABRAS)
    ap.add_argument("--cache", type=Path, default=None,
                    help="ruta de la caché de segmentación (se crea si no existe)")
    ap.add_argument("--reporte", type=Path, default=None)
    args = ap.parse_args()

    if args.cache and not args.cache.exists():
        info = construir_cache(args.entrada, args.cache, args.maximo)
        print(f"Caché creada: {info['documentos']} documentos, "
              f"{info['unidades']} unidades -> {args.cache}")

    r = chunkear_jsonl(args.entrada, args.salida, args.objetivo, args.maximo,
                       args.cache)

    print(f"\nDocumentos          : {r['documentos']}")
    print(f"Chunks              : {r['chunks']}")
    print(f"Media chunks/doc    : {r['chunks'] / max(r['documentos'], 1):.2f}")
    print(f"Sin chunks          : {len(r['documentos_sin_chunks'])}")
    print(f"Sobre el tope       : {len(r['sobre_maximo'])}   <- oraciones reales largas")
    print(f"Catálogo masivo     : {len(r['catalogo_masivo'])}   <- empaquetado ampliado, "
          f"requieren subdividir_para_salida() en generador.py")
    if r["num_tokens_estimado"]:
        print("num_tokens          : ESTIMADO (palabras x 1.9). Recalcular con el "
              "tokenizador del encoder cuando esté elegido.")
    print("\nChunks por formato:")
    for fmt, n in sorted(r["por_formato"].items()):
        print(f"  {fmt:<8} {n:>7}")
    print("\nDocumentos que más chunks generan:")
    for n, fuente in r["chunks_por_doc"][:5]:
        print(f"  {n:>5}  {str(fuente)[-66:]}")

    if args.reporte:
        args.reporte.write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"\nReporte: {args.reporte}")