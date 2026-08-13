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

## Índice vectorial (`entrega/base_vectorial/`)

`encode_index.py` encodea `data/chunks.jsonl` con **BAAI/bge-m3** (dense,
1024-dim, revisión `5617a9f6…` fijada en el script) y construye un
`IndexFlatIP` sobre vectores L2-normalizados — que es coseno con búsqueda
exacta. A 86.046 vectores el índice son ~336 MB y la búsqueda es sub-segundo
en CPU, así que IVF/HNSW no aportan nada y sí traerían no-determinismo.

```bash
# 1. medir antes de comprometer: muestra estratificada por fenómeno
python encode_index.py data/chunks.jsonl salida_muestra --muestra 500
python scripts/verificar_indice.py salida_muestra --n 30

# 2. corrida completa (larga; --reanudar la continúa si se corta)
python encode_index.py data/chunks.jsonl entrega/base_vectorial/encoder_bge-m3
python scripts/verificar_indice.py entrega/base_vectorial/encoder_bge-m3
```

**La corrida completa va en GPU.** Medido en el CPU del equipo (Ryzen 5 5600G,
fp32): 6,9 s/chunk, o sea ~164 h para los 86.046 chunks. En GPU son 1-4 h según
la tarjeta. Hay un notebook por plataforma, mismo recorrido en los dos
(muestra → verificación → corrida → verificación):

* `scripts/kaggle_encode_index.ipynb` — **el recomendado**. Sesión de 12 h
  garantizadas y la salida sobrevive con `Save & Run All`. Entrada como dataset,
  salida en `/kaggle/working`.
* `scripts/colab_encode_index.ipynb` — Colab gratis corta cerca de las 4 h. La
  salida va a Drive para que `--reanudar` sirva de algo cuando eso pase.

`index.faiss` y `metadata.jsonl` no se versionan (superan el límite de 100 MB
de GitHub); `manifiesto.json` sí, y guarda revisión del modelo, versiones,
dispositivo y `sha256` de los dos artefactos.

**El mapeo 1:1** (ID interno `i` de FAISS == línea `i` de `metadata.jsonl`) es
lo único que no puede fallar aquí: si se desalinea, el índice sigue
recuperando "algo" con puntajes razonables y devuelve el texto equivocado.
`scripts/verificar_indice.py` lo comprueba en un proceso nuevo re-encodeando
una muestra y comparando contra `index.reconstruct(i)`. Verificado contra un
control negativo: rotar `metadata.jsonl` una sola línea baja el peor coseno de
1.000000 a 0.34.

`metadata.jsonl` añade sobre el Contrato 2: `num_tokens` recalculado con el
tokenizador real de BGE-M3 (el de `chunks.jsonl` es palabras × 1.9, y queda
guardado como `num_tokens_estimado`) y `catalogo_masivo`, la bandera de los
8.750 chunks de catálogo que necesitan `subdividir_para_salida()` al responder.

## Comprobaciones

```bash
python -m pytest tests/ -q            # 57 pruebas
python validate_jsonl.py data/documents.jsonl        # integridad del JSONL
python verificar_registros.py data/documents.jsonl   # atomicidad de csv/xlsx/pbf
```
