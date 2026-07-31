# SchemaLink

**Schema-guided information extraction from biomedical text using Large Language Models.**

SchemaLink takes a [LinkML](https://linkml.io/) schema and a free-text passage as input and extracts structured entities and relations — grounded to biomedical ontologies — that conform to the schema.

---

## Overview

Biomedical knowledge graphs require structured data extracted from unstructured literature. Existing LLM-based extractors rely on flat, one-shot prompts and ignore the structure of the target schema. SchemaLink addresses this with a **dependency-aware multi-step pipeline** that:

1. Parses the LinkML schema and builds a class-dependency DAG
2. Runs topologically-sorted GPT calls — each step conditioned on the results of its dependencies
3. Applies algorithmic post-processing rules (deduplication, cardinality, inheritance resolution)
4. Grounds extracted entities to biomedical ontologies via [OAK](https://incatools.github.io/ontology-access-kit/) (exact, partial, fuzzy, or Monarch KG matching)

```
LinkML Schema ──┐
                ├──▶  SchemaLink Engine  ──▶  Grounded Knowledge Graph
Biomedical Text ┘         (LLM + DAG)              (JSON / LinkML)
```

---

## Installation

```bash
pip install schemalink
```

Or from source:

```bash
git clone https://github.com/4lirastegar/schemalink-engine.git
cd schemalink-engine
pip install -e .
```

Set your OpenAI API key:

```bash
schemalink api-key set sk-your-key-here
```

---

## Quick Start

Given a schema file (`schema.yaml`) and a text file (`text.txt`):

```bash
# Basic extraction
schemalink extract schema.yaml text.txt

# Dependency-aware multi-step extraction (recommended)
schemalink extract schema.yaml text.txt --add_dependencies

# With ontology grounding (exact match)
schemalink extract schema.yaml text.txt --add_dependencies --ground exact

# Fuzzy grounding (70% similarity threshold)
schemalink extract schema.yaml text.txt --add_dependencies --ground 7
```

### Example Schema (Drug–Disease)

```yaml
id: https://example.org/ddi
name: drug_disease_schema
prefixes:
  MONDO: https://purl.obolibrary.org/obo/mondo/mondo-international.owl
  CHEBI: http://purl.obolibrary.org/obo/chebi.owl
imports:
  - ontogpt:core
  - linkml:types
classes:
  DrugTreatsDiseaseRelationship:
    is_a: Triple
    slot_usage:
      subject:
        range: Drug
      object:
        range: Disease
      predicate:
        range: DrugTreatsDiseasePredicate
  DrugTreatsDiseasePredicate:
    is_a: RelationshipType
    attributes:
      id:
        pattern: 'Treats'
  Disease:
    is_a: NamedEntity
    id_prefixes: [MONDO]
    annotations:
      annotators: sqlite:obo:mondo
  Drug:
    is_a: NamedEntity
    id_prefixes: [CHEBI]
    annotations:
      annotators: sqlite:obo:chebi
```

### Example Output

```json
{
  "Disease": {
    "mentions": [
      { "label": "Parkinson's disease", "id": "MONDO:0005180" }
    ]
  },
  "Drug": {
    "mentions": [
      { "label": "levodopa", "id": "CHEBI:15765" },
      { "label": "carbidopa", "id": "CHEBI:3395" }
    ]
  },
  "DrugTreatsDiseaseRelationship": {
    "mentions": [
      { "subject": "CHEBI:15765", "predicate": "Treats", "object": "MONDO:0005180" }
    ]
  }
}
```

---

## Python API

```python
from schemalink.pipeline import run_extraction_pipeline

run_extraction_pipeline(
    schema_path="schema.yaml",
    text_path="text.txt",
    with_dependencies=True,
    ground_entities={"mode": "exact", "threshold": 1.0},
    show_results=True,
)
```

---

## Grounding Modes

| Mode | Flag | Description |
|---|---|---|
| None | *(default)* | No grounding — returns raw LLM labels |
| Exact | `--ground exact` | OAK exact label match against ontology SQLite DB |
| Partial | `--ground partial` | Word-overlap matching |
| Fuzzy | `--ground 7` | Fuzzy string similarity (0–10 scale) |
| Monarch | `--ground monarch` | Monarch Initiative knowledge graph lookup |

OAK databases are downloaded automatically on first use from the [bbop-sqlite](https://s3.amazonaws.com/bbop-sqlite/) S3 bucket.

---

## Web Interface

SchemaLink ships with a Flask production server that exposes a REST API and a streaming SSE endpoint:

```bash
python production_server.py
# → http://localhost:15002/engine/api/v2/extract  (streaming)
# → http://localhost:15002/engine/api/v1/extract  (sync)
```

A full web application (React + FastAPI) is available at [SchemaLink Web](https://schemalink.anacleto.di.unimi.it).

---

## Supported Ontologies

SchemaLink supports any ontology available as an OAK SQLite database, including:

`chebi` · `go` · `mondo` · `hp` · `hgnc` · `pr` · `mesh` · `pw` · `doid` · `ncit` · `uberon` · `cl` · `ro` · and 200+ more via the OBO Foundry

---

## CLI Reference

```
schemalink extract <schema> <text> [options]

Options:
  --add_dependencies    Dependency-aware multi-step extraction
  --add_guidelines      Include schema-level guidelines in prompts
  --classes A B C       Extract only specific classes
  --ground [mode]       Entity grounding (exact / partial / monarch / 0-10)
  --show_prompts        Print generated GPT prompts
  --show_results        Print extraction output
  --json_schema         Print the parsed JSON schema and exit

schemalink api-key set <key>   Set OpenAI API key
schemalink api-key check       Verify API key
schemalink model <name>        Set GPT model (e.g. gpt-4o, gpt-4o-mini)
schemalink models              List available models
```

---

## Architecture

```
schemalink/
├── cli.py                  # CLI entry point
├── pipeline.py             # Main extraction pipeline
├── schema_convertor.py     # LinkML YAML → JSON schema parser
├── api_key_manager.py      # OpenAI key & model management
└── utils/
    ├── generate_dependencies.py          # DAG construction from schema
    ├── dag_generator.py                  # DAG visualization
    ├── extract_named_entity_classes.py   # NER class identification
    ├── process_named_entities.py         # GPT-based NER
    ├── handle_inherited_classes.py       # Inheritance resolution
    ├── process_inherited_entities.py     # Inherited class extraction
    ├── handle_relationship_classes.py    # Relation class identification
    ├── process_relationship_entities.py  # GPT-based RE
    ├── grounding.py                      # OAK-based entity grounding
    └── some_helper.py                    # Topological sort, utilities
```

---

## Requirements

- Python ≥ 3.8
- OpenAI API key
- See `requirements.txt` for full dependency list

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Citation

If you use SchemaLink in your research, please cite:

```bibtex
@software{rastegar2025schemalink,
  author       = {Rastegar, Ali},
  title        = {{SchemaLink}: Schema-Guided Information Extraction from Biomedical Text},
  year         = {2025},
  publisher    = {GitHub},
  url          = {https://github.com/4lirastegar/schemalink-engine},
  institution  = {Università degli Studi di Milano, AnacletoLAB}
}
```

---

## Acknowledgements

Developed at [AnacletoLAB](https://anacletolab.di.unimi.it/), Università degli Studi di Milano, as part of a Master's thesis in Computer Science.
