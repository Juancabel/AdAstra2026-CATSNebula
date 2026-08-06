# -*- coding: utf-8 -*-
"""
Deteccion de idioma por documento — CODEFEST Ad Astra 2026.

Cumple el §2.2 del spec ("Deteccion y marcado del idioma predominante del
documento"). Se ejecuta sobre el documents.jsonl fusionado (A + B).

CONTRATO: anade UN solo campo, `lang` (ISO 639-1, o 'und'). Nada mas.
Los diagnosticos van a un reporte aparte que no entra al pipeline.

Tres decisiones de diseno:

1. DETERMINISTA. py3langid no muestrea. langdetect SI: sobre 200 corridas del
   mismo texto ("UNOOSA ESA SWF CSIS INPE LEO ASAT") devolvio 'en' 99 veces y
   'pt' 101. Con reproducibilidad como criterio de eliminacion, no vale la pena.
2. SIN LISTA BLANCA. El corpus tiene al menos un documento en chino. Restringido
   a {es,en,pt}, ese documento sale 'en' con conf=0.998. Confiadamente erroneo.
3. LOS VOLCADOS "campo: valor" (PBF/XLSX/CSV) SE MARCAN 'und' POR REGLA, no por
   umbral: un volcado PBF clasifica como 'es' con conf=1.000, por encima de
   articulos cortos legitimos. Ningun umbral los separa.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from py3langid.langid import MODEL_FILE, LanguageIdentifier

DETECTOR_VERSION = "1.1.0"

FORMATOS_SIN_PROSA = frozenset({"pbf", "xlsx", "csv"})
MIN_CARACTERES = 25
TAM_BLOQUE = 800
UMBRAL_MIXTO = 0.85          # solo para el reporte de QA, no para el campo lang
UMBRAL_CONF_CORTO = 0.50

_IDENTIFICADOR = LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)


def _segmentar(texto: str) -> list[str]:
    """Parte el texto en bloques de ~TAM_BLOQUE caracteres en limites de parrafo.

    Se segmenta aunque el resultado sea un solo codigo: en un informe en espanol
    con anexo en ingles, la mayoria ponderada por caracteres da la etiqueta
    correcta, mientras que clasificar la cadena entera de una vez es mas fragil.
    """
    parrafos = [p for p in re.split(r"\n\s*\n", texto) if p.strip()]
    if not parrafos:
        return [texto] if texto.strip() else []
    bloques, actual, largo = [], [], 0
    for parrafo in parrafos:
        actual.append(parrafo)
        largo += len(parrafo)
        if largo >= TAM_BLOQUE:
            bloques.append("\n\n".join(actual))
            actual, largo = [], 0
    if actual:
        bloques.append("\n\n".join(actual))
    return bloques


def _proporcion_alfabetica(texto: str) -> float:
    if not texto:
        return 0.0
    return sum(1 for c in texto if unicodedata.category(c).startswith("L")) / len(texto)


def _analizar(texto: str, formato: str | None) -> tuple[str, dict]:
    """Devuelve (lang, diagnostico). El diagnostico NO va al documents.jsonl."""
    if formato and formato.lower() in FORMATOS_SIN_PROSA:
        return "und", {"motivo": f"formato_sin_prosa:{formato.lower()}"}

    texto = (texto or "").strip()
    if len(texto) < MIN_CARACTERES:
        return "und", {"motivo": "texto_demasiado_corto"}
    if _proporcion_alfabetica(texto) < 0.30:
        return "und", {"motivo": "texto_no_alfabetico"}

    conteo: dict[str, int] = {}
    for bloque in _segmentar(texto):
        if len(bloque) < MIN_CARACTERES:
            continue
        codigo, _ = _IDENTIFICADOR.classify(bloque)
        conteo[codigo] = conteo.get(codigo, 0) + len(bloque)
    if not conteo:
        return "und", {"motivo": "sin_bloques_analizables"}

    total = sum(conteo.values())
    reparto = {k: v / total for k, v in sorted(conteo.items(), key=lambda kv: -kv[1])}
    principal, cobertura = next(iter(reparto.items()))

    # En textos cortos hay un solo bloque: la cobertura siempre da 1.0 y no
    # informa. Ahi si vale mirar la confianza del clasificador.
    if len(texto) < 200:
        _, conf = _IDENTIFICADOR.classify(texto)
        if conf < UMBRAL_CONF_CORTO:
            return "und", {"motivo": f"confianza_baja_texto_corto:{conf:.3f}"}

    diag = {"cobertura": round(cobertura, 4)}
    if cobertura < UMBRAL_MIXTO:
        diag["mixto"] = {k: round(v, 4) for k, v in reparto.items()}
    return principal, diag


def detectar_idioma(texto: str, formato: str | None = None) -> str:
    """Idioma predominante como codigo ISO 639-1, o 'und'."""
    return _analizar(texto, formato)[0]


def anotar_jsonl(entrada: Path, salida: Path, reporte: Path | None = None) -> dict:
    """Anade SOLO el campo `lang`. Los diagnosticos van al reporte aparte."""
    resumen = {
        "detector_version": DETECTOR_VERSION,
        "total": 0,
        "por_lang": {},
        "und": [],       # doc_id + motivo: revisar a mano
        "mixtos": [],    # doc_id + reparto: posible fallo de extraccion
    }

    with entrada.open(encoding="utf-8") as f_in, salida.open("w", encoding="utf-8") as f_out:
        for linea in f_in:
            linea = linea.strip()
            if not linea:
                continue
            doc = json.loads(linea)
            lang, diag = _analizar(doc.get("text", ""), doc.get("formato"))
            doc["lang"] = lang

            resumen["total"] += 1
            resumen["por_lang"][lang] = resumen["por_lang"].get(lang, 0) + 1
            if lang == "und":
                resumen["und"].append({"doc_id": doc.get("doc_id"),
                                       "fuente": doc.get("fuente"),
                                       "motivo": diag.get("motivo")})
            elif "mixto" in diag:
                resumen["mixtos"].append({"doc_id": doc.get("doc_id"),
                                          "fuente": doc.get("fuente"),
                                          "reparto": diag["mixto"]})

            f_out.write(json.dumps(doc, ensure_ascii=False) + "\n")

    resumen["por_lang"] = dict(sorted(resumen["por_lang"].items(), key=lambda kv: -kv[1]))
    if reporte:
        reporte.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumen


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Detecta idioma en un documents.jsonl")
    ap.add_argument("entrada", type=Path)
    ap.add_argument("salida", type=Path)
    ap.add_argument("--reporte", type=Path, default=None,
                    help="ruta del reporte de QA (no entra al pipeline)")
    a = ap.parse_args()
    r = anotar_jsonl(a.entrada, a.salida, a.reporte)
    print(json.dumps({"total": r["total"], "por_lang": r["por_lang"],
                      "und": len(r["und"]), "mixtos": len(r["mixtos"])},
                     ensure_ascii=False, indent=2))