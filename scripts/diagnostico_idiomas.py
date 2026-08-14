# -*- coding: utf-8 -*-
"""
diagnostico_idiomas.py — Reporte sobre etiquetas `lang` sospechosas. NO TOCA TEXTO.

Dos preguntas del brief de fixes, las dos con el mismo método: reproducir y
medir antes de decidir si hay bug.

  --cirilico  (§2.1) Los documentos marcados lang=ru, ¿son ruso de verdad o
              texto europeo con homoglifos cirílicos sueltos (`Schӧn` con `ӧ`
              cirílica en vez de `ö` latina)? Se cuenta el bloque Unicode de
              cada letra: un texto ruso real es mayoritariamente cirílico; un
              homoglifo son cuatro letras perdidas en un texto latino.

  --mixto     (§2.2) Los documentos con `lang` ambiguo, ¿son bilingües de
              verdad o es ruido de membrete/OCR? Se corre py3langid por
              PÁRRAFO en vez de por documento, que es justo lo que la
              detección global no puede ver.

La salida es para que Jade decida caso por caso o en bloque. El script no
propone ni aplica ninguna corrección.

Uso:
    python scripts/diagnostico_idiomas.py --cirilico
    python scripts/diagnostico_idiomas.py --mixto
    python scripts/diagnostico_idiomas.py --mixto --extracto 300
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOCUMENTS = RAIZ / "data" / "documents.jsonl"

# Un párrafo más corto que esto no le da a py3langid con qué trabajar: la
# detección por debajo de ~40 caracteres es ruido, no señal.
MIN_CARACTERES_PARRAFO = 60

# Por debajo de esta proporción de cirílico, un lang=ru no puede ser ruso real.
UMBRAL_CIRILICO_REAL = 0.20


def bloque(c: str) -> str:
    """'CYRILLIC', 'LATIN', 'ARABIC'… a partir del nombre Unicode del carácter."""
    try:
        return unicodedata.name(c).split()[0]
    except ValueError:
        return "?"


def perfil_alfabetos(texto: str) -> Counter:
    return Counter(bloque(c) for c in texto if c.isalpha())


def documentos(filtro=None):
    with DOCUMENTS.open(encoding="utf-8") as f:
        for linea in f:
            o = json.loads(linea)
            if filtro is None or filtro(o):
                yield o


def informe_cirilico() -> None:
    docs = list(documentos(lambda o: (o.get("lang") or "") == "ru"))
    print(f"Documentos con lang=ru: {len(docs)}\n")

    for o in docs:
        texto = o.get("text") or ""
        perfil = perfil_alfabetos(texto)
        total = sum(perfil.values()) or 1
        cir = perfil.get("CYRILLIC", 0)
        lat = perfil.get("LATIN", 0)
        proporcion = cir / total

        veredicto = ("ruso real" if proporcion >= UMBRAL_CIRILICO_REAL
                     else "HOMOGLIFO: etiqueta ru falsa")
        print(f"{o['doc_id']:<18} {o['formato']:<6} {len(texto):>8} chars  "
              f"cirílico {cir:>6} ({proporcion:>6.2%})  latino {lat:>6}   {veredicto}")
        print(f"  {o['fuente'][-88:]}")

        if proporcion < UMBRAL_CIRILICO_REAL:
            # Las cirílicas concretas, con su contexto: es lo que permite decir
            # si son homoglifos de letras latinas o una cita en ruso.
            casos = Counter()
            for i, c in enumerate(texto):
                if c.isalpha() and bloque(c) == "CYRILLIC":
                    ventana = texto[max(0, i - 12):i + 13].replace("\n", " ")
                    casos[(c, unicodedata.name(c))] += 1
                    if len(casos) <= 6 and casos[(c, unicodedata.name(c))] == 1:
                        print(f"    {c!r}  {unicodedata.name(c)}")
                        print(f"       …{ventana}…")
            print(f"    distintas: {len(casos)}   ocurrencias: {sum(casos.values())}")
        print()

    # La otra mitad de la pregunta: si los lang=ru son ruso de verdad, ¿dónde
    # están los homoglifos? En documentos LATINOS con cuatro cirílicas sueltas,
    # que la detección global nunca marca porque no cambian la etiqueta.
    print("=" * 74)
    print("PORTADORES DE HOMOGLIFOS — texto mayoritariamente latino con cirílicas sueltas")
    print("=" * 74)
    portadores = []
    for o in documentos():
        texto = o.get("text") or ""
        if len(texto) < 200:
            continue
        perfil = perfil_alfabetos(texto)
        total = sum(perfil.values()) or 1
        cir = perfil.get("CYRILLIC", 0)
        if 0 < cir / total < 0.02 and perfil.get("LATIN", 0) / total > 0.5:
            portadores.append((cir, o, texto))

    portadores.sort(reverse=True, key=lambda t: t[0])
    print(f"documentos afectados: {len(portadores)}\n")
    for cir, o, texto in portadores[:12]:
        letras = Counter(c for c in texto if c.isalpha() and bloque(c) == "CYRILLIC")
        ejemplos = []
        for c in letras:
            i = texto.find(c)
            ejemplos.append(texto[max(0, i - 14):i + 15].replace("\n", " "))
            if len(ejemplos) >= 2:
                break
        print(f"{o['doc_id']:<18} {str(o.get('lang')):<4} {cir:>4} cirílicas  "
              f"{''.join(sorted(letras))[:14]!r}")
        for e in ejemplos:
            print(f"    …{e}…")


def informe_mixto(extracto: int) -> None:
    """
    Los documentos que detectar_idioma.py ya marcó como mixtos.

    Se parte de `data/reporte_idioma.json` en vez de recalcular el reparto por
    nuestra cuenta: si este script usara su propia segmentación, estaría
    diagnosticando un conjunto distinto del que produjo la corrida.

    La aportación es el nivel de detalle que el reporte no da: la clasificación
    PÁRRAFO A PÁRRAFO y un extracto de cada idioma detectado, que es lo que
    distingue un documento bilingüe de verdad de un membrete o una bibliografía
    en otra lengua.
    """
    import py3langid

    reporte = json.loads((RAIZ / "data" / "reporte_idioma.json").read_text(encoding="utf-8"))
    mixtos = {m["doc_id"]: m for m in reporte.get("mixtos", [])}
    print(f"Documentos marcados como mixtos en reporte_idioma.json: {len(mixtos)}\n")

    docs = {o["doc_id"]: o for o in documentos(lambda o: o["doc_id"] in mixtos)}

    print(f"{'doc_id':<18} {'lang':<5} {'fmt':<5} {'párrafos':>9} {'principal':>10}   "
          f"reparto por párrafo")
    detalle = []
    for doc_id in sorted(mixtos):
        o = docs.get(doc_id)
        if o is None:
            print(f"{doc_id:<18} (no está en documents.jsonl)")
            continue
        texto = (o.get("text") or "").strip()
        parrafos = [p.strip() for p in texto.split("\n\n")
                    if len(p.strip()) >= MIN_CARACTERES_PARRAFO]
        if not parrafos:
            parrafos = [texto]

        votos = Counter(py3langid.classify(p)[0] for p in parrafos)
        principal, n = votos.most_common(1)[0]
        reparto = " ".join(f"{k}:{v}" for k, v in votos.most_common(5))
        print(f"{doc_id:<18} {str(o.get('lang')):<5} {str(o.get('formato')):<5} "
              f"{len(parrafos):>9} {principal:>7} {n / len(parrafos):>3.0%}   {reparto}")
        detalle.append((o, votos, parrafos))

    print("\n" + "=" * 78)
    print("EXTRACTOS — para decidir bilingüe real vs. ruido de membrete")
    print("=" * 78)
    for o, votos, parrafos in detalle:
        print(f"\n--- {o['doc_id']}  lang={o.get('lang')}  "
              f"reparto_reporte={mixtos[o['doc_id']]['reparto']} ---")
        print(f"    {o['fuente'][-88:]}")
        vistos = set()
        for p in parrafos:
            idioma = py3langid.classify(p)[0]
            if idioma in vistos:
                continue
            vistos.add(idioma)
            print(f"    [{idioma}] {p[:extracto]!r}")
            if len(vistos) >= 3:
                break


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cirilico", action="store_true", help="§2.1 documentos lang=ru")
    ap.add_argument("--mixto", action="store_true", help="§2.2 lang ambiguo o mixto")
    ap.add_argument("--extracto", type=int, default=200)
    args = ap.parse_args()

    if not (args.cirilico or args.mixto):
        ap.error("elige --cirilico y/o --mixto")
    if args.cirilico:
        informe_cirilico()
    if args.mixto:
        informe_mixto(args.extracto)


if __name__ == "__main__":
    main()
