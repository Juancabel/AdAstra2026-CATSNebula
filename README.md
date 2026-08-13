# AdAstra2026-CATSNebula

Este proyecto contiene la respuesta a la primera etapa 1 de la competencia AdAstra2026 de el grupo CATSNebula.

## Puesta en marcha

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
.venv/bin/pip install -r requirements.txt         # Linux/macOS
```

El OCR necesita además el binario **Tesseract** instalado en el sistema, con
los idiomas `spa`, `eng` y `por`. [Aquí](https://www.youtube.com/watch?v=2kWvk4C1pMo) un tutorial de como descargar e instalar Tesseract. No olvidar marcar las casillas de más idiomas.

El corpus va en `corpus_original/` y no se versiona. Es solo descargar el RAR provisto por el equipo organizador, descomprimirlo en el proyecto, y renombrar el directorio como  `corpus_original/`.

## Regenerar `data/documents.jsonl`

`data/documents.jsonl` pesa ~200 MB y **no está en git**: supera el límite de
100 MB por archivo de GitHub. Es dato derivado y la ingesta es determinista
(dos corridas dan el mismo `sha256`), así que se reconstruye con dos comandos:

```bash
python src/ingest_data.py corpus_original data/documents.jsonl
python detectar_idioma.py data/documents.jsonl data/documents.jsonl --reporte data/reporte_idioma.json
```

Tarda unos 70 minutos. Se demora debido a que hay alrededor de 75 PDFs que tienen texto no seleccionable, lo que obliga al sistema a convertir cada una de sus páginas en imagenes y luego a extraer su contenido usando Tesseract. 

## Identidad de los documentos

El `doc_id` **no se calcula**: es la columna `DOC_ID` de
`corpus_original/Indice_Datos_Codefest.xlsx` (ej. `F1-AIINDEX-001`), que los
organizadores confirmaron como clave de emparejamiento de la evaluación. Lo
carga [src/indice_oficial.py](src/indice_oficial.py) usando
`Carpeta + "/" + Nombre estandarizado` como clave, que casa exacto con el campo
`fuente`.

La huella del contenido (`content_sha1`) sigue existiendo dentro de `extra`,
pero ya no es identidad: solo sirve para detectar documentos con texto idéntico
y `doc_id` distintos.

## Comprobaciones

```bash
python -m pytest tests/ -q            # 57 pruebas
python validate_jsonl.py data/documents.jsonl        # integridad del JSONL
python verificar_registros.py data/documents.jsonl   # atomicidad de csv/xlsx/pbf
```
