# Documentación Completa: Grafo de Conocimiento para Recuperación Reforzada

## Tabla de Contenidos

1. [Introducción](#introducción)
2. [Arquitectura General](#arquitectura-general)
3. [Componentes Clave](#componentes-clave)
4. [Flujo de Datos](#flujo-de-datos)
5. [Especificación de Entrada y Salida](#especificación-de-entrada-y-salida)
6. [Algoritmos de Extracción](#algoritmos-de-extracción)
7. [Construcción del Grafo](#construcción-del-grafo)
8. [Integración con Ranking Vectorial](#integración-con-ranking-vectorial)
9. [Decisiones de Diseño](#decisiones-de-diseño)
10. [Limitaciones Conocidas](#limitaciones-conocidas)
11. [Guía de Uso](#guía-de-uso)
12. [Verificación y Pruebas](#verificación-y-pruebas)
13. [Extensiones Futuras](#extensiones-futuras)
14. [Apéndices](#apéndices)

---

## Introducción

El **grafo de conocimiento** es una capa complementaria al índice vectorial FAISS que mejora la recuperación de documentos mediante:

- **Extracción de entidades**: detección de países, organizaciones, tecnologías y eventos.
- **Extracción de relaciones**: identificación de vínculos semánticos entre entidades (ej: "desarrolla", "regula", "coordina").
- **Reforzamiento de ranking**: fusión RRF entre candidatos vectoriales y candidatos del grafo.
- **Trazabilidad semántica**: evidencia explícita de por qué un documento es relevante.

**Propósito en el reto**: No reemplaza la base vectorial FAISS, sino que actúa como soporte y explicación del ranking, cumpliendo con la Sección 8.4 del reto (fusión de candidatos sin modelos generativos).

**Ventajas clave**:
- Determinista y reproducible (sin aleatoriedad).
- Bajo overhead computacional (grafo preconstruido).
- Integración opcional y no crítica con el pipeline principal.
- Mejora de la explicabilidad de resultados.

---

## Arquitectura General

### Diagrama Conceptual

```
┌─────────────────────────────────────────────────────────────────┐
│                     Pipeline de Recuperación                     │
└─────────────────────────────────────────────────────────────────┘

                    ENTRADA: Query + Índice FAISS
                              │
                    ┌─────────┴─────────┐
                    │                   │
            ┌───────▼────────┐   ┌──────▼────────┐
            │ Recuperación   │   │ Extracción de │
            │ Vectorial      │   │ Entidades     │
            │ (Top-200)      │   │ de Query      │
            └───────┬────────┘   └──────┬────────┘
                    │                   │
                    │         ┌─────────▼─────────┐
                    │         │ Búsqueda en Grafo │
                    │         │ de Vecinos        │
                    │         └──────┬────────────┘
                    │                │
            ┌───────▼────────────────▼──────┐
            │ Fusión RRF (Rank Fusion)      │
            │ - Vectorial + Grafo           │
            │ - Scores combinados           │
            └───────┬─────────────────────┘
                    │
            ┌───────▼──────────────┐
            │ Ranking Definitivo   │
            │ (Top-3 documentos)   │
            └───────┬──────────────┘
                    │
            ┌───────▼──────────────┐
            │ Extracción de        │
            │ Fragmentos (10)      │
            │ + Evidencia del Grafo│
            └───────┬──────────────┘
                    │
            SALIDA: resultados.jsonl
                    + evidencia_consultas.jsonl
```

### Capas de la Arquitectura

| Capa | Componente | Entrada | Salida | Propósito |
|------|-----------|---------|--------|-----------|
| **Ingesta** | `chunk.py` | corpus | chunks.jsonl | Segmentación de documentos |
| **Grafo** | `knowledge_graph.py` | chunks.jsonl | grafo.graphml | Extracción y almacenamiento de entidades/relaciones |
| **Construcción** | `build_knowledge_graph.py` | chunks.jsonl | grafo.graphml + tripletas.jsonl | Orquestación de construcción |
| **Recuperación** | `entrega/generador.py` | índice FAISS + grafo | resultados.jsonl | Integración con ranking vectorial |
| **Orquestación** | `scripts/corrida_grafo.py` | datos reales o demo | salida final | Pipeline unificado |

---

## Componentes Clave

### 1. `knowledge_graph.py` - Motor de Extracción y Grafo

**Responsabilidades**:
- Extracción de entidades (países, organizaciones, tecnologías, eventos).
- Extracción de relaciones verbales entre entidades.
- Construcción de un grafo orientado (DiGraph de NetworkX).
- Exportación a GraphML para persistencia.

**Funciones principales**:

#### `normalize_entity(value: str) -> str`
Normaliza nombres de entidades eliminando espacios redundantes, símbolos y caracteres de control.

```python
>>> normalize_entity("  Estados Unidos  ")
'Estados Unidos'

>>> normalize_entity("sistemas autónomos,,,")
'sistemas autónomos'
```

#### `extract_entities_from_text(text: str) -> list[str]`
Detección de entidades en español/inglés usando:
1. **Coincidencia exacta** con diccionarios predefinidos (COUNTRIES_AND_ORGS, TECH_TERMS, EVENT_TERMS).
2. **Heurística de frases**: detecta sustantivos propios capitalizados de 1-5 palabras.
3. **Deduplicación y filtrado** de stopwords.

**Algoritmo**:
```
entrada: texto sin procesar
1. Normalizar espacios
2. Buscar tokens de diccionarios (ordenados por longitud desc)
3. Ejecutar heurística de frases capitalizadas
4. Deduplicar y filtrar stopwords
5. Devolver lista ordenada de entidades
```

**Ejemplo**:
```python
text = "Estados Unidos y Colombia desarrollan un sistema autónomo para la Fuerza Aérea."
entities = extract_entities_from_text(text)
# Resultado: ['Estados Unidos', 'Colombia', 'sistema autónomo', 'Fuerza Aérea']
```

#### `extract_triplets_from_text(text: str, doc_id: str, chunk_id: str) -> list[dict]`
Extracción de tripletas (sujeto, relación, objeto) preservando trazabilidad.

**Algoritmo en dos fases**:

**Fase 1: Emparejamiento de entidades y relaciones**
```
para cada oración:
  1. Extraer entidades de la oración
  2. Para cada relación conocida:
    Para cada par de entidades (sujeto, objeto):
      Si la relación aparece entre el sujeto y el objeto en la oración:
        Crear tripleta con evidencia
```

**Fase 2: Fallback heurístico**
```
Si Fase 1 no produjo tripletas:
  Para cada oración:
    Buscar patrón: <SUSTANTIVO> <RELACIÓN> <RESTO>
    Extraer objeto limpiando preposiciones redundantes
```

**Deduplicación final**: se mantiene una sola tripleta por clave única (subject, relation, object).

**Ejemplo**:
```python
text = "Estados Unidos desarrolla sistemas autónomos. Colombia regula el uso."
triplets = extract_triplets_from_text(text, doc_id="F1-001", chunk_id="F1-001_c000")

# Resultado:
# [
#   {"subject": "Estados Unidos", "relation": "desarrolla", "object": "sistemas autónomos",
#    "doc_id": "F1-001", "chunk_id": "F1-001_c000", "evidence": "Estados Unidos desarrolla sistemas autónomos."},
#   {"subject": "Colombia", "relation": "regula", "object": "uso",
#    "doc_id": "F1-001", "chunk_id": "F1-001_c000", "evidence": "Colombia regula el uso."}
# ]
```

#### `build_graph_from_chunks(chunks: Iterable[dict]) -> nx.DiGraph`
Construcción del grafo orientado a partir de chunks.

**Estructura del grafo**:
- **Nodos**: entidades (strings normalizados).
- **Aristas**: relaciones con atributos:
  - `relation`: tipo de relación (ej: "desarrolla").
  - `doc_ids`: lista de doc_id de los chunks que contienen la relación (formato string separado por `;`).
  - `chunk_ids`: lista de chunk_id asociados.
  - `evidence`: fragmento textual de hasta 300 caracteres que respalda la relación.

**Algoritmo**:
```
grafo = DiGraph vacío
para cada chunk:
  texto = chunk["texto"]
  doc_id = chunk["doc_id"]
  chunk_id = chunk["chunk_id"]
  
  para cada tripleta en extract_triplets_from_text(texto, doc_id, chunk_id):
    sujeto = tripleta["subject"]
    objeto = tripleta["object"]
    relación = tripleta["relation"]
    
    si la arista (sujeto -> objeto) no existe:
      grafo.add_edge(sujeto, objeto, relation=relación, doc_ids=doc_id, ...)
    else:
      actualizar doc_ids (dedup)
      
Retornar grafo
```

**Ejemplo de grafo**:
```
Nodos: {Estados Unidos, Colombia, sistemas autónomos, Fuerza Aérea, ONU, ...}

Aristas:
  Estados Unidos --desarrolla--> sistemas autónomos
    (doc_id: F1-001, evidence: "Estados Unidos desarrolla...")
  
  Colombia --regula--> sistemas autónomos
    (doc_id: F1-002, evidence: "Colombia regula el uso...")
  
  ONU --coordina--> Fuerza Aérea
    (doc_id: F2-005, evidence: "La ONU coordina con la Fuerza Aérea...")
```

#### `save_graph(graph, path)` / `load_graph(path)`
Persistencia en formato GraphML (estándar abierto compatible con Gephi, Cytoscape, etc.).

---

### 2. `build_knowledge_graph.py` - Orquestador de Construcción

**Responsabilidades**:
- Lectura de chunks reales desde `data/chunks.jsonl`.
- Fallback a chunks demo si no existen datos reales.
- Invocación de `build_graph_from_chunks()`.
- Exportación a GraphML y tripletas.

**Interfaz CLI**:
```bash
python build_knowledge_graph.py <ruta_chunks> \
  --output <ruta_grafo.graphml> \
  --triplets <ruta_tripletas.jsonl>
```

**Flujo**:
```
1. Validar que chunks.jsonl exista
   Si no → crear demo_chunks.jsonl
2. Iterar sobre líneas de chunks
3. Construir grafo con build_graph_from_chunks()
4. Exportar grafo.graphml
5. Exportar tripletas.jsonl (formato legible para auditoría)
```

**Salida de tripletas.jsonl**:
```json
{
  "subject": "Estados Unidos",
  "relation": "desarrolla",
  "object": "sistemas autónomos",
  "doc_ids": "F1-001;F1-003",
  "evidence": "Estados Unidos desarrolla sistemas autónomos..."
}
```

---

### 3. `entrega/generador.py` - Integración Retrieval + Grafo

**Componentes nuevos añadidos**:

#### `cargar_grafo(ruta: Path) -> nx.DiGraph | None`
Carga el grafo desde GraphML. Retorna `None` si no existe (pipeline tolerante).

#### `doc_ids_relacionados_por_consulta(query_text: str, grafo) -> set`
Busca entidades en la query y recupera todos los `doc_id` conectados en el grafo.

**Algoritmo**:
```
entidades = extract_entities_from_text(query_text)
matches = {}

para cada entidad en entidades:
  para cada nodo en grafo:
    si nodo coincide con entidad (búsqueda fuzzy):
      para cada arista saliente o entrante:
        extraer doc_ids del atributo de arista
        agregar a matches

retornar matches como conjunto
```

**Ejemplo**:
```
query: "¿Qué hace Estados Unidos?"
entidades detectadas: ["Estados Unidos"]
nodos relacionados en grafo: "sistemas autónomos", "defensa", ...
doc_ids recuperados: {"F1-001", "F1-003", "F2-005"}
```

#### `fusionar_resultados_vectoriales(vector_ranking, graph_candidates, k0=60) -> list`
Fusión de dos listas de doc_id usando **RRF (Reciprocal Rank Fusion)**.

**Fórmula RRF**:
```
score(doc) = Σ 1 / (k₀ + rank_i)
             i ∈ {vectorial, grafo}

k₀ = 60 (constante de dampening)
```

**Algoritmo**:
```
1. Para cada ranking (vectorial, grafo):
   Para cada posición i (1-indexed):
     score[doc_id] += 1 / (k₀ + i)

2. Ordenar docs por score descendente
3. En caso de empate: priorizar orden vectorial, luego grafo
4. Retornar lista ordenada
```

**Ejemplo**:
```
vector_ranking = ["DOC-1", "DOC-2", "DOC-3", "DOC-4"]
graph_candidates = ["DOC-2", "DOC-5", "DOC-6"]

Cálculo de scores (k₀=60):
  DOC-1: 1/61 = 0.0164 (vector)
  DOC-2: 1/62 + 1/61 = 0.0328 (vector + grafo)
  DOC-3: 1/63 = 0.0159 (vector)
  DOC-4: 1/64 = 0.0156 (vector)
  DOC-5: 1/62 = 0.0161 (grafo)
  DOC-6: 1/63 = 0.0159 (grafo)

Orden final: DOC-2 > DOC-1 > DOC-5 > DOC-3,DOC-6 > DOC-4
```

#### `grafo_evidencia_para_doc(grafo, doc_id, query_text) -> str`
Extrae fragmentos de evidencia del grafo para un documento específico.

**Lógica**:
```
snippets = []
para cada arista en grafo:
  si doc_id está en los doc_ids de la arista:
    crear snippet = "<sujeto> <relación> <objeto>. Evidencia: <texto>"
    agregar a snippets

retornar primeros 3 snippets unidos con " | "
```

#### `generar_evidencia_consulta(qid, query_text, documents, fragments, grafo) -> dict`
Genera un artefacto paralelo (sidecar) con la evidencia del grafo.

**Estructura de salida**:
```json
{
  "query_id": "q001",
  "query_text": "¿Qué hace Estados Unidos en defensa?",
  "documents": [
    {"rank": 1, "doc_id": "F1-001"},
    ...
  ],
  "fragments": [...],
  "graph_evidence": [
    {
      "rank": 1,
      "doc_id": "F1-001",
      "graph_evidence": [
        {
          "subject": "Estados Unidos",
          "relation": "desarrolla",
          "object": "sistemas autónomos",
          "evidence": "Estados Unidos desarrolla..."
        }
      ]
    }
  ]
}
```

#### `construir_fragmentos(..., grafo) -> list`
Modifica la extracción de fragmentos para incluir evidencia del grafo.

**Cambio**:
- Después de subdividir por palabras, se intenta agregar evidencia del grafo.
- Se respeta el límite duro de 250 palabras.
- La evidencia se prepone con el prefijo `"Evidencia del grafo: "`.

**Ejemplo de fragmento final**:
```
"text": "Estados Unidos desarrolla sistemas autónomos para defensa... 
         Evidencia del grafo: Estados Unidos desarrolla sistemas autónomos. 
         Colombia regula el uso de estos sistemas..."
```

---

### 4. `scripts/corrida_grafo.py` - Pipeline Unificado

**Responsabilidades**:
- Ejecutar construcción del grafo.
- Opcionalmente, disparar generador de resultados.
- Manejo de fallback a chunks demo.

**CLI**:
```bash
python scripts/corrida_grafo.py [--chunks ruta] [--sin-generador]
```

**Opciones**:
- `--chunks`: ruta a chunks.jsonl reales (default: `data/chunks.jsonl`).
- `--sin-generador`: solo construir grafo, no ejecutar generador.

---

## Flujo de Datos

### Flujo Completo (End-to-End)

```
ENTRADA: corpus_original/ (PDFs)
  ↓
[ingest_data.py]
  ↓
ARCHIVO: data/documents.jsonl (~200 MB)
  ↓
[chunk.py]
  ↓
ARCHIVO: data/chunks.jsonl (~86K chunks)
  ↓
┌─────────────────────────────────┐
│                                  │
├→ [encode_index.py] ──→ FAISS Index
│                        (~336 MB)
│
├→ [build_knowledge_graph.py]
│  [knowledge_graph.py]
   ↓
   ARCHIVO: entrega/base_vectorial/grafo/
   ├── grafo.graphml (grafo serializado)
   ├── tripletas.jsonl (para auditoría)
   └── demo_chunks.jsonl (fallback)

ENTRADA RUNTIME: Consultas (50 preguntas)
  ↓
[entrega/generador.py]
  ├ Carga FAISS index
  ├ Carga grafo
  ├ Codifica queries
  │
  ├ Para cada query:
  │  1. Top-200 FAISS
  │  2. Extrae entidades de query
  │  3. Busca entidades en grafo → doc_ids
  │  4. Fusión RRF (vectorial + grafo)
  │  5. Top-3 documentos
  │  6. Extrae 10 fragmentos con evidencia
  │
  ├ Genera sidecar de evidencia
  │
  ↓
SALIDA: entrega/
├── resultados.jsonl (50 líneas, 3 docs, 10 frags por consulta)
└── base_vectorial/grafo/evidencia_consultas.jsonl (sidecar con evidencia del grafo)
```

### Ejemplo Concreto: Procesamiento de Una Consulta

**Entrada**:
```json
{
  "query_id": "q001",
  "texto": "¿Qué organismos internacionales regulan los sistemas autónomos?"
}
```

**Paso 1: Extracción de entidades**
```python
entities = extract_entities_from_text("¿Qué organismos internacionales...")
# → ["organismos internacionales", "sistemas autónomos", "ONU", ...]
```

**Paso 2: Recuperación vectorial (FAISS)**
```python
qvec = encoder.encode("¿Qué organismos internacionales regulan...")
D, I = index.search([qvec], TOP_K=200)
# → hits = [(0.95, 42), (0.92, 157), (0.88, 201), ...]
# doc_ids vectoriales: ["F1-001", "F2-005", "F1-003", "F3-010", ...]
```

**Paso 3: Recuperación del grafo**
```python
graph_doc_ids = doc_ids_relacionados_por_consulta(query, grafo)
# Coincidencias en nodos: ONU, sistemas autónomos
# Aristas: ONU --coordina--> Fuerza Aérea (doc_id: F2-005)
#          Colombia --regula--> sistemas autónomos (doc_id: F1-002)
# → {"F2-005", "F1-002", "F3-015"}
```

**Paso 4: Fusión RRF**
```python
vector_ranking = ["F1-001", "F2-005", "F1-003", "F3-010", ...]
graph_candidates = ["F2-005", "F1-002", "F3-015"]

fused = fusionar_resultados_vectoriales(vector_ranking, graph_candidates)
# → ["F2-005", "F1-001", "F1-002", "F3-010", "F3-015", ...]
```

**Paso 5: Top-3 documentos**
```python
top_docs = fused[:3]
# → [{"rank": 1, "doc_id": "F2-005"}, 
#    {"rank": 2, "doc_id": "F1-001"},
#    {"rank": 3, "doc_id": "F1-002"}]
```

**Paso 6: Extracción de fragmentos**
```python
fragments = []
para cada hit en hits (ordenado por score):
  texto = chunk[CAMPO_TEXTO]
  subdividir por 250 palabras
  
  # Agregar evidencia del grafo
  evidencia = grafo_evidencia_para_doc(grafo, doc_id, query)
  # → "ONU coordina con Fuerza Aérea. Evidence: La ONU coordina..."
  
  texto_final = texto + " Evidencia del grafo: " + evidencia[:espacio_restante]
  
  fragments.append({
    "rank": len(fragments) + 1,
    "chunk_id": meta["chunk_id"],
    "doc_id": meta["doc_id"],
    "text": texto_final
  })
  
  si len(fragments) == 10:
    break

# → [10 fragmentos con evidencia incrustada]
```

**Paso 7: Salida JSON**
```json
{
  "query_id": "q001",
  "documents": [
    {"rank": 1, "doc_id": "F2-005"},
    {"rank": 2, "doc_id": "F1-001"},
    {"rank": 3, "doc_id": "F1-002"}
  ],
  "fragments": [
    {
      "rank": 1,
      "chunk_id": "F2-005_c042",
      "doc_id": "F2-005",
      "text": "La ONU coordina con la Fuerza Aérea para mitigar riesgos... Evidencia del grafo: ONU coordina Fuerza Aérea..."
    },
    ...
  ]
}
```

**Sidecar de evidencia** (grafo/evidencia_consultas.jsonl):
```json
{
  "query_id": "q001",
  "query_text": "¿Qué organismos internacionales regulan los sistemas autónomos?",
  "documents": [...],
  "fragments": [...],
  "graph_evidence": [
    {
      "rank": 1,
      "doc_id": "F2-005",
      "graph_evidence": [
        {
          "subject": "ONU",
          "relation": "coordina",
          "object": "Fuerza Aérea",
          "evidence": "La ONU coordina con la Fuerza Aérea para mitigar riesgos..."
        }
      ]
    }
  ]
}
```

---

## Especificación de Entrada y Salida

### Entrada: chunks.jsonl

Cada línea es un JSON con:

```json
{
  "doc_id": "F1-AIINDEX-001",
  "chunk_id": "F1-AIINDEX-001_c000",
  "fuente": "F1_IA/ai_index/intro.json",
  "fenomeno": 1,
  "texto": "Estados Unidos desarrolla sistemas autónomos para defensa...",
  "posicion": 0,
  "num_tokens": 147,
  "num_tokens_estimado": 89
}
```

**Campos obligatorios**:
- `doc_id`: identificador oficial del documento (asignado por organizadores).
- `chunk_id`: identificador único del segmento.
- `texto`: contenido de texto a procesar.

**Campos opcionales**: `fuente`, `fenomeno`, `posicion`, `num_tokens`, etc.

### Salida: grafo.graphml

Formato estándar GraphML compatible con:
- Gephi, Cytoscape, yEd (edición visual).
- Python (NetworkX), R (igraph), JavaScript (Cytoscape.js).

**Estructura**:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<graphml>
  <key id="relation" for="edge" attr.name="relation" attr.type="string" />
  <key id="doc_ids" for="edge" attr.name="doc_ids" attr.type="string" />
  <key id="evidence" for="edge" attr.name="evidence" attr.type="string" />
  
  <graph edgedefault="directed">
    <node id="Estados Unidos" />
    <node id="sistemas autónomos" />
    
    <edge source="Estados Unidos" target="sistemas autónomos">
      <data key="relation">desarrolla</data>
      <data key="doc_ids">F1-001;F1-003</data>
      <data key="evidence">Estados Unidos desarrolla sistemas...</data>
    </edge>
  </graph>
</graphml>
```

**Lectura en Python**:
```python
import networkx as nx
grafo = nx.read_graphml("grafo.graphml")

# Iterar aristas:
for u, v, data in grafo.edges(data=True):
    print(f"{u} --{data['relation']}--> {v} ({data['doc_ids']})")
```

### Salida: tripletas.jsonl

Formato JSONL para auditoría y carga en sistemas de grafos alternativos.

```json
{"subject": "Estados Unidos", "relation": "desarrolla", "object": "sistemas autónomos", "doc_ids": "F1-001", "evidence": "Estados Unidos desarrolla..."}
{"subject": "Colombia", "relation": "regula", "object": "sistemas autónomos", "doc_ids": "F1-002", "evidence": "Colombia regula el uso..."}
```

### Salida: resultados.jsonl

Idéntico al esquema de entrega del reto (Sección 9), pero con evidencia del grafo integrada en los textos de fragmentos.

```json
{
  "query_id": "q001",
  "documents": [
    {"rank": 1, "doc_id": "F1-001"},
    {"rank": 2, "doc_id": "F2-005"},
    {"rank": 3, "doc_id": "F1-003"}
  ],
  "fragments": [
    {
      "rank": 1,
      "chunk_id": "F1-001_c000",
      "doc_id": "F1-001",
      "text": "... Evidencia del grafo: Estados Unidos desarrolla sistemas autónomos. ..."
    },
    ...
  ]
}
```

### Salida: evidencia_consultas.jsonl (Sidecar)

Artefacto paralelo con trazabilidad completa del grafo (no afecta `resultados.jsonl`).

```json
{
  "query_id": "q001",
  "query_text": "¿Qué hace Estados Unidos?",
  "documents": [...],
  "fragments": [...],
  "graph_evidence": [
    {
      "rank": 1,
      "doc_id": "F1-001",
      "graph_evidence": [
        {"subject": "Estados Unidos", "relation": "desarrolla", "object": "sistemas autónomos", "evidence": "..."}
      ]
    }
  ]
}
```

---

## Algoritmos de Extracción

### Algoritmo de Normalización de Entidades

**Objetivo**: Limpiar y estandarizar nombres para deduplicación y matching.

**Pasos**:
```
1. Reemplazar múltiples espacios por uno.
2. Eliminar espacios en los extremos.
3. Eliminar caracteres especiales: , ; : . - _ ( ) [ ] { } \n
4. Retornar string normalizado.

Ejemplo:
  Entrada: "  Estados---Unidos  "
  Salida: "Estados Unidos"
```

**Código**:
```python
def normalize_entity(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip(" ,;:.-_()[]{}\n")
    if not text:
        return text
    return text.strip()
```

### Algoritmo de Detección de Entidades

**Objetivo**: Identificar nombramientos relevantes de países, organizaciones, tecnologías y eventos.

**Fases**:

**Fase 1: Coincidencia Exacta (Diccionarios)**

Diccionarios predefinidos:
- `COUNTRIES_AND_ORGS`: {"Estados Unidos", "Colombia", "ONU", "OTAN", ...}
- `TECH_TERMS`: {"IA", "inteligencia artificial", "sistema autónomo", ...}
- `EVENT_TERMS`: {"cumbre", "conferencia", "conflicto", ...}

```
Entrada: texto normalizado

Para cada término en DICCIONARIOS (ordenado por longitud descendente):
  Si término aparece en texto (case-insensitive):
    Agregar a candidatos

Razón de ordenamiento: evitar false positives al buscar "IA" cuando existe "inteligencia artificial"
```

**Fase 2: Heurística de Sustantivos Propios**

Detecta frases capitalizadas que no están en diccionarios.

```
Patrón regex: [A-Z][A-Za-z...]+ (\s+ [A-Z][A-Za-z...]+){0,4}
Significado: palabras que comienzan con mayúscula, hasta 5 palabras

Filtros:
  - Ignorar si está compuesto principalmente de preposiciones ("por", "con", "para")
  - Ignorar si tiene 0 palabras o está en stopwords
  - Ignorar si es exclusivamente mayúscula de 1-2 letras (ej: "a", "y")
```

**Fase 3: Deduplicación**

```
Para cada candidato:
  Normalizar a minúsculas
  Si ya existe (variante de otra entidad):
    Descartar
  Else:
    Agregar a resultado final
```

**Ejemplo completo**:
```
Entrada: "Estados Unidos y Colombia desarrollan un sistema autónomo para la Fuerza Aérea en la cumbre de seguridad espacial."

Fase 1 (diccionarios):
  - "Estados Unidos" ✓ (en COUNTRIES_AND_ORGS)
  - "Colombia" ✓
  - "sistema autónomo" ✓ (en TECH_TERMS)
  - "Fuerza Aérea" ✓
  - "cumbre" ✓ (en EVENT_TERMS)
  - "seguridad espacial" ✓

Fase 2 (heurística): ninguna nueva (ya cubiertas por diccionarios)

Fase 3 (deduplicación): todas únicas

Salida: ["Estados Unidos", "Colombia", "sistema autónomo", "Fuerza Aérea", "cumbre", "seguridad espacial"]
```

### Algoritmo de Extracción de Relaciones

**Objetivo**: Identificar vínculos semánticos entre pares de entidades.

**Patrones de relación**:
```python
RELATION_PATTERNS = [
    ("desarrolla", "desarrolla"),      # ES y formas EN
    ("regula", "regula"),
    ("coordina", "coordina"),
    ("afecta", "afecta"),
    ("opera", "opera"),
    ("develops", "desarrolla"),
    ("governs", "regula"),
    # ... más patrones ...
]
```

**Algoritmo en dos fases**:

**Fase 1: Emparejamiento de Entidades**

```
Para cada oración:
  1. Extraer entidades de la oración
  2. Si hay < 2 entidades:
     Pasar a siguiente oración
  
  3. Para cada relación R y cada par (Sujeto S, Objeto O):
     Si encontrado: S + "verbo_de_R" + O en la oración:
       Crear tripleta(S, R, O)
       Registrar segmento textual como evidencia

Razón: La coincidencia de verbos exactos reduce ruido vs heurística pura
```

**Ejemplo**:
```
Oración: "Estados Unidos desarrolla sistemas autónomos para defensa."

Entidades: ["Estados Unidos", "sistemas autónomos", "defensa"]

Para relación "desarrolla":
  Buscar en oración: "Estados Unidos" + "desarrolla" + "sistemas autónomos"?
  → ✓ Coincidencia encontrada
  → Tripleta: (Estados Unidos, desarrolla, sistemas autónomos)
```

**Fase 2: Fallback Heurístico**

```
Si Fase 1 no produjo tripletas:
  Para cada oración:
    Buscar patrón: <SUSTANTIVO> <VERBO_RELACIÓN> <RESTO>
    
    Limpiar <RESTO>:
      - Eliminar preposiciones finales (para, con, en, de, del, por, y)
      - Eliminar artículos iniciales (el, la, los, las, un, una)
      - Truncar a token siguiente separado por preposición
    
    Crear tripleta con <SUSTANTIVO> y <RESTO> limpio
```

**Ejemplo**:
```
Oración: "Colombia regula el uso de sistemas autónomos en su territorio."

Patrón: Colombia (desarrolla|regula|...) <RESTO>

Coincidencia: "Colombia regula" <RESTO donde RESTO = "el uso de sistemas autónomos en su territorio">

Limpiar RESTO:
  1. Eliminar "el" al inicio: "uso de sistemas autónomos en su territorio"
  2. Truncar por "de": "uso" (tomar solo la parte antes de "de")
  
Tripleta: (Colombia, regula, uso)

Nota: La Fase 1 habría sido mejor si existiera entidad "sistemas autónomos" explícita,
pero este fallback captura al menos la relación.
```

**Deduplicación**:
```
Tras generar todas las tripletas:
  Para cada tripleta única (S, R, O):
    Mantener solo una instancia
    (agregar más evidencias al mismo edge si hay múltiples)
```

---

## Construcción del Grafo

### Algoritmo General

```
Entrada: Iterador de chunks, cada uno con:
  - doc_id, chunk_id, texto

Salida: nx.DiGraph con nodos = entidades, aristas = relaciones

Pasos:
  1. Grafo = DiGraph vacío
  
  2. Para cada chunk en chunks:
       texto = chunk["texto"]
       doc_id = chunk["doc_id"]
       chunk_id = chunk["chunk_id"]
       
       triplets = extract_triplets_from_text(texto, doc_id, chunk_id)
       
       Para cada triplet en triplets:
         s = triplet["subject"]
         o = triplet["object"]
         r = triplet["relation"]
         doc_id_trip = triplet["doc_id"]
         chunk_id_trip = triplet["chunk_id"]
         evidence = triplet["evidence"]
         
         Si (s, o) existe en Grafo:
           # Actualizar: agregar más doc_ids, preservar evidencia máxima
           arista = Grafo.edges[s, o]
           doc_ids_actuales = arista["doc_ids"].split(";")
           doc_ids_actuales.add(doc_id_trip)
           arista["doc_ids"] = ";".join(sorted(set(doc_ids_actuales)))
         Else:
           # Nueva arista
           Grafo.add_edge(s, o, relation=r, doc_ids=doc_id_trip, 
                          chunk_ids=chunk_id_trip, evidence=evidence)
  
  3. Retornar Grafo
```

### Complejidad Computacional

| Métrica | Estimación |
|---------|-----------|
| Chunks | ~86,000 |
| Entidades por chunk | 3-7 (promedio 5) |
| Relaciones por chunk | 1-3 (promedio 2) |
| Nodos en grafo | ~5,000-10,000 |
| Aristas en grafo | ~10,000-20,000 |
| Tiempo de construcción | 5-15 min (CPU) |
| Tamaño GraphML | 1-5 MB |

---

## Integración con Ranking Vectorial

### Rationale: Por qué RRF

El grafo proporciona candidatos que el índice vectorial FAISS podría no recuperar por:

1. **Similitud semántica limitada**: FAISS busca por embeddings, que capturan similitud léxico-semántica pero pueden perder relaciones estructuradas.
2. **Contexto de dominio**: El grafo mantiene información de dominio explícita (ej: "ONU regula sistemas autónomos"), que FAISS codifica implícitamente.
3. **Reutilización**: Un documento relevante en múltiples relaciones grafo-ales aparece reforzado sin reentrenamiento.

### Puntuación RRF vs Alternativas

| Estrategia | Ventajas | Desventajas |
|-----------|----------|------------|
| **RRF (Elegida)** | Determinista, sin hiper-parámetros, combina rangs | No aprende pesos óptimos |
| Score Lineal | Simple | Escalas distintas causan sesgos |
| Modelo Generativo | Mayor precisión potencial | Viola restricciones del reto (8.4) |
| Grafo-primero | Máxima trazabilidad | Puede perder hits FAISS valiosos |

### Impacto en Resultados

**Hipótesis**: RRF mejora recall sin sacrificar precision.

**Mecanismo**:
- Si doc_id aparece en FAISS y en grafo: score RRF += 2 términos → sube más.
- Si doc_id aparece solo en grafo: score RRF = 1 término → sube moderadamente.
- Si doc_id aparece solo en FAISS: score RRF = 1 término → mantiene ranking base.

---

## Decisiones de Diseño

### D1: Grafo Complementario, No Principal

**Decisión**: El grafo NO es el motor de ranking; es capa de soporte.

**Justificación**:
- **Requisito del reto (Sec. 8.4)**: "Combinación de candidatos sin modelos generativos."
- **Robustez**: FAISS es más robusto a variedad de queries; grafo es mejor en dominio acotado.
- **Compatibilidad**: No requiere reentrenamiento; es preconstruido.

**Alternativa rechazada**: Usar grafo-first (ranking principal), FAISS como soporte.
- Riesgo: queries genéricas sin entidades detectables caen rendimiento.

### D2: NER/RE Minimal pero Determinista

**Decisión**: Usar heurística + diccionarios, NO modelos ML.

**Justificación**:
- **Reproducibilidad**: Determinista en cualquier máquina; sin randomness de modelos.
- **Velocidad**: Construcción grafo en minutos, no horas.
- **Mantenibilidad**: Reglas transparentes y auditables.

**Tradeoff**: Menor cobertura vs modelos spaCy/BERT, pero suficiente para dominio del reto.

**Extensión futura**: Reemplazar con `spaCy es_core_news_sm` o `distilBERT` si métricas justifican overhead.

### D3: Preservación de Trazabilidad

**Decisión**: Cada arista preserva `doc_id`, `chunk_id`, `evidence`.

**Justificación**:
- **Auditoría**: Verificar por qué documento se reforzó.
- **Debugging**: Reproducir decisiones sin "caja negra".
- **Confianza**: Evaluadores pueden validar la cadena de razonamiento.

### D4: RRF para Fusión

**Decisión**: Usar RRF (Reciprocal Rank Fusion) vs score linear.

**Justificación**:
- **Escalas distintas**: FAISS scores (0-1) vs recuento de apariciones en grafo → RRF los normaliza.
- **Sin hiper-parámetros**: k₀=60 es estándar en IR; no requiere tuning.
- **Determinista**: Orden de entrada no afecta, solo posiciones.

### D5: Evidencia Incrustada en Fragmentos

**Decisión**: Agregar evidencia del grafo al texto del fragmento vs campos paralelos.

**Justificación**:
- **Esquema reto**: No hay campos adicionales en fragmentos; solo `text`.
- **Legibilidad**: El evaluador ve la evidencia directamente.
- **Cumplimiento**: Respeta límite 250 palabras con precedencia: contenido chunk > evidencia grafo.

### D6: Fallback a Demo Chunks

**Decisión**: Si `data/chunks.jsonl` no existe, usar chunks demo.

**Justificación**:
- **Testing**: Permitir CI/CD sin corpus completo.
- **Reproducibilidad**: Resultados demo predecibles para validación.

---

## Limitaciones Conocidas

### L1: Cobertura de Entidades Limitada

**Problema**: Diccionarios predefinidos cubren solo dominios de reto; entidades genéricas pueden perderse.

**Impacto**: Baja → queries sobre dominios no cubiertos carecen de reforzamiento grafo.

**Mitigación**: 
- Diccionarios extensos preentrenados en dominio.
- Heurística de sustantivos propios como fallback.

### L2: Relaciones Simples

**Problema**: Solo 5-7 tipos de relaciones; modelos reales tienen cientos.

**Impacto**: Baja → relaciones semánticamente válidas pueden no extractarse.

**Mitigación**: Extensibles; agregar patrones bajo demanda de evaluación.

### L3: Sin Resolución de Correferencia

**Problema**: "Sistema autónomo", "sistema autonomo", "SA" se tratan como entidades distintas.

**Impacto**: Fragmentación de nodos; menos conexiones en grafo.

**Mitigación**: Normalizador agresivo; fuzzy matching en búsqueda.

### L4: Monosemia Asumida

**Problema**: "Banco" (institución vs geográfico) no se desambigua.

**Impacto**: Baja potencial; pero corpus de reto es acotado.

**Mitigación**: Contexto de chunk como desambiguador implícito.

### L5: Escalabilidad a 1M+ Chunks

**Problema**: Construcción grafo es O(n_chunks * n_entities_per_chunk). Para corpus masivos, puede ser lento.

**Impacto**: No relevante para etapa 1; corpus ~86K chunks.

**Mitigación**: Paralelización con `multiprocessing` si necesario en etapas futuras.

---

## Guía de Uso

### Instalación de Dependencias

```bash
pip install networkx==3.4.2
```

### Construcción del Grafo (Desde Cero)

```bash
# Opción 1: Con chunks reales
python build_knowledge_graph.py data/chunks.jsonl \
  --output entrega/base_vectorial/grafo/grafo.graphml \
  --triplets entrega/base_vectorial/grafo/tripletas.jsonl

# Opción 2: Con pipeline orquestado
python scripts/corrida_grafo.py --chunks data/chunks.jsonl
```

**Salida esperada**:
```
✓ grafo.graphml (1-5 MB)
✓ tripletas.jsonl (500K-1M líneas)
```

### Carga del Grafo en Runtime

```python
import networkx as nx

grafo = nx.read_graphml("entrega/base_vectorial/grafo/grafo.graphml")

# Inspeccionar
print(f"Nodos: {grafo.number_of_nodes()}")
print(f"Aristas: {grafo.number_of_edges()}")

# Iterar
for u, v, data in grafo.edges(data=True):
    print(f"{u} --{data['relation']}--> {v}")
    print(f"  doc_ids: {data['doc_ids']}")
    print(f"  evidence: {data['evidence'][:100]}")
```

### Generación de Resultados Completa

```bash
python entrega/generador.py
```

**Archivos generados**:
```
entrega/resultados.jsonl               (50 líneas, esquema reto)
entrega/base_vectorial/grafo/
  ├── evidencia_consultas.jsonl        (sidecar con evidencia)
  ├── grafo.graphml                    (grafo persistido)
  └── tripletas.jsonl                  (tripletas para auditoría)
```

### Inspección de Resultados

```python
import json

# Verificar estructura resultados.jsonl
with open("entrega/resultados.jsonl") as f:
    for line in f:
        obj = json.loads(line)
        print(f"Query: {obj['query_id']}")
        print(f"  Documentos: {len(obj['documents'])}")
        print(f"  Fragmentos: {len(obj['fragments'])}")
        
        # Verificar evidencia del grafo
        for frag in obj['fragments']:
            if "Evidencia del grafo" in frag['text']:
                print(f"    ✓ Frag #{frag['rank']} con evidencia grafo")

# Verificar sidecar de evidencia
with open("entrega/base_vectorial/grafo/evidencia_consultas.jsonl") as f:
    for line in f:
        evid = json.loads(line)
        for doc_evid in evid['graph_evidence']:
            if doc_evid['graph_evidence']:
                print(f"Query {evid['query_id']}, doc {doc_evid['doc_id']}:")
                for trip in doc_evid['graph_evidence']:
                    print(f"  {trip['subject']} --{trip['relation']}--> {trip['object']}")
```

### Validación Manual del Grafo

```bash
# Inspeccionar tripletas
head -5 entrega/base_vectorial/grafo/tripletas.jsonl

# Verificar integridad GraphML
python -c "
import networkx as nx
g = nx.read_graphml('entrega/base_vectorial/grafo/grafo.graphml')
print(f'Grafo válido: {nx.is_directed_acyclic_graph(g)}')  # Nota: puede tener ciclos
print(f'Nodos: {g.number_of_nodes()}, Aristas: {g.number_of_edges()}')
"
```

### Depuración de Query Específica

```python
# Depurar por qué se incluyó un documento
from knowledge_graph import extract_entities_from_text, doc_ids_relacionados_por_consulta
import networkx as nx

query = "¿Qué hace Estados Unidos?"
entities = extract_entities_from_text(query)
print(f"Entidades detectadas: {entities}")

grafo = nx.read_graphml("entrega/base_vectorial/grafo/grafo.graphml")
doc_ids = doc_ids_relacionados_por_consulta(query, grafo)
print(f"Doc_ids del grafo: {doc_ids}")

# Mostrar evidencia específica
for entidad in entities:
    for nodo in grafo.nodes():
        if entidad.lower() in nodo.lower():
            print(f"\nNodo coincidente: {nodo}")
            for u, v, data in grafo.edges(nodo, data=True):
                print(f"  → {v} ({data['relation']}, doc_ids: {data['doc_ids']})")
            for u, v, data in grafo.in_edges(nodo, data=True):
                print(f"  ← {u} ({data['relation']}, doc_ids: {data['doc_ids']})")
```

---

## Verificación y Pruebas

### Suite de Pruebas

```bash
python -m pytest tests/test_knowledge_graph.py -v
```

**Pruebas clave**:

#### `test_extract_triplets_from_text_detects_domain_relations`
Verifica que los patrones de relación se extraigan correctamente.

**Input**:
```
"Estados Unidos desarrolla sistemas autónomos para defensa. 
 Colombia regula el uso de estos sistemas. 
 La ONU coordina con la Fuerza Aérea."
```

**Validaciones**:
- Relaciones detectadas: {"desarrolla", "regula", "coordina"}
- Sujetos: "Estados Unidos" presente
- Objetos: "sistemas autónomos" presente

#### `test_extract_entities_from_text_detects_country_org_technology_and_event`
Verifica detección de entidades de distintas categorías.

**Input**:
```
"Estados Unidos y Colombia desarrollan un sistema autónomo para la 
 Fuerza Aérea en la cumbre de seguridad espacial."
```

**Validaciones**:
- Países: "Estados Unidos", "Colombia"
- Organizaciones: "Fuerza Aérea"
- Tecnologías: "sistema autónomo"
- Eventos: "cumbre"

#### `test_build_graph_from_chunks_keeps_doc_and_chunk_traceability`
Verifica que trazabilidad se preserve en el grafo.

**Input**: Chunks con doc_id, chunk_id

**Validaciones**:
- Grafo tiene nodos y aristas
- Aristas contienen doc_id, chunk_id en atributos
- GraphML se puede serializar y deserializar

#### `test_fusionar_resultados_vectoriales_uses_graph_candidates_with_rrf`
Verifica fusión RRF correcta.

**Input**:
- vector_ranking = ["DOC-1", "DOC-2", "DOC-3", "DOC-4"]
- graph_candidates = ["DOC-2", "DOC-5", "DOC-6"]

**Validaciones**:
- DOC-2 (ambas listas) sube a primera posición
- DOC-5, DOC-6 (solo grafo) aparecen en ranking
- DOC-1, DOC-3, DOC-4 (solo vector) mantienen orden

### Checklist de Validación Manual

```
[ ] Chunks reales: data/chunks.jsonl existe y tiene ~86K líneas
[ ] Construcción: python build_knowledge_graph.py ... ejecuta sin errores
[ ] Grafo: entrega/base_vectorial/grafo/grafo.graphml existe (1-5 MB)
[ ] Tripletas: entrega/base_vectorial/grafo/tripletas.jsonl existe (legible)
[ ] Nodos: grafo tiene entre 5K y 10K nodos
[ ] Aristas: grafo tiene entre 10K y 20K aristas
[ ] Generador: python entrega/generador.py ejecuta sin errores
[ ] Resultados: entrega/resultados.jsonl tiene 50 líneas
[ ] Fragmentos: cada uno tiene "text" con ≤ 250 palabras
[ ] Evidencia: algunos fragmentos contienen "Evidencia del grafo"
[ ] Sidecar: entrega/base_vectorial/grafo/evidencia_consultas.jsonl existe
```

---

## Extensiones Futuras

### E1: NER/RE Más Robusta

**Mejora**: Integrar modelo de NER preentrenado.

```python
import spacy

# Opción A: spaCy + modelo en español
nlp = spacy.load("es_core_news_sm")
doc = nlp("Estados Unidos desarrolla sistemas autónomos.")

for ent in doc.ents:
    print(f"{ent.text} ({ent.label_})")
```

**Impacto**: Mayor cobertura de entidades, relaciones más precisas.
**Costo**: +10-50 ms por chunk; requiere modelos adicionales.

### E2: Resolución de Correferencia

**Mejora**: Mapear "Sistema autónomo" → "Sistema autonomo" → "SA".

```python
from neuralcoref import NeuralCoref
# Integrar antes de extracción
```

**Impacto**: Grafo más conectado; menos fragmentación de nodos.
**Costo**: Complejidad; requerimiento neural (más lento).

### E3: Scoring Basado en Aprendizaje

**Mejora**: Entrenar pesos óptimos para RRF + features adicionales.

```
score_final = w_vector * score_vector + w_grafo * score_grafo + w_length * len(chunk)
```

**Impacto**: Potencialmente mejor F1@3.
**Costo**: Datos de entrenamiento; riesgo de overfitting al dev set.

### E4: Grafo Dinámico con Actualizaciones

**Mejora**: Permitir agregar nuevas relaciones en runtime sin reconstruir.

**Impacto**: Menor latencia para nuevos datos.
**Costo**: Gestión de consistencia; transacciones.

### E5: Visualización Interactiva

**Mejora**: Web UI con Cytoscape.js para inspeccionar grafo.

```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.20.0/cytoscape.min.js"></script>
```

**Impacto**: Debugging visual; comunicación con stakeholders.
**Costo**: Frontend dev.

---

## Apéndices

### A. Diccionarios de Dominios

**COUNTRIES_AND_ORGS** (18 entradas):
```
"Estados Unidos", "Colombia", "China", "Rusia", "EE.UU.", "ONU", "OTAN",
"Unión Europea", "Fuerza Aérea", "América Latina", "LATAM", "Alianza",
"Ministerio de Defensa", "Ministerio de Relaciones Exteriores"
```

**TECH_TERMS** (14 entradas):
```
"IA", "inteligencia artificial", "sistema autónomo", "algoritmo",
"software", "tecnología", "dron", "satélite", "nube",
"ciberseguridad", "seguridad espacial"
```

**EVENT_TERMS** (13 entradas):
```
"cumbre", "conferencia", "acuerdo", "incidente", "guerra", "conflicto",
"proliferación", "cooperación", "colisión"
```

**ENTITY_STOPWORDS** (25 entradas):
```
Español: "la", "el", "los", "las", "de", "del", "y", "en", "para", "con", "por", "sobre", "como", "según", "una", "unos", "unas"
English: "the", "and", "for", "with", "of", "in", "on", "to", "a", "an"
```

### B. Patrones de Relación

```python
RELATION_PATTERNS = [
    ("desarrolla", "desarrolla"),
    ("regula", "regula"),
    ("coordina", "coordina"),
    ("afecta", "afecta"),
    ("opera", "opera"),
    # English variants
    ("develops", "desarrolla"),
    ("develop", "desarrolla"),
    ("governs", "regula"),
    ("coordinates", "coordina"),
    ("affects", "afecta"),
    ("operates", "opera"),
]
```

### C. Parámetros de Configuración

| Parámetro | Valor | Notas |
|-----------|-------|-------|
| TOP_K (FAISS search) | 200 | Holgura para dedup + agregación |
| N_DOCS | 3 | Fijo por reto |
| N_FRAGS | 10 | Fijo por reto |
| MAX_PALABRAS | 250 | Tope duro reto |
| k₀ (RRF dampening) | 60 | Estándar IR |
| MAX_TOKENS (encoder) | 8192 | Debe coincidir con encode_index.py |

### D. Estructura de Directorios

```
c:\Users\danna\OneDrive\Documentos\GitHub\AdAstra2026-CATSNebula\
├── README.md                                (documentación principal)
├── KNOWLEDGE_GRAPH_IMPLEMENTATION.md        (este archivo)
├── knowledge_graph.py                       (módulo núcleo)
├── build_knowledge_graph.py                 (script construcción)
├── entrega/
│   ├── generador.py                         (integración final)
│   ├── consultas.jsonl                      (50 preguntas)
│   ├── resultados.jsonl                     (salida reto)
│   ├── base_vectorial/
│   │   ├── encoder_bge-m3/
│   │   │   ├── index.faiss                  (índice FAISS)
│   │   │   └── metadata.jsonl               (alineación FAISS)
│   │   └── grafo/
│   │       ├── grafo.graphml                (grafo exportado)
│   │       ├── tripletas.jsonl              (tripletas auditoría)
│   │       └── evidencia_consultas.jsonl    (sidecar evidencia)
├── scripts/
│   ├── corrida_grafo.py                     (pipeline orquestado)
│   └── ... otros scripts
├── tests/
│   ├── test_knowledge_graph.py              (suite pruebas grafo)
│   └── ... otros tests
└── data/
    ├── chunks.jsonl                         (chunks reales ~86K)
    └── ... otros datos
```

### E. Referencias Externas

- **NetworkX**: https://networkx.org/
- **GraphML Standard**: http://graphml.graphdrawing.org/
- **RRF (Reciprocal Rank Fusion)**: https://dl.acm.org/doi/10.1145/1571941.1572114
- **FAISS**: https://github.com/facebookresearch/faiss
- **BGE-M3**: https://huggingface.co/BAAI/bge-m3

### F. Glosario

| Término | Definición |
|---------|-----------|
| **Entidad** | Sustantivo o frase que representa un concepto del dominio (ej: "Estados Unidos") |
| **Relación** | Verbo que conecta dos entidades (ej: "desarrolla") |
| **Tripleta** | Tupla (sujeto, relación, objeto) con metadatos |
| **Grafo** | Estructura de datos con nodos (entidades) y aristas (relaciones) |
| **doc_id** | Identificador oficial del documento asignado por organizadores |
| **chunk_id** | Identificador único del segmento dentro de un documento |
| **RRF** | Técnica de fusión de dos rankings sin necesidad de scores numéricos |
| **FAISS** | Librería de búsqueda de similitud vectorial (Facebook AI Similarity Search) |
| **Sidecar** | Artefacto paralelo que acompaña la salida principal sin modificarla |
| **GraphML** | Formato estándar XML para grafos |

---

## Conclusión

El **grafo de conocimiento** implementado cumple con los objetivos de la Sección 8.4 del reto CODEFEST AdAstra 2026:

✅ **Combinación de candidatos**: RRF fusiona FAISS + grafo sin modelos generativos.
✅ **Trazabilidad**: Cada resultado lleva evidencia semántica explícita.
✅ **Determinismo**: Sin aleatoriedad; reproducible en cualquier máquina.
✅ **Compatibilidad**: Integrado sin romper esquema de `resultados.jsonl`.
✅ **Escalabilidad**: Construcción en minutos para corpus de ~86K chunks.

El sistema está listo para evaluación y extensión según necesidades futuras.

