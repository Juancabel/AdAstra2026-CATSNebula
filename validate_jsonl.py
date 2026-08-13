"""
validate_jsonl.py — Verificación de integridad de un archivo JSONL.

Detecta los problemas que no se ven a simple vista y que rompen el pipeline
en silencio:
  - separadores de línea Unicode (U+2028/U+2029) que json.dumps NO escapa
  - caracteres invisibles de categoría Cf sobrevivientes
  - líneas que no parsean como JSON
  - líneas en blanco
  - campos obligatorios ausentes
  - doc_id o fuente duplicados

Uso:
    python validate_jsonl.py data/documents_data.jsonl
"""

import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

CAMPOS_OBLIGATORIOS = {
    "doc_id", "fuente", "formato", "fenomeno", "text",
}

# Los que json.dumps NO escapa y rompen el conteo de líneas.
SEPARADORES_PELIGROSOS = {0x2028, 0x2029}


def _contar_no_ascii(texto: str) -> dict:
    """Cuenta caracteres no ASCII, ignorando letras acentuadas normales."""
    cuenta = Counter()
    for c in texto:
        if ord(c) < 128:
            continue
        # Las letras con tilde de es/pt/en son normales: no interesan aquí.
        if unicodedata.category(c) in ("Ll", "Lu") and ord(c) < 0x250:
            continue
        cuenta[c] += 1
    return cuenta


