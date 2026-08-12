"""
ingest_data.py — Ingesta de formatos estructurados y exóticos. Módulo de B.

Formatos: json, xlsx, imagen, pbf. (Los csv son de A.)

Recorre el corpus, extrae el texto de cada archivo que le toca, y escribe
data/documents_data.jsonl siguiendo el Contrato 1.

Uso:
    python src/ingest_data.py corpus_original data/documents_data.jsonl
    python src/ingest_data.py corpus_original data/documents_data.jsonl --formato json
    python src/ingest_data.py corpus_original data/documents_data.jsonl --sin-ocr
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus_paths import infer_fenomeno, infer_formato
from dedup_pbf import construir_asignacion
from extractores_datos import (
    extraer_imagen,
    extraer_json,
    extraer_pbf,
    extraer_xlsx,
    extraer_pdf,
    extraer_html,
    extraer_md,
    extraer_txt,
)
from identity import compute_doc_id, normalize_text, text_is_usable
from indice_oficial import cargar_indice_oficial, comparar_con_ingesta
from mapeos_json import (
    ARCHIVOS_EXCLUIDOS,
    BASURA_SISTEMA,
    PREFIJO_BLOQUEO_EXCEL,
)

# Formatos que le corresponden a B. Los csv los procesa A.
FORMATOS_DE_B = {"json", "xlsx", "imagen", "pbf", "pdf", "html", "md", "txt"}

# Campos del índice oficial que se copian a `extra`. El resto (nombre_archivo,
# fenomeno_indice) ya está en el documento o se infiere de la ruta.
CAMPOS_INDICE_A_EXTRA = ("doc_id_oficial", "observatorio", "codigo_observatorio")

# Umbral de palabras útiles POR FORMATO.
#
# Descartar un documento significa que NUNCA se podrá recuperar. Si el ground
# truth lo referencia, es una pérdida directa de F1@3. Por eso el umbral solo
# es agresivo donde el ruido es real:
#   - imagen: el OCR de una foto produce cadenas sin sentido -> filtrar
#   - json/xlsx/pbf: un artículo corto, o un tile con un solo municipio, SIGUE
#     SIENDO un documento legítimo con su `fuente` -> 0
#
# El umbral de pbf era 8 y descartaba dos teselas cuyo único feature tenía tres
# palabras ("popup: Los Lobos"). Ese contenido es correcto, no ruido de OCR:
# el filtro existe para la basura de Tesseract, no para datos bien decodificados.
MIN_PALABRAS_POR_FORMATO = {
    "imagen": 20,
    "pbf": 0,
    "json": 0,
    "xlsx": 0,
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def institucion_de(ruta_relativa: str) -> str:
    """Los dos primeros segmentos de la ruta: F<N>_.../<Observatorio>."""
    partes = ruta_relativa.split("/")
    return "/".join(partes[:2]) if len(partes) >= 2 else ruta_relativa


def debe_excluirse(ruta: Path) -> tuple[str, bool] | None:
    """
    Devuelve (motivo, silencioso) o None si el archivo debe procesarse.

    'silencioso' indica que ni siquiera vale la pena reportarlo: basura del
    sistema operativo que solo ensuciaría el log.
    """
    if ruta.name in BASURA_SISTEMA:
        return ("basura del sistema operativo", True)
    if ruta.name.startswith(PREFIJO_BLOQUEO_EXCEL):
        return ("archivo de bloqueo de Excel", False)
    if ruta.name in ARCHIVOS_EXCLUIDOS:
        return ("metadata del reto, no contenido del corpus", False)
    return None


# ---------------------------------------------------------------------------
# Construcción del documento
# ---------------------------------------------------------------------------

def construir_documento(
    ruta: Path,
    raiz: Path,
    formato: str,
    indice_oficial: dict,
    usar_ocr: bool = True,
    asignacion_pbf: dict | None = None,
) -> dict:
    """
    Convierte un archivo del corpus en un objeto del Contrato 1.

    Raises:
        ValueError: fenómeno indeterminable, institución sin mapeo, o
            extracción vacía (la lanza compute_doc_id).
    """
    ruta_relativa = ruta.relative_to(raiz).as_posix()
    fenomeno = infer_fenomeno(ruta_relativa)
    institucion = institucion_de(ruta_relativa)

    if formato == "json":
        texto, titulo, extra = extraer_json(ruta, institucion)
    elif formato == "xlsx":
        texto, titulo, extra = extraer_xlsx(ruta)
    elif formato == "imagen":
        if not usar_ocr:
            raise ValueError("OCR desactivado con --sin-ocr")
        texto, titulo, extra = extraer_imagen(ruta)
    elif formato == "pdf":
        texto, titulo, extra = extraer_pdf(ruta)
    elif formato == "html":
        texto, titulo, extra = extraer_html(ruta)
    elif formato == "md":
        texto, titulo, extra = extraer_md(ruta)
    elif formato == "txt":
        texto, titulo, extra = extraer_txt(ruta)
    elif formato == "pbf":
        asignados = None
        if asignacion_pbf is not None:
            asignados = asignacion_pbf.get(ruta_relativa, {})
        texto, titulo, extra = extraer_pbf(ruta, ruta_relativa, asignados)
    else:
        raise ValueError(f"formato no soportado por B: {formato}")

    # compute_doc_id normaliza por dentro y lanza ValueError si queda vacío.
    doc_id = compute_doc_id(texto)

    # Metadata del índice oficial de ADL. La clave es la RUTA RELATIVA, no el
    # nombre de archivo: 127 archivos del inventario comparten nombre con otro
    # (72 teselas pbf, 112 de CSET, 2 de ESA). Con clave por nombre, esas
    # teselas recibían el DOC_ID de una tesela distinta.
    info_oficial = indice_oficial.get(ruta_relativa, {})
    extra.update({
        k: v for k, v in info_oficial.items()
        if v and k in CAMPOS_INDICE_A_EXTRA
    })

    return {
        "doc_id": doc_id,
        "fuente": ruta_relativa,
        "nombre_archivo": ruta.name,
        "formato": formato,
        "fenomeno": fenomeno,
        "lang": None,      # se llena en el Día 2
        "title": titulo,
        "text": normalize_text(texto),
        "extra": extra,
    }


# ---------------------------------------------------------------------------
# Recorrido del corpus
# ---------------------------------------------------------------------------

def ingestar(
    raiz_corpus: str,
    salida: str,
    filtro_formato: str | None = None,
    usar_ocr: bool = True,
    dedup_pbf: bool = True,
    max_tiles_por_feature: int | None = None,
) -> None:
    raiz = Path(raiz_corpus)
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)

    ruta_indice = raiz / "Indice_Datos_Codefest.xlsx"
    indice_oficial = {}
    if ruta_indice.exists():
        try:
            indice_oficial = cargar_indice_oficial(ruta_indice)
            print(f"Índice oficial cargado: {len(indice_oficial)} archivos registrados\n")
        except Exception as e:  # noqa: BLE001
            print(f"[AVISO] no se pudo cargar el índice oficial: {e}\n")

    # Deduplicación de tiles PBF: se calcula UNA vez, antes del bucle.
    asignacion_pbf = None
    if dedup_pbf and any(raiz.rglob("*.pbf")):
        asignacion_pbf = construir_asignacion(
            raiz, max_tiles_por_feature=max_tiles_por_feature
        )
        print()

    # sorted() es obligatorio: sin él el orden depende del sistema de archivos
    # y el JSONL sale distinto en cada máquina. Reproducibilidad = eliminación.
    archivos = sorted(p for p in raiz.rglob("*") if p.is_file())

    procesados = 0
    por_formato = Counter()
    por_fenomeno = Counter()
    excluidos = []
    basura_so = 0
    omitidos_de_A = 0
    descartados_ruido = []
    fallos = []
    vistos = {}          # doc_id -> fuente
    fuentes_de_B = set()  # toda ruta que B ha intentado procesar

    with salida.open("w", encoding="utf-8") as f:
        for ruta in archivos:
            ruta_relativa = ruta.relative_to(raiz).as_posix()

            exclusion = debe_excluirse(ruta)
            if exclusion:
                motivo, silencioso = exclusion
                if silencioso:
                    basura_so += 1
                else:
                    excluidos.append((ruta_relativa, motivo))
                continue

            try:
                formato = infer_formato(ruta_relativa)
            except ValueError as e:
                fallos.append((ruta_relativa, f"extensión desconocida: {e}"))
                continue

            if formato not in FORMATOS_DE_B:
                omitidos_de_A += 1
                continue

            if filtro_formato and formato != filtro_formato:
                continue

            fuentes_de_B.add(ruta_relativa)

            if formato == "imagen" and not usar_ocr:
                descartados_ruido.append((ruta_relativa, "OCR desactivado"))
                continue

            try:
                doc = construir_documento(
                    ruta, raiz, formato, indice_oficial, usar_ocr, asignacion_pbf
                )
            except ValueError as e:
                fallos.append((ruta_relativa, str(e)))
                continue
            except Exception as e:  # noqa: BLE001
                fallos.append(
                    (ruta_relativa, f"error inesperado: {type(e).__name__}: {e}")
                )
                continue

            # Filtro de ruido: OCR pobre.
            umbral = MIN_PALABRAS_POR_FORMATO.get(formato, 0)
            if umbral and not text_is_usable(doc["text"], umbral):
                descartados_ruido.append(
                    (ruta_relativa, f"menos de {umbral} palabras útiles ({formato})")
                )
                continue

            if doc["doc_id"] in vistos:
                print(f"[DUPLICADO] {ruta_relativa}")
                print(f"            mismo contenido que {vistos[doc['doc_id']]}")
            else:
                vistos[doc["doc_id"]] = ruta_relativa

            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            procesados += 1
            por_formato[formato] += 1
            por_fenomeno[doc["fenomeno"]] += 1

    # -----------------------------------------------------------------------
    # Reporte
    # -----------------------------------------------------------------------
    print(f"\n{'=' * 62}")
    print(f"INGESTA DE DATOS (B) — {raiz}")
    print(f"{'=' * 62}")
    print(f"Procesados          : {procesados}")
    print(f"Omitidos (para A)   : {omitidos_de_A}")
    print(f"Excluidos (metadata): {len(excluidos)}")
    print(f"Basura del SO       : {basura_so}  (.DS_Store, Thumbs.db...)")
    print(f"Descartados (ruido) : {len(descartados_ruido)}")
    print(f"Fallos              : {len(fallos)}")
    print(f"doc_id únicos       : {len(vistos)}")
    print(f"Salida              : {salida}")

    if por_formato:
        print("\nPor formato:")
        for formato, n in sorted(por_formato.items()):
            print(f"  {formato:<10} {n:>5}")
    if por_fenomeno:
        print("Por fenómeno:")
        for fenomeno, n in sorted(por_fenomeno.items()):
            print(f"  F{fenomeno}  {n:>5}")

    if excluidos:
        print(f"\nExcluidos deliberadamente:")
        for r, m in excluidos:
            print(f"  {r}\n    -> {m}")

    if descartados_ruido:
        print(f"\nDescartados por bajo contenido (primeros 5):")
        for r, m in descartados_ruido[:5]:
            print(f"  {r}\n    -> {m}")
        if len(descartados_ruido) > 5:
            print(f"  ... y {len(descartados_ruido) - 5} más")

    if fallos:
        print(f"\nFallos (primeros 10):")
        for r, m in fallos[:10]:
            print(f"  {r}\n    -> {m}")
        if len(fallos) > 10:
            print(f"  ... y {len(fallos) - 10} más")

    # Logs completos: alimentan el informe técnico.
    log = salida.parent / "fallos_ingesta_data.jsonl"
    with log.open("w", encoding="utf-8") as f:
        for r, m in fallos:
            f.write(json.dumps({"fuente": r, "tipo": "fallo", "motivo": m},
                               ensure_ascii=False) + "\n")
        for r, m in descartados_ruido:
            f.write(json.dumps({"fuente": r, "tipo": "descarte", "motivo": m},
                               ensure_ascii=False) + "\n")
        for r, m in excluidos:
            f.write(json.dumps({"fuente": r, "tipo": "excluido", "motivo": m},
                               ensure_ascii=False) + "\n")
    print(f"\nLog completo: {log}")

    # -----------------------------------------------------------------------
    # Verificación de completitud
    #
    # Se compara SOLO contra los archivos del inventario que le tocan a B. El
    # total de 1.826 incluye los pdf de A y compararse contra él no dice nada.
    # -----------------------------------------------------------------------
    if indice_oficial:
        del_indice_para_B = set()
        formato_desconocido = 0
        for ruta_rel in indice_oficial:
            try:
                if infer_formato(ruta_rel) in FORMATOS_DE_B:
                    del_indice_para_B.add(ruta_rel)
            except ValueError:
                formato_desconocido += 1

        indice_de_B = {k: indice_oficial[k] for k in del_indice_para_B}
        r = comparar_con_ingesta(indice_de_B, fuentes_de_B)

        print(f"\nVerificación de completitud (solo formatos de B):")
        print(f"  En el inventario oficial : {r['total_indice']}")
        print(f"  Vistos por esta ingesta  : {r['total_vistas']}")
        print(f"  Cubiertos                : {r['cubiertas']}")
        print(f"  EN EL ÍNDICE Y NO VISTOS : {len(r['faltan_por_ingerir'])}"
              f"   <- deben ser 0")
        print(f"  Vistos y no en el índice : {len(r['no_en_indice'])}")
        if formato_desconocido:
            print(f"  (filas del índice con extensión no reconocida: {formato_desconocido})")

        for ruta_rel in r["faltan_por_ingerir"][:10]:
            print(f"    FALTA  {ruta_rel}")
        for ruta_rel in r["no_en_indice"][:10]:
            print(f"    EXTRA  {ruta_rel}")
        print(f"  Inventario completo (todos los formatos): {len(indice_oficial)}")
        print("  -> Cuadrar los pdf/csv contra la ingesta de A cuando esté lista.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("raiz", help="raíz del corpus, ej. corpus_original")
    ap.add_argument("salida", help="ej. data/documents_data.jsonl")
    ap.add_argument("--formato", choices=sorted(FORMATOS_DE_B), default=None,
                    help="procesar solo un formato (útil para depurar)")
    ap.add_argument("--sin-dedup-pbf", action="store_true",
                    help="no deduplicar features entre niveles de zoom")
    ap.add_argument("--max-tiles-por-feature", type=int, default=None,
                    help="acota en cuántas teselas del mismo zoom se emite un "
                         "feature; las que queden sin features reciben resumen. "
                         "Sin valor = sin cota. Palanca para el A/B del Día 5.")
    ap.add_argument("--sin-ocr", action="store_true",
                    help="saltar imágenes (si Tesseract no está instalado)")
    args = ap.parse_args()
    ingestar(args.raiz, args.salida, args.formato,
             usar_ocr=not args.sin_ocr, dedup_pbf=not args.sin_dedup_pbf,
             max_tiles_por_feature=args.max_tiles_por_feature)