"""
demo_identity.py — Demostración visual de identity.py

Ejecutar:  python demo_identity.py

No es una prueba (esas están en tests/test_identity.py). Sirve para VER
que el módulo hace lo que promete, sobre todo los casos de codificación
que son invisibles a simple vista.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from identity import compute_content_sha1, normalize_text


def titulo(t):
    print(f"\n{'=' * 68}\n{t}\n{'=' * 68}")


# ---------------------------------------------------------------------------
titulo("1. Mismo contenido -> mismo ID, siempre")

texto = "La órbita baja terrestre presenta congestión creciente."
print(f"Texto : {texto}")
print(f"ID 1  : {compute_content_sha1(texto)}")
print(f"ID 2  : {compute_content_sha1(texto)}")
print("-> Idénticos. El ID no depende del orden de archivos ni de la máquina.")


# ---------------------------------------------------------------------------
titulo("2. Casos de codificación: bytes distintos, ID idéntico")

pares = [
    ("NFC vs NFD (tilde)", "Bogot\u00e1", "Bogota\u0301"),
    ("Salto Windows vs Unix", "A\r\nB", "A\nB"),
    ("Con BOM vs sin BOM", "\ufeffTexto largo de prueba", "Texto largo de prueba"),
    ("Espacio duro vs normal", "Fuerza\u00a0Aeroespacial", "Fuerza Aeroespacial"),
    ("Guion suave de PDF", "inter\u00adnacional aqui", "internacional aqui"),
]

for nombre, a, b in pares:
    ida, idb = compute_content_sha1(a), compute_content_sha1(b)
    marca = "OK " if ida == idb else "MAL"
    print(f"[{marca}] {nombre:<26} bytes iguales: {a == b!s:<5} ID: {ida}")

print("\n-> Los strings crudos son DISTINTOS, pero el ID es el MISMO.")
print("   Sin esto, el mismo documento tendría dos IDs según de dónde salga.")


# ---------------------------------------------------------------------------
titulo("3. Contenido distinto -> ID distinto")

for t in [
    "Primer documento sobre basura espacial.",
    "Segundo documento sobre basura espacial.",
    "Inteligencia artificial en defensa nacional.",
]:
    print(f"{compute_content_sha1(t)}  <-  {t}")


# ---------------------------------------------------------------------------
titulo("4. Normalización: qué se conserva y qué no")

crudo = "  Título\r\n\n\n\n\nCuerpo\u00a0del    texto.   \n\n\n"
print(f"Crudo       : {crudo!r}")
print(f"Normalizado : {normalize_text(crudo)!r}")
print("-> Los párrafos sobreviven (C los usa como frontera de chunk),")
print("   pero el ruido de codificación desaparece.")


# ---------------------------------------------------------------------------
titulo("5. Extracción fallida: falla ruidosamente, no en silencio")

for descripcion, valor in [("vacío", ""), ("solo espacios", "  \n\t "), ("None", None)]:
    try:
        compute_content_sha1(valor)
        print(f"[MAL] {descripcion}: no lanzó error")
    except ValueError:
        print(f"[OK ] {descripcion}: ValueError, como debe ser")

print("\n-> Si dejáramos pasar el vacío, TODOS los documentos con extracción")
print("   fallida compartirían el mismo hash y se pisarían en metadata.jsonl.")


# ---------------------------------------------------------------------------
titulo("6. Flujo completo: archivo -> documents.jsonl -> relectura")

with tempfile.TemporaryDirectory() as tmp:
    md = Path(tmp) / "informe_leo_2025.md"
    md.write_text(
        "# Informe LEO 2025\n\n"
        "La congestión en órbita baja terrestre aumentó un 12% este año.\n\n"
        "Los desechos espaciales superan los 36.000 objetos catalogados.\n",
        encoding="utf-8",
    )

    texto_crudo = md.read_text(encoding="utf-8")
    documento = {
        "doc_id": compute_content_sha1(texto_crudo),
        "fuente": md.name,           # verbatim, tal cual lo entrega ADL
        "formato": "md",
        "fenomeno": 2,
        "lang": "es",
        "text": normalize_text(texto_crudo),
    }

    salida = Path(tmp) / "documents.jsonl"
    with salida.open("w", encoding="utf-8") as f:
        f.write(json.dumps(documento, ensure_ascii=False) + "\n")

    lineas = salida.read_text(encoding="utf-8").splitlines()
    recuperado = json.loads(lineas[0])

    print(f"Archivo        : {md.name}")
    print(f"doc_id         : {documento['doc_id']}")
    print(f"Líneas JSONL   : {len(lineas)} (debe ser 1)")
    print(f"Round-trip     : {'OK' if recuperado['doc_id'] == documento['doc_id'] else 'MAL'}")
    print(f"\nLínea escrita:\n{json.dumps(recuperado, ensure_ascii=False, indent=2)}")

print("\n-> Con PDF, CSV, XLSX o PBF el flujo es idéntico.")
print("   Lo único que cambia es la función de extracción.\n")
