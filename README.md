# SchemaLink IE Engine

**Schema-guided information extraction from biomedical text using Large Language Models.**

The SchemaLink IE engine takes a schema developed with the SchemaLink webapp (SchemaLink.biodata.di.unimi.it) and a text as input to extract structured entities and relations — grounded to biomedical ontologies — that conform to the schema.

---

## Overview

The SchemaLink IE engine implements a **dependency-aware multi-step pipeline** that:

1. Parses the schema and builds a class-dependency DAG
2. Runs topologically-sorted GPT calls — each step conditioned on the results of its dependencies
3. Applies algorithmic post-processing rules (deduplication, cardinality, inheritance resolution)
4. Optionally grounds extracted entities to biomedical ontologies via [OAK](https://incatools.github.io/ontology-access-kit/)

```
   Schema     ──┐
                ├──▶  SchemaLink Engine  ──▶  Entities and Triples
Biomedical Text ┘         (LLM + DAG)           (JSON / LinkML)
```

---

## Installation

```bash
pip install schemalink-engine
```

Or from source:

```bash
git clone https://github.com/BioDataUniMI/schemalink-engine.git
cd schemalink-engine
pip install -e .
```

Set your OpenAI API key:

```bash
schemalink api-key set sk-your-key-here
```

---

## Quick Start

Given a schema file (`schema.yaml`, built with [SchemaLink](https://SchemaLink.biodata.di.unimi.it)) and a text file (`text.txt`):

```bash
# Standard extraction — dependency-aware by default
schemalink extract schema.yaml text.txt

# With ontology grounding (recommended when the schema defines annotators)
schemalink extract schema.yaml text.txt --ground

# Flat extraction — all classes extracted independently, ignoring dependencies
schemalink extract schema.yaml text.txt --flat
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
from schemalink_engine.pipeline import run_extraction_pipeline

# Dependency-aware extraction (default)
run_extraction_pipeline(
    schema_path="schema.yaml",
    text_path="text.txt",
    with_dependencies=True,   # True by default
    show_results=True,
)

# With ontology grounding
run_extraction_pipeline(
    schema_path="schema.yaml",
    text_path="text.txt",
    ground_entities={"mode": "auto"},
    show_results=True,
)
```

---

## Grounding

By default, extraction returns raw LLM labels with no ontology IDs. Pass `--ground` to enable grounding:

```bash
schemalink extract schema.yaml text.txt --ground
```

The grounding method is **chosen automatically** based on the `annotators:` field defined in the schema class:

| Annotator in schema | Grounding method |
|---|---|
| `sqlite:obo:mondo`, `sqlite:obo:chebi`, etc. | OAK exact match against a local ontology SQLite database |
| `cellosaurus`, `ncbigene`, `mesh_d`, etc. | Lookup against a local reference table |

**OAK databases** are downloaded automatically on first use from the [bbop-sqlite](https://s3.amazonaws.com/bbop-sqlite/) S3 bucket and cached in `~/.data/oaklib/`. If the OAK library is unavailable, the engine falls back to querying the `.db` files directly via SQLite. If no local database or lookup table is found, the entity is left ungrounded.

Classes with no `annotators:` field are always left ungrounded regardless of the `--ground` flag.

---

## Web Interface

The SchemaLink IE engine ships with a Flask production server that exposes a REST API and a streaming SSE endpoint:

```bash
python production_server.py
# → http://localhost:15002/engine/api/v2/extract  (streaming)
# → http://localhost:15002/engine/api/v1/extract  (sync)
```

The full web application is available at [SchemaLink](https://SchemaLink.biodata.di.unimi.it).

---

## Supported Ontologies

SchemaLink supports any ontology available as an OAK SQLite database, including:

`chebi` · `go` · `mondo` · `hp` · `hgnc` · `pr` · `mesh` · `pw` · `doid` · `ncit` · `uberon` · `cl` · `ro` · and 200+ more via the OBO Foundry

---

## CLI Reference

```
schemalink extract <schema> <text> [options]

Options:
  --ground              Enable ontology grounding (method chosen automatically
                        from the annotators defined in the schema)
  --flat                Disable dependency-aware extraction — extract all
                        classes independently (default: dependency-aware ON)
  --add_guidelines      Include schema-level guidelines in prompts
  --classes A B C       Extract only specific classes
  --model <name>        Override the GPT model for this run
  --show_prompts        Print the prompts sent to the LLM (API call still made)
  --show_results        Print the extraction output to stdout
  --json_schema         Print the parsed JSON schema and exit

schemalink api-key set <key>   Save your OpenAI API key
schemalink api-key check       Check whether an API key is configured
schemalink api-key remove      Remove the saved API key
schemalink model set <name>    Set the default GPT model
schemalink models              List all supported GPT models
```

---

## Architecture

```
schemalink_engine/
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
@inproceedings{schemalinkIEengine2026,
author={Emanuele Cavalleri, Ali Rastegar Mojarad, J. Harry Caufield, Justin T. Reese, Christopher J. Mungall, and Marco Mesiti},
title = "{Schema-Driven Structured Information Extraction from Biomedical Literature via Large Language Models}", 
year = {2026},
publisher = {Association for Computing Machinery},
address = {New York, NY, USA},
booktitle = {Proceedings of the 35th ACM International Conference on Information and Knowledge Management},
location = {Rome, Italy},
series = {CIKM '26},
notes = {To appear.}
}
```