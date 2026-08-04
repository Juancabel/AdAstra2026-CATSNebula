# `identity.py` — Normalización e IDs

Módulo compartido. **Nadie calcula un `doc_id` fuera de aquí.**

Alcance: solo identidad a nivel de **documento**. El `chunk_id` y la estrategia
de fragmentación los define C; este módulo no los toca.

## Instalación

No tiene dependencias externas: solo `hashlib`, `re` y `unicodedata` de la
librería estándar. Copiar `src/identity.py` al repo y listo.

## Uso

```python
from identity import compute_doc_id, compute_chunk_id, normalize_text

# 1. Extraer el texto crudo con la herramienta que toque según formato
texto_crudo = extraer_pdf(ruta)      # A: fitz / trafilatura
# texto_crudo = serializar_csv(ruta) # B: csv.DictReader

# 2. El ID sale del contenido. compute_doc_id normaliza por dentro:
#    NO hay que normalizar antes.
doc_id = compute_doc_id(texto_crudo)

# 3. El texto que se guarda sí va normalizado
documento = {
    "doc_id": doc_id,
    "fuente": nombre_original,        # VERBATIM, sin tocar
    "formato": "pdf",
    "fenomeno": 2,
    "lang": "es",
    "text": normalize_text(texto_crudo),
}

# 4. Escribir SIEMPRE con json.dumps: escapa saltos de línea y comillas
f.write(json.dumps(documento, ensure_ascii=False) + "\n")
```

## Manejo de extracción fallida

`compute_doc_id` lanza `ValueError` si el texto normalizado queda vacío.
Es intencionado: si el vacío pasara, **todos** los documentos con extracción
fallida compartirían el mismo hash y se pisarían en `metadata.jsonl`.

```python
try:
    doc_id = compute_doc_id(texto_crudo)
except ValueError:
    log_fallos.append(ruta)   # registrar y NO indexar
    continue
```

Para imágenes con OCR y tiles PBF, filtrar antes el ruido:

```python
if not text_is_usable(texto_crudo, min_palabras=20):
    log_baja_calidad.append(ruta)
    continue
```

## Pruebas

```bash
pytest tests/test_identity.py -v     # 27 pruebas
python demo_identity.py              # demostración visual
```

## Qué normaliza y por qué

| Paso | Motivo |
|---|---|
| Unicode NFC | `"á"` tiene dos codificaciones válidas. PDF y HTML no coinciden en cuál emiten. Sin esto, el mismo documento tendría dos IDs. |
| Quitar BOM / zero-width / soft hyphen | Invisibles, pero cambian el hash. El soft hyphen abunda en PDFs justificados. |
| `\xa0` → espacio | Los PDFs y los `&nbsp;` de HTML lo producen constantemente. |
| CRLF/CR → LF | Archivos de Windows y de Mac antiguo. |
| Colapsar espacios, quitar los finales de línea | Ruido de extracción. |
| 3+ saltos → 2 | Limpia sin destruir el límite de párrafo. |

**Lo que NO hace:** no colapsa los saltos de línea a espacios. La estructura de
párrafos se conserva a propósito, porque C la usa como señal de chunking.

## Regla crítica

`NORMALIZER_VERSION = "1.0.0"` está en la cabecera del módulo.

Si alguien modifica `normalize_text()`, **todos los `doc_id` del corpus
cambian** y hay que reconstruir el índice FAISS y `metadata.jsonl` enteros.

`tests/test_identity.py::test_vectores_congelados` compara contra hashes
fijos y falla ruidosamente si eso pasa. Es la red de seguridad: no actualizar
esos valores sin avisar al equipo.

Después del Día 3, tocar este archivo debería requerir acuerdo de los cuatro.
