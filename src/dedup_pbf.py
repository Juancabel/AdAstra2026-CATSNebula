"""
dedup_pbf.py — Deduplicación de features entre niveles de zoom.

El corpus de Amazon_Underworld tiene 73 tiles con 11.906 features, de los
cuales solo 1.409 son únicos: 88% de redundancia. El mismo municipio aparece
hasta 26 veces en distintos niveles de zoom.

Sin deduplicar, una consulta sobre un municipio recuperaría 26 fragmentos
casi idénticos que llenarían el top-10 con la misma información.

TRES DECISIONES SEPARADAS
-------------------------
1. DÓNDE se indexa el feature -> en TODOS los tiles del ZOOM MÁS ALTO donde
   aparece. Zoom alto = área pequeña = pocos municipios por documento, que es
   una unidad de recuperación coherente. Un documento con 1.263 municipios de
   medio continente no lo es: su embedding no significa nada.

   Ojo con "todos": un municipio que cruza el borde entre dos teselas vecinas
   del MISMO zoom aparece en las dos, y las dos son igual de granulares y
   válidas. Desempatar por orden alfabético de ruta vaciaba 25 tiles de zoom 6
   sin ningún criterio que lo justificara, y cada tile vaciado es un `fuente`
   que deja de ser recuperable (§10.2.1 empareja por `fuente`: un documento
   sin texto es un F1@3 perdido si el ground truth lo referencia).

2. QUÉ atributos se usan -> la versión MÁS RICA, venga del zoom que venga.
   La riqueza varía de forma impredecible entre tiles: unos traen
   au_popup_window_* (texto ya redactado para humanos) y otros no. Quedarse
   con la versión pobre solo porque está en el zoom correcto perdería el
   mejor contenido del dataset.

3. QUÉ HACER con los tiles de zoom bajo, cuyos features están todos cubiertos
   por tiles de zoom superior -> emitir un RESUMEN DE COBERTURA: los valores
   distintos de cada atributo, sin repetir municipio por municipio.

   La alternativa (dejarlos vacíos) pierde 21 `fuente`. La alternativa opuesta
   (emitir sus features completos) reintroduce la redundancia 26x que este
   módulo existe para evitar. El resumen conserva el documento y le da un
   texto propio y distinto, así que no compite como casi-duplicado: la
   granularidad del texto pasa a coincidir con la del tile, que es lo que un
   tile de zoom 3 realmente es, una vista general.

Determinismo: no hay desempates arbitrarios; todo orden es alfabético y todos
los conjuntos se ordenan antes de serializar.
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mapeos_json import ATRIBUTOS_PBF_DESCARTADOS

# extractores_datos NO importa dedup_pbf a nivel de módulo (lo hace dentro de
# extraer_pbf), así que esta importación no crea un ciclo. Si alguien sube esa
# importación al principio del otro archivo, este import hay que hacerlo lazy.
from extractores_datos import aplanar_valor

CLAVE_FEATURE = "fid"

# Clave reservada en la asignación de un tile para indicar que ese tile no
# emite features propios sino un resumen de cobertura. extraer_pbf() la busca.
CLAVE_RESUMEN = "__resumen__"

# En un resumen, un atributo con más valores distintos que esto es demasiado
# granular para una vista general (los nombres de municipio, las áreas). Se
# reporta su cardinalidad en vez de enumerarlos: listar 1.263 municipios
# recrearía justo el documento gigante que se quiere evitar.
MAX_VALORES_RESUMEN = 40


def _riqueza(props: dict) -> int:
    """Cuenta atributos con contenido real, ignorando códigos internos."""
    return sum(
        1
        for k, v in props.items()
        if k not in ATRIBUTOS_PBF_DESCARTADOS and v not in (None, "")
    )


def _zoom_de(ruta_relativa: str) -> int:
    """Extrae el zoom de 'tiles/<z>/<x>/<nombre>.pbf'. Devuelve -1 si no aplica."""
    partes = ruta_relativa.split("/")
    if "tiles" not in partes:
        return -1
    idx = partes.index("tiles")
    if idx + 1 >= len(partes):
        return -1
    z = partes[idx + 1]
    return int(z) if z.isdigit() else -1


def _construir_resumen(props_del_tile: list[dict], zoom: int) -> str:
    """
    Construye el resumen de cobertura de un tile cuyos features ya se indexan
    en otro sitio.

    Enumera los valores DISTINTOS de cada atributo. Los atributos con
    demasiados valores distintos (municipios, áreas) se reportan por
    cardinalidad: son el detalle que el tile granular ya cubre.

    Usa las mismas claves que los tiles granulares para que el texto viva en
    una región parecida del espacio de embeddings.
    """
    valores = defaultdict(set)
    for props in props_del_tile:
        for clave, valor in props.items():
            if clave in ATRIBUTOS_PBF_DESCARTADOS or clave == CLAVE_FEATURE:
                continue
            if valor in (None, ""):
                continue
            # aplanar_valor: los popups traen listas con viñetas en varias
            # líneas. Sin aplanar, el valor entra al conjunto con saltos
            # internos y la línea del resumen se parte en huérfanas.
            texto = aplanar_valor(str(valor))
            # Los numéricos puros no describen cobertura, solo miden.
            if not texto or texto.replace(".", "", 1).replace("-", "", 1).isdigit():
                continue
            valores[clave].add(texto)

    lineas = [
        f"resumen_tile: vista general de {len(props_del_tile)} features "
        f"(zoom {zoom}); el detalle por municipio se indexa en los tiles de "
        f"mayor zoom"
    ]
    for clave in sorted(valores):
        distintos = sorted(valores[clave])
        if len(distintos) <= MAX_VALORES_RESUMEN:
            lineas.append(f"{clave}: " + "; ".join(distintos))
        else:
            lineas.append(f"{clave}: {len(distintos)} valores distintos")

    return "\n".join(lineas)


def resolver(tiles: dict[str, list[dict]], max_tiles_por_feature: int | None = None) -> dict:
    """
    Núcleo puro de la deduplicación: sin E/S, testeable con datos sintéticos.

    Args:
        tiles: {ruta_relativa_posix: [props de cada feature del tile]}

    Returns:
        {ruta_relativa_posix: {fid: props}}  para tiles con features propios,
        {ruta_relativa_posix: {CLAVE_RESUMEN: texto}}  para tiles resumidos.
        Todo tile de entrada aparece en la salida: ninguno queda vacío.
    """
    zoom_max = {}       # fid -> zoom máximo donde aparece
    rutas_de = defaultdict(set)  # fid -> rutas de ese zoom máximo
    mejores_props = {}  # fid -> (riqueza, props) mejor versión encontrada

    for ruta_rel in sorted(tiles):
        zoom = _zoom_de(ruta_rel)
        for props in tiles[ruta_rel]:
            fid = props.get(CLAVE_FEATURE)
            if fid is None:
                continue

            # Decisión 1: zoom más alto. Empate de zoom -> TODAS las rutas.
            actual = zoom_max.get(fid)
            if actual is None or zoom > actual:
                zoom_max[fid] = zoom
                rutas_de[fid] = {ruta_rel}
            elif zoom == actual:
                rutas_de[fid].add(ruta_rel)

            # Decisión 2: atributos más ricos, sin importar el zoom.
            r = _riqueza(props)
            mejor = mejores_props.get(fid)
            if mejor is None or r > mejor[0]:
                mejores_props[fid] = (r, props)

    asignacion = defaultdict(dict)
    for fid, rutas in rutas_de.items():
        elegidas = sorted(rutas)
        if max_tiles_por_feature is not None:
            elegidas = elegidas[:max_tiles_por_feature]
        for ruta_rel in elegidas:
            asignacion[ruta_rel][fid] = mejores_props[fid][1]

    # Decisión 3: los tiles sin features propios reciben resumen de cobertura.
    for ruta_rel in sorted(tiles):
        if ruta_rel in asignacion:
            continue
        props_del_tile = [p for p in tiles[ruta_rel] if p.get(CLAVE_FEATURE) is not None]
        if not props_del_tile:
            continue  # tile realmente vacío: no hay nada que resumir
        asignacion[ruta_rel] = {
            CLAVE_RESUMEN: _construir_resumen(props_del_tile, _zoom_de(ruta_rel))
        }

    return dict(asignacion)


def verificar_consistencia_fid(raiz: Path) -> dict:
    """
    Comprueba que un mismo fid describa siempre la misma entidad.

    Si fid fuera un contador por tile en vez de un identificador global,
    deduplicar por fid fusionaría municipios distintos y corrompería el
    corpus en silencio. Esto lo verifica antes de que eso pase.
    """
    import mapbox_vector_tile

    nombres_por_fid = defaultdict(set)

    for ruta in sorted(raiz.rglob("*.pbf")):
        try:
            tile = mapbox_vector_tile.decode(ruta.read_bytes())
        except Exception:  # noqa: BLE001
            continue
        for capa in tile.values():
            for feature in capa.get("features", []):
                props = feature.get("properties", {}) or {}
                fid = props.get(CLAVE_FEATURE)
                if fid is None:
                    continue
                nombre = (
                    props.get("b_adm2_geral")
                    or props.get("b_ADM2_PT")
                    or props.get("au_level2")
                )
                if nombre:
                    nombres_por_fid[fid].add(str(nombre))

    conflictos = [
        (fid, sorted(n)) for fid, n in nombres_por_fid.items() if len(n) > 1
    ]
    return {
        "consistente": not conflictos,
        "conflictos": conflictos,
        "total_fids": len(nombres_por_fid),
    }


def leer_tiles(raiz: Path) -> dict[str, list[dict]]:
    """Decodifica todos los .pbf bajo raiz -> {ruta_relativa: [props, ...]}."""
    import mapbox_vector_tile

    tiles = {}
    for ruta in sorted(raiz.rglob("*.pbf")):
        ruta_rel = ruta.relative_to(raiz).as_posix()
        try:
            tile = mapbox_vector_tile.decode(ruta.read_bytes())
        except Exception:  # noqa: BLE001
            tiles[ruta_rel] = []
            continue
        props = [
            f.get("properties", {}) or {}
            for capa in tile.values()
            for f in capa.get("features", [])
        ]
        tiles[ruta_rel] = props
    return tiles


def construir_asignacion(raiz: Path, verbose: bool = True,
                         max_tiles_por_feature: int | None = None) -> dict:
    """
    Decide, para cada feature, en qué tile(s) se indexa y con qué atributos.

    Returns:
        {ruta_relativa_posix: {fid: props}} o {ruta: {CLAVE_RESUMEN: texto}}
    """
    tiles = leer_tiles(raiz)
    asignacion = resolver(tiles, max_tiles_por_feature)

    if verbose:
        con_features = {r: v for r, v in asignacion.items() if CLAVE_RESUMEN not in v}
        con_resumen = {r: v for r, v in asignacion.items() if CLAVE_RESUMEN in v}
        total_features = sum(len(v) for v in tiles.values())
        fids_unicos = len({p.get(CLAVE_FEATURE) for v in tiles.values()
                           for p in v if p.get(CLAVE_FEATURE) is not None})
        emitidos = sum(len(v) for v in con_features.values())
        tamanos = sorted(len(v) for v in con_features.values())

        print(f"{'=' * 62}")
        print("DEDUPLICACIÓN DE TILES PBF")
        print(f"{'=' * 62}")
        print(f"Tiles analizados       : {len(tiles)}")
        print(f"Features totales       : {total_features}")
        print(f"Features únicos        : {fids_unicos}")
        print(f"Features emitidos      : {emitidos}"
              f"  (>{fids_unicos} por municipios que cruzan borde de tesela)")
        if total_features:
            red = 100 * (1 - emitidos / total_features)
            print(f"Redundancia eliminada  : {red:.1f}%")
        print(f"Tiles con features     : {len(con_features)}")
        print(f"Tiles con resumen      : {len(con_resumen)}")
        print(f"Tiles sin nada         : {len(tiles) - len(asignacion)}"
              f"   (deben ser 0: cada tile es un `fuente` recuperable)")
        if tamanos:
            print(
                f"Features por tile      : mín {tamanos[0]}, "
                f"mediana {tamanos[len(tamanos) // 2]}, máx {tamanos[-1]}"
            )
            por_zoom = defaultdict(int)
            for ruta_rel, fids in con_features.items():
                por_zoom[_zoom_de(ruta_rel)] += len(fids)
            print("Features indexados por zoom:")
            for z, n in sorted(por_zoom.items()):
                print(f"  zoom {z}: {n}")

    return asignacion


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python src/dedup_pbf.py <raiz_corpus>")
        sys.exit(1)

    raiz = Path(sys.argv[1])

    print("Verificando consistencia de fid...\n")
    chequeo = verificar_consistencia_fid(raiz)
    if chequeo["consistente"]:
        print(
            f"OK — los {chequeo['total_fids']} fid son consistentes: "
            f"un fid siempre describe la misma entidad.\n"
        )
    else:
        print(
            f"[ERROR] {len(chequeo['conflictos'])} fid describen entidades "
            f"distintas en tiles distintos."
        )
        print("        NO deduplicar por fid: fusionaría municipios diferentes.")
        for fid, nombres in chequeo["conflictos"][:5]:
            print(f"          fid={fid}: {nombres}")
        sys.exit(1)

    construir_asignacion(raiz)