def validar(ruta: str) -> int:
    ruta = Path(ruta)
    datos_crudos = ruta.read_bytes()

    print("=" * 62)
    print(f"VALIDACIÓN DE JSONL — {ruta}")
    print("=" * 62)
    print(f"Tamaño: {len(datos_crudos) / 1024 / 1024:.2f} MB")

    problemas = []
    avisos = []

    # -----------------------------------------------------------------------
    # 1. Separadores de línea peligrosos a nivel de bytes
    # -----------------------------------------------------------------------
    texto_completo = datos_crudos.decode("utf-8", errors="replace")
    for cp in SEPARADORES_PELIGROSOS:
        n = texto_completo.count(chr(cp))
        if n:
            problemas.append(
                f"U+{cp:04X} ({unicodedata.name(chr(cp))}) aparece {n} veces. "
                f"json.dumps NO lo escapa: el archivo tiene más 'líneas' que "
                f"objetos. Corregir normalize_text() y reingerir."
            )

    # -----------------------------------------------------------------------
    # 2. Conteo de líneas: splitlines() vs saltos reales
    # -----------------------------------------------------------------------
    n_splitlines = len(texto_completo.splitlines())
    n_saltos = texto_completo.count("\n")
    if n_splitlines != n_saltos:
        problemas.append(
            f"splitlines() cuenta {n_splitlines} líneas pero solo hay "
            f"{n_saltos} saltos '\\n'. Hay separadores Unicode escondidos."
        )

    # -----------------------------------------------------------------------
    # 3. Línea por línea
    # -----------------------------------------------------------------------
    n_lineas = 0
    n_blancas = 0
    doc_ids = Counter()
    fuentes = Counter()
    invisibles = Counter()
    por_formato = Counter()
    sin_campos = []
    no_parsean = []
    textos_vacios = []

    with ruta.open(encoding="utf-8") as f:
        for i, linea in enumerate(f, start=1):
            if not linea.strip():
                n_blancas += 1
                continue
            n_lineas += 1

            try:
                doc = json.loads(linea)
            except json.JSONDecodeError as e:
                no_parsean.append((i, str(e)))
                continue

            faltantes = CAMPOS_OBLIGATORIOS - doc.keys()
            if faltantes:
                sin_campos.append((i, sorted(faltantes)))

            doc_ids[doc.get("doc_id")] += 1
            fuentes[doc.get("fuente")] += 1
            por_formato[doc.get("formato")] += 1

            texto = doc.get("text") or ""
            if not texto.strip():
                textos_vacios.append(doc.get("fuente"))

            for c in texto:
                if unicodedata.category(c) in ("Cf", "Zl", "Zp"):
                    invisibles[f"U+{ord(c):04X}"] += 1

    # -----------------------------------------------------------------------
    # 4. Caracteres "ambiguos" (los que marca VS Code)
    # -----------------------------------------------------------------------
    # VS Code avisa de caracteres que se PARECEN a ASCII pero no lo son.
    # Casi todos son tipografía legítima del scraping web (comillas curvas,
    # guiones largos) y NO hay que tocarlos: la Tabla 1 pide texto original.
    #
    # Lo único preocupante es el HOMOGLIFO: una letra cirílica o griega
    # DENTRO de una palabra por lo demás latina (p.ej. "inform<е>" con e
    # cirílica). Eso sí es basura del scraping y rompe el matching.
    #
    # OJO: el griego NO cuenta como mezcla. Letras griegas pegadas a texto
    # latino son normales en química y física (β-HCH, α-partícula, γ-radiación).
    # El cirílico junto a latino, en cambio, es casi siempre homoglifo.
    #
    # Una letra griega suelta (β, ρ) es notación matemática legítima, y la
    # puntuación de ancho completo (，：) viene de citas en chino. Ninguna
    # de las dos es un problema, así que se reportan aparte.
    tipograficos = Counter()
    otros_alfabetos = Counter()
    homoglifos = []

    for palabra in texto_completo.split():
        alfabetos = set()
        for c in palabra:
            if not c.isalpha():
                continue
            # Las letras ASCII cuentan como latinas: sin esto, una palabra
            # como "inform<е>" solo vería la cirílica y no detectaría la mezcla.
            if ord(c) < 128:
                alfabetos.add("latino")
                continue
            try:
                nombre = unicodedata.name(c)
            except ValueError:
                continue
            if "CYRILLIC" in nombre:
                alfabetos.add("cirílico")
            elif "LATIN" in nombre:
                alfabetos.add("latino")
        # Mezcla dentro de la MISMA palabra = homoglifo
        if "latino" in alfabetos and len(alfabetos) > 1:
            homoglifos.append(palabra)

    for c, n in _contar_no_ascii(texto_completo).items():
        try:
            nombre = unicodedata.name(c)
        except ValueError:
            nombre = "?"
        if c.isalpha() or "FULLWIDTH" in nombre or "IDEOGRAPHIC" in nombre:
            otros_alfabetos[f"U+{ord(c):04X} {nombre}"] = n
        else:
            tipograficos[f"U+{ord(c):04X} {nombre}"] = n

    # -----------------------------------------------------------------------
    # Reporte
    # -----------------------------------------------------------------------
    print(f"Objetos JSON válidos: {n_lineas}")
    if n_blancas:
        avisos.append(f"{n_blancas} líneas en blanco (se ignoran al leer)")

    if por_formato:
        print("\nPor formato:")
        for formato, n in sorted(por_formato.items()):
            print(f"  {str(formato):<10} {n:>6}")

    if no_parsean:
        problemas.append(f"{len(no_parsean)} líneas no parsean como JSON")
        for i, e in no_parsean[:5]:
            print(f"    línea {i}: {e}")

    if sin_campos:
        problemas.append(f"{len(sin_campos)} objetos sin campos obligatorios")
        for i, f_ in sin_campos[:5]:
            print(f"    línea {i}: faltan {f_}")

    if textos_vacios:
        problemas.append(f"{len(textos_vacios)} documentos con text vacío")

    dup_fuente = {k: v for k, v in fuentes.items() if v > 1}
    if dup_fuente:
        problemas.append(
            f"{len(dup_fuente)} valores de 'fuente' duplicados "
            f"(el mismo archivo se ingirió dos veces)"
        )
        for k, v in list(dup_fuente.items())[:5]:
            print(f"    {k} x{v}")

    dup_docid = {k: v for k, v in doc_ids.items() if v > 1}
    if dup_docid:
        avisos.append(
            f"{len(dup_docid)} doc_id repetidos (contenido idéntico en varios "
            f"archivos — deduplicación funcionando, no es error)"
        )

    if tipograficos:
        print("\nCaracteres tipográficos (lo que marca VS Code — INOFENSIVO):")
        for desc, n in Counter(tipograficos).most_common(8):
            print(f"  {desc:<46} {n:>7}")
        print("  -> Son comillas y guiones del scraping web. NO tocar:")
        print("     la Tabla 1 pide 'texto original sin modificaciones'.")

    if otros_alfabetos:
        print("\nOtros alfabetos y puntuación (normalmente legítimos):")
        for desc, n in Counter(otros_alfabetos).most_common(8):
            print(f"  {desc:<46} {n:>7}")
        print("  -> Letras griegas sueltas = notación matemática (β, ρ).")
        print("     Puntuación de ancho completo = citas en chino/japonés.")

    if homoglifos:
        problemas.append(
            f"{len(homoglifos)} palabras mezclan latino y cirílico: "
            f"{homoglifos[:5]}. Una letra cirílica DENTRO de una palabra "
            f"latina es un homoglifo del scraping."
        )

    if invisibles:
        problemas.append(
            f"caracteres invisibles sobrevivientes en el campo 'text': "
            f"{dict(invisibles.most_common(5))}"
        )

    print()
    if problemas:
        print(f"[ERROR] {len(problemas)} problemas:")
        for p in problemas:
            print(f"  · {p}")
    if avisos:
        print(f"\n[AVISO] {len(avisos)}:")
        for a in avisos:
            print(f"  · {a}")
    if not problemas:
        print("OK — el archivo está íntegro.")

    return 1 if problemas else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python validate_jsonl.py <archivo.jsonl>")
        sys.exit(2)
    sys.exit(validar(sys.argv[1]))