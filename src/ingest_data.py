"""
ingest_data.py — Ingesta del corpus completo. Orquestador único del equipo.

Los ocho formatos del reto: json, pdf, pbf, csv, jpg, xlsx, avif, txt.

Recorre el corpus, extrae el texto de cada archivo, le asigna el DOC_ID oficial
de ADL y escribe data/documents.jsonl siguiendo el Contrato 1.

IDENTIDAD
    El `doc_id` NO se calcula: se lee de Indice_Datos_Codefest.xlsx, que los
    organizadores confirmaron como clave de emparejamiento de la evaluación.
    Un archivo que no esté en ese inventario no tiene DOC_ID, no es evaluable
    y se excluye — nunca se le inventa un id.

NADA SE DESCARTA EN SILENCIO
    Todo archivo del inventario acaba en el JSONL, aunque su texto salga vacío
    (imágenes sin texto legible, un JSON que es una lista vacía). Los
    organizadores lo pidieron así: "se conserva su metadata sin inventar
    contenido". Descartarlos costaría 9 DOC_ID evaluables de 1.826.

Uso:
    python src/ingest_data.py corpus_original data/documents.jsonl
    python src/ingest_data.py corpus_original data/documents.jsonl --formato json
    python src/ingest_data.py corpus_original data/documents.jsonl --sin-ocr
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus_paths import FORMATOS_VALIDOS, infer_fenomeno, infer_formato
from dedup_pbf import construir_asignacion
from extractores_datos import (
    extraer_csv,
    extraer_imagen,
    extraer_json,
    extraer_pbf,
    extraer_xlsx,
    extraer_pdf,
    extraer_txt,
)
from identity import compute_content_sha1, normalize_text, text_is_usable
from indice_oficial import cargar_indice_oficial, comparar_con_ingesta
from mapeos_json import (
    ARCHIVOS_EXCLUIDOS,
    BASURA_SISTEMA,
    PREFIJO_BLOQUEO_EXCEL,
)

# Los ocho formatos del reto. Una sola corrida los cubre todos: ya no hay
# reparto entre ramas, y `--formato` es solo una ayuda para depurar.
FORMATOS_SOPORTADOS = set(FORMATOS_VALIDOS)

# Formatos que son imagen y pasan por OCR.
FORMATOS_IMAGEN = {"jpg", "avif"}

# Campos del índice oficial que se copian a `extra`. `doc_id_oficial` ya no va
# aquí: es el `doc_id` del documento, no metadata suelta.
CAMPOS_INDICE_A_EXTRA = ("observatorio", "codigo_observatorio")

# Aviso de bajo contenido POR FORMATO. Ya NO descarta: solo anota en el log.
#
# Antes esto era un filtro con `continue`, y tenía sentido cuando la identidad
# era un hash del texto: un documento sin texto no podía recibir id. Con el
# DOC_ID oficial la aritmética se invierte — cada descarte es una fila del
# inventario que desaparece del entregable y que el ground truth puede estar
# referenciando. Se emiten todos y se revisa el aviso a mano.
MIN_PALABRAS_AVISO = {
    "jpg": 20,
    "avif": 20,
}

# Umbral para avisar de un PDF sospechosamente corto. Un informe de 40 páginas
# que rinde 30 palabras no falla: devuelve basura silenciosamente.
MIN_PALABRAS_PDF_POR_PAGINA = 10


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

def fenomeno_entero(valor, ruta_relativa: str) -> int:
    """
    Convierte el 'F1'/'F2'/'F3' del índice oficial al entero del Contrato 1.

    El contrato pide un ENTERO (1, 2 o 3), no la etiqueta del Excel. Si el
    índice no trae el dato, se cae a inferirlo de la ruta.
    """
    texto = str(valor or "").strip().upper()
    if texto.startswith("F") and texto[1:].isdigit():
        return int(texto[1:])
    if texto.isdigit():
        return int(texto)
    return infer_fenomeno(ruta_relativa)


def construir_documento(
    ruta: Path,
    raiz: Path,
    formato: str,
    info_oficial: dict,
    usar_ocr: bool = True,
    asignacion_pbf: dict | None = None,
) -> dict:
    """
    Convierte un archivo del corpus en un objeto del Contrato 1.

    `info_oficial` es la fila del inventario de ADL correspondiente a este
    archivo. El llamador ya garantizó que existe: un archivo sin fila no tiene
    DOC_ID y no debe llegar hasta aquí.

    A diferencia de la versión anterior, un texto vacío NO es un error: el
    documento se emite igual con su DOC_ID y su metadata.

    Raises:
        ValueError: fenómeno indeterminable, institución sin mapeo, o fallo
            del extractor.
    """
    ruta_relativa = ruta.relative_to(raiz).as_posix()
    institucion = institucion_de(ruta_relativa)

    if formato == "json":
        texto, titulo, extra = extraer_json(ruta, institucion)
    elif formato == "xlsx":
        texto, titulo, extra = extraer_xlsx(ruta)
    elif formato == "csv":
        texto, titulo, extra = extraer_csv(ruta)
    elif formato in FORMATOS_IMAGEN:
        if not usar_ocr:
            raise ValueError("OCR desactivado con --sin-ocr")
        texto, titulo, extra = extraer_imagen(ruta)
    elif formato == "pdf":
        # usar_ocr también gobierna el fallback de los PDF escaneados, no solo
        # las imágenes: con --sin-ocr esos 51 salen con su texto vacío.
        texto, titulo, extra = extraer_pdf(ruta, usar_ocr=usar_ocr)
    elif formato == "txt":
        texto, titulo, extra = extraer_txt(ruta)
    elif formato == "pbf":
        asignados = None
        if asignacion_pbf is not None:
            asignados = asignacion_pbf.get(ruta_relativa, {})
        texto, titulo, extra = extraer_pbf(ruta, ruta_relativa, asignados)
    else:
        raise ValueError(f"formato sin extractor: {formato}")

    texto_normalizado = normalize_text(texto)

    # La huella de contenido baja a `extra`: ya no es identidad, pero sigue
    # delatando documentos idénticos con DOC_ID distintos (CEOBS x8, SWF x1).
    # Es None cuando no hay texto, para no dar a todos los vacíos la misma.
    sha1 = compute_content_sha1(texto_normalizado)
    if sha1:
        extra["content_sha1"] = sha1

    # Metadata del índice oficial de ADL.
    extra.update({
        k: v for k, v in info_oficial.items()
        if v and k in CAMPOS_INDICE_A_EXTRA
    })

    return {
        # El DOC_ID de ADL, ej. "F1-AIINDEX-001". Leído, nunca calculado.
        "doc_id": info_oficial["doc_id_oficial"],
        "fuente": ruta_relativa,
        "nombre_archivo": ruta.name,
        "formato": formato,
        "fenomeno": fenomeno_entero(info_oficial.get("fenomeno_indice"), ruta_relativa),
        "lang": None,      # lo llena detectar_idioma.py sobre el JSONL fusionado
        "title": titulo,
        "text": texto_normalizado,
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

    # El índice oficial se carga UNA vez y sin red de seguridad. Antes esto
    # estaba envuelto en try/except y caía a {}: con el diccionario vacío
    # ningún documento recibía doc_id y la verificación de completitud se
    # saltaba entera sin imprimir nada. Un corpus sin identidad daba "todo
    # correcto". Si el índice no carga, aquí se rompe la corrida.
    ruta_indice = raiz / "Indice_Datos_Codefest.xlsx"
    indice_oficial = cargar_indice_oficial(ruta_indice)
    print(f"Índice oficial cargado: {len(indice_oficial)} archivos registrados\n")

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
    total_archivos = len(archivos)
    print(f"Archivos a examinar: {total_archivos}\n")

    por_formato = Counter()
    por_fenomeno = Counter()
    excluidos = []
    basura_so = 0
    fuera_del_indice = []
    avisos = []
    fallos = []
    vistos = {}          # doc_id -> fuente
    fuentes_vistas = set()  # toda ruta que la ingesta ha intentado procesar

    # Los documentos se acumulan y se escriben ordenados por doc_id al final:
    # el orden de emisión tiene que ser determinista e independiente del orden
    # del sistema de archivos. Son ~1.826 objetos, cabe de sobra en memoria.
    documentos = []

    # Progreso en vivo. El OCR de los PDF escaneados tarda ~45 min y sin esto
    # la terminal se queda muda todo ese rato: no se distingue "trabajando" de
    # "colgado".
    #
    # Va a stdout, NO a stderr. En PowerShell 5.1, si el llamador redirige
    # stderr de un ejecutable nativo con 2>&1, cada línea escrita ahí se
    # convierte en un NativeCommandError y con $ErrorActionPreference='Stop'
    # aborta el script entero. El progreso no es un error y no debe viajar
    # por el canal de errores.
    t_inicio = time.time()

    for n_examinado, ruta in enumerate(archivos, 1):
        ruta_relativa = ruta.relative_to(raiz).as_posix()

        if n_examinado % 100 == 0 or n_examinado == total_archivos:
            transcurrido = time.time() - t_inicio
            print(f"  [{n_examinado:>5}/{total_archivos}] {len(documentos)} "
                  f"emitidos · {transcurrido / 60:.1f} min", flush=True)

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

        if formato not in FORMATOS_SOPORTADOS:
            fallos.append((ruta_relativa, f"formato sin extractor: {formato}"))
            continue

        if filtro_formato and formato != filtro_formato:
            continue

        fuentes_vistas.add(ruta_relativa)

        # Sin fila en el inventario no hay DOC_ID, y el doc_id NO se inventa:
        # el documento se excluye y queda listado en el reporte.
        info_oficial = indice_oficial.get(ruta_relativa)
        if info_oficial is None:
            fuera_del_indice.append(ruta_relativa)
            continue

        if formato in FORMATOS_IMAGEN and not usar_ocr:
            fallos.append((ruta_relativa, "OCR desactivado con --sin-ocr"))
            continue

        try:
            doc = construir_documento(
                ruta, raiz, formato, info_oficial, usar_ocr, asignacion_pbf
            )
        except ValueError as e:
            fallos.append((ruta_relativa, str(e)))
            continue
        except Exception as e:  # noqa: BLE001
            fallos.append(
                (ruta_relativa, f"error inesperado: {type(e).__name__}: {e}")
            )
            continue

        # ------------------------------------------------------------------
        # Avisos. Ninguno descarta el documento: solo lo anotan en el log.
        # ------------------------------------------------------------------
        umbral = MIN_PALABRAS_AVISO.get(formato, 0)
        if umbral and not text_is_usable(doc["text"], umbral):
            avisos.append(
                (ruta_relativa,
                 f"menos de {umbral} palabras útiles ({formato}); se emite con "
                 f"su metadata y sin inventar contenido")
            )

        if not doc["text"].strip():
            avisos.append((ruta_relativa, "texto vacío: se emite solo la metadata"))

        # El fallback de PyMuPDF a PyPDF2 era invisible en el JSONL.
        if doc["extra"].get("motor_pdf") == "pypdf2":
            avisos.append(
                (ruta_relativa,
                 f"PDF extraído con PyPDF2 tras fallar PyMuPDF: "
                 f"{doc['extra'].get('aviso_pdf')}")
            )

        # Un PDF escaneado que pasó por OCR: el texto es reconocido, no leído.
        if doc["extra"].get("motor_pdf") == "ocr":
            # Son los 51 documentos que se llevan el 95% del tiempo de corrida:
            # conviene verlos caer uno a uno en vez de esperar a ciegas.
            print(f"    OCR  {ruta.name}  ({doc['extra'].get('paginas')} págs, "
                  f"{len(doc['text'].split())} palabras reconocidas)", flush=True)
            avisos.append(
                (ruta_relativa,
                 f"PDF escaneado: texto obtenido por OCR a "
                 f"{doc['extra'].get('ocr_dpi')} DPI "
                 f"(la capa de texto traía {doc['extra'].get('caracteres_capa_texto')} "
                 f"caracteres en {doc['extra'].get('paginas')} páginas)")
            )

        # Un PDF largo que rinde poquísimo texto suele ser un escaneado sin
        # capa de texto: no falla, devuelve basura corta.
        paginas = doc["extra"].get("paginas") or 0
        if formato == "pdf" and paginas:
            palabras = len(doc["text"].split())
            if palabras < paginas * MIN_PALABRAS_PDF_POR_PAGINA:
                avisos.append(
                    (ruta_relativa,
                     f"PDF con {palabras} palabras en {paginas} páginas "
                     f"(<{MIN_PALABRAS_PDF_POR_PAGINA}/pág.): ¿escaneado sin "
                     f"capa de texto?")
                )

        if doc["doc_id"] in vistos:
            # Imposible con DOC_ID oficiales únicos; si pasa, el índice mintió.
            raise ValueError(
                f"doc_id repetido {doc['doc_id']}: {ruta_relativa} y "
                f"{vistos[doc['doc_id']]}"
            )
        vistos[doc["doc_id"]] = ruta_relativa

        documentos.append(doc)
        por_formato[formato] += 1
        por_fenomeno[doc["fenomeno"]] += 1

    # Orden determinista de emisión: por doc_id, no por orden de recorrido.
    documentos.sort(key=lambda d: d["doc_id"])
    with salida.open("w", encoding="utf-8", newline="\n") as f:
        for doc in documentos:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    procesados = len(documentos)

    # -----------------------------------------------------------------------
    # Reporte
    # -----------------------------------------------------------------------
    print(f"\n{'=' * 62}")
    print(f"INGESTA DEL CORPUS — {raiz}")
    print(f"{'=' * 62}")
    print(f"Procesados          : {procesados}")
    print(f"Excluidos (sin DOC_ID): {len(excluidos)}")
    print(f"Basura del SO       : {basura_so}  (.DS_Store, Thumbs.db...)")
    print(f"Fuera del índice    : {len(fuera_del_indice)}")
    print(f"Avisos              : {len(avisos)}")
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
        print("\nExcluidos deliberadamente:")
        for r, m in excluidos:
            print(f"  {r}\n    -> {m}")

    if fuera_del_indice:
        print("\nVistos pero SIN fila en el inventario (sin DOC_ID, excluidos):")
        for r in fuera_del_indice:
            print(f"  {r}")

    if avisos:
        print("\nAvisos (el documento SÍ se emitió):")
        for r, m in avisos:
            print(f"  {r}\n    -> {m}")

    if fallos:
        print("\nFallos (primeros 10):")
        for r, m in fallos[:10]:
            print(f"  {r}\n    -> {m}")
        if len(fallos) > 10:
            print(f"  ... y {len(fallos) - 10} más")

    # Logs completos: alimentan el informe técnico.
    log = salida.parent / "fallos_ingesta_data.jsonl"
    with log.open("w", encoding="utf-8", newline="\n") as f:
        for r, m in sorted(fallos):
            f.write(json.dumps({"fuente": r, "tipo": "fallo", "motivo": m},
                               ensure_ascii=False) + "\n")
        for r, m in sorted(avisos):
            f.write(json.dumps({"fuente": r, "tipo": "aviso", "motivo": m},
                               ensure_ascii=False) + "\n")
        for r, m in sorted(excluidos):
            f.write(json.dumps({"fuente": r, "tipo": "excluido", "motivo": m},
                               ensure_ascii=False) + "\n")
        for r in sorted(fuera_del_indice):
            f.write(json.dumps({"fuente": r, "tipo": "fuera_del_indice",
                                "motivo": "no figura en el inventario oficial"},
                               ensure_ascii=False) + "\n")
    print(f"\nLog completo: {log}")

    # -----------------------------------------------------------------------
    # Verificación de completitud — BLOQUEANTE
    #
    # Ya no se compara contra un subconjunto por rama: una sola corrida cubre
    # los ocho formatos, así que el patrón de medida es el inventario entero.
    # Y ya no es informativa: sin DOC_ID no hay evaluación posible, de modo que
    # un hueco no justificado tiene que romper la corrida en vez de quedar en
    # una línea del reporte que nadie lee.
    # -----------------------------------------------------------------------
    r = comparar_con_ingesta(indice_oficial, fuentes_vistas)

    print("\nVerificación de completitud (inventario completo):")
    print(f"  En el inventario oficial : {r['total_indice']}")
    print(f"  Vistos por esta ingesta  : {r['total_vistas']}")
    print(f"  Cubiertos                : {r['cubiertas']}")
    print(f"  EN EL ÍNDICE Y NO VISTOS : {len(r['faltan_por_ingerir'])}"
          f"   <- deben ser 0")
    print(f"  Vistos y no en el índice : {len(r['no_en_indice'])}")

    for ruta_rel in r["faltan_por_ingerir"][:20]:
        print(f"    FALTA  {ruta_rel}")
    for ruta_rel in r["no_en_indice"][:20]:
        print(f"    EXTRA  {ruta_rel}")

    # Con --formato la cobertura parcial es esperada: no se bloquea.
    if filtro_formato:
        print(f"\n  (corrida parcial con --formato {filtro_formato}: "
              f"no se verifica la cobertura total)")
        return

    problemas = []
    if r["faltan_por_ingerir"]:
        problemas.append(
            f"{len(r['faltan_por_ingerir'])} archivos del inventario no se "
            f"ingirieron"
        )
    emitidos = {d["fuente"] for d in documentos}
    del_indice_sin_emitir = sorted(set(indice_oficial) - emitidos)
    if del_indice_sin_emitir:
        problemas.append(
            f"{len(del_indice_sin_emitir)} archivos del inventario se vieron "
            f"pero no llegaron al JSONL: {del_indice_sin_emitir[:5]}"
        )
    if procesados != len(indice_oficial):
        problemas.append(
            f"se emitieron {procesados} documentos y el inventario tiene "
            f"{len(indice_oficial)}"
        )

    if problemas:
        raise SystemExit(
            "\nINGESTA INCOMPLETA — no se puede dar por buena:\n  - "
            + "\n  - ".join(problemas)
            + f"\n\nDetalle en {log}"
        )

    print(f"\n  OK: los {procesados} documentos del inventario están en el JSONL.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("raiz", help="raíz del corpus, ej. corpus_original")
    ap.add_argument("salida", help="ej. data/documents.jsonl")
    ap.add_argument("--formato", choices=sorted(FORMATOS_SOPORTADOS), default=None,
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