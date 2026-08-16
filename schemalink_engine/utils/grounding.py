from __future__ import annotations

import os
import sys
import sqlite3
import logging
from difflib import SequenceMatcher
from typing import Optional


# Suppress OAK's noisy warnings about invalid CURIEs
logging.getLogger("root").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# OAK database setup helpers
# ---------------------------------------------------------------------------

_OAK_S3_BASE = 'https://s3.amazonaws.com/bbop-sqlite'

# Ontologies known to be unavailable on the bbop-sqlite S3 bucket.
_OAK_S3_UNAVAILABLE = {
    'addicto', 'aeo', 'afo', 'afpo', 'bcgo', 'bmont', 'cao', 'ccf', 'ceph',
    'coho', 'covoc', 'dc', 'dcat', 'dcterms', 'ehdaa2', 'emap', 'enm',
    'ensemblglossary', 'evorao', 'exmo', 'gallont', 'gaz', 'gexo', 'gmho',
    'gold', 'gscmixs', 'hra', 'ictv', 'idocovid19', 'idomal',
    'lifestylefactors', 'lipidmaps', 'mcro', 'medgen', 'miro', 'ngbo',
    'nmrcv', 'oio', 'om', 'omiabis', 'orth', 'owl', 'pbpko', 'phi',
    'prefer', 'probonto', 'rdfs', 'reproduceme', 'reto', 'rexo',
    'schemaorg_http', 'schemaorg_https', 'semapv', 'shareloc', 'sibo',
    'skos', 'slm', 'slso', 'snomed', 'srao', 't4fs', 'tads', 'tao',
    'teddy', 'tgma', 'unimod', 'uniprotrdfs', 'vario', 'vsao',
}

# Approximate compressed download sizes (MB) — used to warn before large downloads.
_ONTOLOGY_DOWNLOAD_SIZE_MB = {
    'chebi':      620,
    'ncbitaxon':  210,
    'pr':         110,
    'go':          55,
    'mondo':       42,
    'mesh':        28,
    'hp':          14,
    'hgnc':         9,
    'doid':         5,
    'cl':           4,
    'uberon':      18,
    'ncit':        50,
}


def _extract_oak_annotators_from_schema_file(schema_path: str) -> list[str]:
    """Parse a LinkML schema YAML file and return all unique sqlite:obo: ontology names."""
    try:
        import yaml as _yaml
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = _yaml.safe_load(f)
    except Exception:
        return []

    names: set[str] = set()
    for cls_info in (schema.get('classes') or {}).values():
        if not isinstance(cls_info, dict):
            continue
        annotators_raw = (cls_info.get('annotations') or {}).get('annotators', '')
        if not isinstance(annotators_raw, str):
            continue
        for part in annotators_raw.split(','):
            part = part.strip()
            if part.startswith('sqlite:obo:'):
                names.add(part[len('sqlite:obo:'):].lower())
    return sorted(names)


def ensure_oak_databases(schema_path: str) -> None:
    """Check that all OAK SQLite databases required by the schema are present.

    Downloads any missing databases from the bbop-sqlite S3 bucket with a
    tqdm progress bar. Already-cached databases are skipped silently.
    """
    import gzip
    import shutil
    import requests
    from tqdm import tqdm

    oak_dir = os.path.join(os.path.expanduser('~'), '.data', 'oaklib')
    if os.environ.get('OAK_DATA_DIR'):
        oak_dir = os.environ['OAK_DATA_DIR']
    os.makedirs(oak_dir, exist_ok=True)

    needed = _extract_oak_annotators_from_schema_file(schema_path)
    if not needed:
        return

    missing = [
        name for name in needed
        if not os.path.exists(os.path.join(oak_dir, f'{name}.db'))
        and name not in _OAK_S3_UNAVAILABLE
    ]

    if not missing:
        return

    print()
    print('  📦 Ontology databases needed for grounding:')
    for name in needed:
        db_path = os.path.join(oak_dir, f'{name}.db')
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            print(f'     ✅  {name}.db  ({size_mb:.0f} MB, already cached)')
        elif name in _OAK_S3_UNAVAILABLE:
            print(f'     ⚠️  {name}.db  — not available for download, grounding will be skipped')
        else:
            hint = _ONTOLOGY_DOWNLOAD_SIZE_MB.get(name)
            size_str = f'~{hint} MB compressed' if hint else 'size unknown'
            print(f'     ⬇️  {name}.db  ({size_str}) — will be downloaded now')
    print()

    for name in missing:
        gz_url = f'{_OAK_S3_BASE}/{name}.db.gz'
        db_path = os.path.join(oak_dir, f'{name}.db')
        gz_path = db_path + '.gz'

        try:
            response = requests.get(gz_url, stream=True, timeout=120)
            response.raise_for_status()
            total_bytes = int(response.headers.get('content-length', 0))

            # Download the .gz file with a progress bar
            with tqdm(
                total=total_bytes if total_bytes else None,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                desc=f'  Downloading {name}.db',
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]',
                leave=True,
            ) as bar:
                with open(gz_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            bar.update(len(chunk))

            # Decompress in place
            print(f'  🔧 Decompressing {name}.db …', end=' ', flush=True)
            with gzip.open(gz_path, 'rb') as f_in, open(db_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(gz_path)
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            print(f'done ({size_mb:.0f} MB)')

        except requests.exceptions.ConnectionError:
            _cleanup_partial(gz_path, db_path)
            print(f'\n  ⚠️  No internet connection — could not download {name}.db.')
            print(f'      Grounding for "{name}" will be skipped this run.')
        except requests.exceptions.Timeout:
            _cleanup_partial(gz_path, db_path)
            print(f'\n  ⚠️  Download timed out for {name}.db.')
            print(f'      Grounding for "{name}" will be skipped this run.')
        except requests.exceptions.HTTPError as e:
            _cleanup_partial(gz_path, db_path)
            print(f'\n  ⚠️  HTTP error downloading {name}.db: {e}')
            print(f'      Grounding for "{name}" will be skipped this run.')
        except Exception as e:
            _cleanup_partial(gz_path, db_path)
            print(f'\n  ⚠️  Failed to download {name}.db: {e}')
            print(f'      Grounding for "{name}" will be skipped this run.')

    print()


def _cleanup_partial(*paths: str) -> None:
    """Remove partial download files silently."""
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

try:
    from oaklib import get_adapter
    from oaklib.datamodels.text_annotator import TextAnnotationConfiguration
    from oaklib.interfaces import TextAnnotatorInterface
    # Suppress OAK library warnings
    logging.getLogger("oaklib").setLevel(logging.ERROR)
    OAK_AVAILABLE = True
except Exception as e:
    OAK_AVAILABLE = False
    if os.environ.get("SCHEMALINK_CLI") != "1":
        print(f"⚠️  OAK library could not be loaded ({e}). Using local SQLite .db files instead.")

# Direct SQLite grounding — used when OAK is unavailable but .db files exist.
# Queries rdfs_label_statement in OAK-format SQLite databases using built-in sqlite3.
_SQLITE_DB_CACHE: dict = {}  # ontology_name -> sqlite3.Connection | None

def _get_sqlite_db_paths():
    """Return candidate directories containing OAK SQLite .db files."""
    dirs = []
    if os.environ.get("OAK_DATA_DIR"):
        dirs.append(os.environ["OAK_DATA_DIR"])
    dirs.append(os.path.join(os.path.expanduser("~"), ".data", "oaklib"))
    # Project-relative .data/oaklib
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.dirname(os.path.dirname(_here))
    dirs.append(os.path.join(_root, ".data", "oaklib"))
    return dirs

def _get_sqlite_conn(ontology_name: str):
    """Return a cached sqlite3.Connection for the given ontology, or None."""
    if ontology_name in _SQLITE_DB_CACHE:
        return _SQLITE_DB_CACHE[ontology_name]
    for d in _get_sqlite_db_paths():
        db_path = os.path.join(d, f"{ontology_name}.db")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                _SQLITE_DB_CACHE[ontology_name] = conn
                return conn
            except Exception:
                pass
    _SQLITE_DB_CACHE[ontology_name] = None
    return None

def _entity_name_variants(entity_name: str) -> list:
    """Return mention variants so 'Parkinson's disease' also matches 'Parkinson disease'."""
    variants = [entity_name]
    cleaned = entity_name.replace("\u2019", "'").replace("\u2018", "'")
    if cleaned != entity_name:
        variants.append(cleaned)
    if "'" in cleaned:
        variants.append(cleaned.replace("'s ", " ").replace("'s", "s").replace("'", ""))
    seen = set()
    out = []
    for v in variants:
        key = v.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _ground_with_sqlite(entity_name: str, ontology_name: str) -> Optional[str]:
    """
    Query an OAK SQLite .db file the same way OAK does: labels + synonyms
    in the `statements` table (rdfs:label, oio:hasExactSynonym, …).
    """
    conn = _get_sqlite_conn(ontology_name)
    if conn is None:
        return None
    _ROOT_IDS = {"MONDO:0000001", "GO:0003674", "GO:0008150", "GO:0005575",
                 "HP:0000001", "CHEBI:24431"}
    _PRED_RANK = {
        "rdfs:label": 0,
        "oio:hasExactSynonym": 1,
        "oio:hasRelatedSynonym": 2,
        "oio:hasNarrowSynonym": 3,
        "oio:hasBroadSynonym": 4,
    }
    try:
        rows = []
        for variant in _entity_name_variants(entity_name):
            try:
                cur = conn.execute(
                    """
                    SELECT subject, predicate, value FROM statements
                    WHERE lower(value) = lower(?)
                      AND predicate IN (
                          'rdfs:label',
                          'oio:hasExactSynonym',
                          'oio:hasRelatedSynonym',
                          'oio:hasNarrowSynonym',
                          'oio:hasBroadSynonym'
                      )
                      AND subject NOT LIKE '_:%'
                    """,
                    (variant,),
                )
                rows = cur.fetchall()
            except Exception:
                rows = []
            if rows:
                break
        if not rows:
            return None
        rows = [r for r in rows
                if r[0] not in _ROOT_IDS and "obsolete" not in (r[2] or "").lower()]
        if not rows:
            return None
        rows.sort(key=lambda r: (_PRED_RANK.get(r[1], 9), r[0]))
        raw_id = rows[0][0]
        if ":" in raw_id:
            prefix, local = raw_id.split(":", 1)
            raw_id = f"{prefix.upper()}:{local}"
        return raw_id
    except Exception:
        return None


class GroundingManager:
    """
    Manages entity grounding using OAK (Ontology Access Kit) and lookup tables.
    Grounds entities by matching their names to canonical IDs using OAK adapters
    for sqlite:obo: annotators, or lookup tables as fallback.
    Supports both exact and fuzzy matching with configurable threshold.
    """
    
    # Per-table column configuration: table_name -> (name_col, id_col, skip_header)
    # Default is (0, 1, False) — name in col 0, id in col 1, no header
    TABLE_COLUMN_CONFIG = {
        'ncbigene'   : (2, 1, False),  # taxon_id | gene_id | symbol
        'medic'      : (1, 0, False),  # id | name
        'mesh_d'     : (1, 0, False),  # id | name
        'ncbitaxon'  : (0, 1, False),  # name | id
        'dbsnp'      : (0, 1, True),   # Name | RS# (dbSNP)  — has header
        'cellosaurus': (0, 1, True),   # nomeCellLine | ID    — has header
    }

    def __init__(self, threshold=1.0, mode='exact'):
        """
        Initialize GroundingManager.
        
        Args:
            threshold (float): Similarity threshold for fuzzy matching (0.0 to 1.0).
                              1.0 = exact match only, 0.7 = 70% similarity, etc.
            mode (str): Grounding mode - 'exact', 'partial', 'fuzzy', or 'monarch'
        """
        self.lookup_tables = {}  # Cache for loaded lookup tables
        self.oak_adapters = {}  # Cache for OAK adapters
        self.project_root = self._get_project_root()
        self.threshold = threshold
        self.mode = mode
    
    def _get_project_root(self):
        """Get the project root directory (where lookup_tables/ folder is located)."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))  # Go up two levels from utils/
        return project_root
    
    def _is_oak_annotator(self, annotator_value):
        """
        Check if annotator is an OAK-compatible annotator (sqlite:obo: format).
        
        Args:
            annotator_value (str): e.g., "sqlite:obo:pr" or "hgnc"
        
        Returns:
            bool: True if it's an OAK annotator, False otherwise
        """
        if not annotator_value:
            return False
        return annotator_value.startswith("sqlite:obo:") or annotator_value.startswith("translator:")
    
    def _get_oak_adapter(self, annotator_value):
        """
        Get or create an OAK adapter for the given annotator.
        Uses existing database files if available, otherwise tries to download.
        
        Args:
            annotator_value (str): OAK annotator string (e.g., "sqlite:obo:pr")
        
        Returns:
            TextAnnotatorInterface: OAK adapter, or None if OAK is not available or failed
        """
        if not OAK_AVAILABLE:
            return None
        
        # Check if we need to initialize or retry (if database now exists)
        needs_init = annotator_value not in self.oak_adapters
        
        # Get OAK data directory from environment variable or use default
        # Check multiple possible locations
        possible_dirs = []
        if os.environ.get('OAK_DATA_DIR'):
            possible_dirs.append(os.environ.get('OAK_DATA_DIR'))
        possible_dirs.append(os.path.join(os.path.expanduser("~"), ".data", "oaklib"))
        possible_dirs.append("/home/alirm/.data/oaklib")  # Explicit server path
        possible_dirs.append(os.path.join(self.project_root, ".data", "oaklib"))  # Project-relative
        
        # If adapter was previously marked as failed, check if database exists now
        if not needs_init and annotator_value.startswith("sqlite:obo:"):
            ontology_name = annotator_value.replace("sqlite:obo:", "")
            for oak_data_dir in possible_dirs:
                db_path = os.path.join(oak_data_dir, f"{ontology_name}.db")
                if os.path.exists(db_path) and self.oak_adapters.get(annotator_value) is None:
                    needs_init = True
                    if annotator_value in self.oak_adapters:
                        del self.oak_adapters[annotator_value]
                    break
        
        if needs_init:
            try:
                # Check if database already exists locally
                if annotator_value.startswith("sqlite:obo:"):
                    ontology_name = annotator_value.replace("sqlite:obo:", "")
                    db_path = None
                    
                    # Try all possible directories
                    for oak_data_dir in possible_dirs:
                        test_path = os.path.join(oak_data_dir, f"{ontology_name}.db")
                        if os.path.exists(test_path):
                            db_path = test_path
                            break
                    
                    if db_path:
                        # Use existing database directly with absolute path
                        abs_path = os.path.abspath(db_path)
                        adapter = get_adapter(f"sqlite:///{abs_path}")
                    else:
                        # Try to download database
                        adapter = get_adapter(annotator_value)
                else:
                    # For non-sqlite:obo: annotators, use as-is
                    adapter = get_adapter(annotator_value)
                
                self.oak_adapters[annotator_value] = adapter
            except Exception as e:
                print(f"⚠️ Failed to initialize OAK adapter {annotator_value}: {e}")
                # Mark as failed so we don't keep trying
                self.oak_adapters[annotator_value] = None
                return None
        
        # Return None if adapter was marked as failed
        adapter = self.oak_adapters.get(annotator_value)
        return adapter if adapter else None
    
    def _parse_annotator(self, annotator_value):
        """
        Parse annotator value to extract lookup table name (for non-OAK annotators).
        
        Args:
            annotator_value (str): e.g., "sqlite:obo:pr" or "hgnc"
        
        Returns:
            str: Lookup table name (e.g., "pr", "hgnc") or None if invalid
        """
        if not annotator_value:
            return None
        
        # Remove known prefixes
        if annotator_value.startswith("sqlite:obo:"):
            table_name = annotator_value.replace("sqlite:obo:", "")
        elif annotator_value.startswith("schemalink:"):
            table_name = annotator_value.replace("schemalink:", "")
        else:
            table_name = annotator_value
        
        return table_name.strip() if table_name else None
    
    def _load_lookup_table(self, table_name):
        """
        Load a lookup table from file.
        
        Args:
            table_name (str): Name of the lookup table (e.g., "pr", "hgnc")
        
        Returns:
            dict: Dictionary mapping entity names to sets of IDs {name: {id1, id2, ...}}
        """
        # Check if already cached
        if table_name in self.lookup_tables:
            return self.lookup_tables[table_name]
        
        # Construct file path
        lookup_file = os.path.join(self.project_root, "lookup_tables", f"{table_name}.txt")
        
        lookup_dict = {}
        
        # Get column config for this table (default: name=col0, id=col1, no header)
        name_col, id_col, skip_header = self.TABLE_COLUMN_CONFIG.get(table_name, (0, 1, False))
        min_cols = max(name_col, id_col) + 1
        
        try:
            if not os.path.exists(lookup_file):
                pass
                self.lookup_tables[table_name] = {}
                return {}
            
            with open(lookup_file, "r", encoding="utf-8") as file:
                for i, line in enumerate(file):
                    if skip_header and i == 0:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    
                    parts = line.split("\t")
                    if len(parts) >= min_cols:
                        entity_name = parts[name_col].strip()
                        entity_id = parts[id_col].strip()
                        if entity_name and entity_id:
                            # Collect ALL ids per label (case-insensitive).
                            # Multiple lines for the same label are all accumulated.
                            key = entity_name.lower()
                            if key not in lookup_dict:
                                lookup_dict[key] = set()
                            lookup_dict[key].add(entity_id)
            
            # Cache the loaded table
            self.lookup_tables[table_name] = lookup_dict
            
            print(f"✅ Loaded lookup table: {table_name} ({len(lookup_dict)} unique labels)")
            
        except Exception as e:
            print(f"⚠️ Error loading lookup table {table_name}: {e}")
            self.lookup_tables[table_name] = {}
        
        return self.lookup_tables[table_name]
    
    def _fuzzy_match(self, entity_name, lookup_table):
        """
        Perform fuzzy matching against lookup table.
        
        Args:
            entity_name (str): The entity name to match
            lookup_table (dict): The lookup table dictionary {name: set of ids}
        
        Returns:
            tuple: (matched_name, set_of_ids, similarity_score) if found, None otherwise
        """
        if not entity_name or not lookup_table:
            return None
        
        entity_name_lower = entity_name.lower()
        best_match = None
        best_score = 0.0
        
        for lookup_name, ids in lookup_table.items():
            similarity = SequenceMatcher(None, entity_name_lower, lookup_name).ratio()
            
            if similarity > best_score and similarity >= self.threshold:
                best_score = similarity
                best_match = (lookup_name, ids, similarity)
        
        return best_match
    
    def ground_entity(self, entity_name, lookup_table):
        """
        Ground a single entity by searching in the lookup table.
        Uses exact matching if threshold is 1.0, otherwise uses fuzzy matching.
        
        Args:
            entity_name (str): The entity name to ground
            lookup_table (dict): The lookup table dictionary {name: set of ids}
        
        Returns:
            tuple: (entity_name, set_of_ids) if found, None otherwise
        """
        if not entity_name or not lookup_table:
            return None
        
        # Exact matching (threshold = 1.0)
        if self.threshold >= 1.0:
            entity_name_lower = entity_name.lower()
            if entity_name_lower in lookup_table:
                ids = lookup_table[entity_name_lower]
                return (entity_name, ids)
        else:
            # Fuzzy matching
            fuzzy_result = self._fuzzy_match(entity_name, lookup_table)
            if fuzzy_result:
                matched_name, ids, similarity = fuzzy_result
                return (entity_name, ids)
        
        return None
    
    def _is_root_term(self, entity_id, annotator_value):
        """
        Check if an entity ID is a root/generic term that should be filtered out.
        
        Args:
            entity_id (str): Entity ID (CURIE)
            annotator_value (str): OAK annotator string
        
        Returns:
            bool: True if it's a root term, False otherwise
        """
        # Known root terms for common ontologies
        root_terms = {
            'sqlite:obo:mondo': ['MONDO:0000001'],  # disease
            'sqlite:obo:go': ['GO:0003674', 'GO:0008150', 'GO:0005575'],  # molecular_function, biological_process, cellular_component
            'sqlite:obo:hp': ['HP:0000001'],  # All
            'sqlite:obo:pr': [],  # PR doesn't have a single root
            'sqlite:obo:hgnc': [],  # HGNC doesn't have root terms
            'sqlite:obo:chebi': ['CHEBI:24431'],  # chemical entity
            'sqlite:obo:drugbank': [],
        }
        
        # Check if it's a known root term
        root_list = root_terms.get(annotator_value, [])
        if entity_id in root_list:
            return True
        
        # Also check by prefix
        ontology_name = annotator_value.replace('sqlite:obo:', '') if annotator_value.startswith('sqlite:obo:') else ''
        if ontology_name == 'mondo' and entity_id == 'MONDO:0000001':
            return True
        if ontology_name == 'go' and entity_id in ['GO:0003674', 'GO:0008150', 'GO:0005575']:
            return True
        if ontology_name == 'hp' and entity_id == 'HP:0000001':
            return True
        if ontology_name == 'chebi' and entity_id == 'CHEBI:24431':
            return True
        
        return False
    
    def _rank_results(self, results_list, entity_name, adapter, min_similarity=0.0):
        """
        Rank OAK results to prefer more specific matches.
        With whole text matching, all results are already exact matches, so similarity
        filtering is less relevant, but ranking by specificity (exact label match, etc.)
        is still useful.
        
        Args:
            results_list: List of annotation results
            entity_name: Original entity name
            adapter: OAK adapter
            min_similarity: Minimum similarity threshold (0.0-1.0) to accept a result.
                          With whole text matching, typically set to 0.0 since matches are already exact.
        
        Returns:
            List of results sorted by specificity (most specific first), filtered by similarity
        """
        if not results_list:
            return []
        
        scored_results = []
        entity_lower = entity_name.lower()
        
        for result in results_list:
            entity_id = result.object_id
            
            # Get label for similarity scoring
            try:
                label = adapter.label(entity_id) if hasattr(adapter, 'label') else ''
                label_lower = label.lower() if label else ''
            except:
                label_lower = ''
            
            # Score based on:
            # 1. Exact label match = highest priority
            # 2. Label contains entity name = medium priority  
            # 3. Entity name contains label = lower priority
            # 4. Text similarity = lowest priority
            
            score = 0
            similarity = 0.0
            
            if label_lower == entity_lower:
                score = 1000  # Exact match
                similarity = 1.0
            elif entity_lower in label_lower:
                # Label contains entity - calculate similarity
                from difflib import SequenceMatcher
                similarity = SequenceMatcher(None, entity_lower, label_lower).ratio()
                score = 500 + (similarity * 100)  # Boost score with similarity
            elif label_lower and label_lower in entity_lower:
                # Entity contains label - calculate similarity
                from difflib import SequenceMatcher
                similarity = SequenceMatcher(None, entity_lower, label_lower).ratio()
                score = 300 + (similarity * 100)
            else:
                # Use text similarity
                from difflib import SequenceMatcher
                similarity = SequenceMatcher(None, entity_lower, label_lower).ratio()
                score = similarity * 100
            
            # Only include if similarity meets threshold
            if similarity >= min_similarity:
                scored_results.append((score, result, similarity))
        
        # Sort by score (highest first)
        scored_results.sort(key=lambda x: x[0], reverse=True)
        
        return [result for score, result, similarity in scored_results]
    
    def _ground_entity_with_oak(self, entity_name, annotator_value):
        """
        Ground a single entity using OAK adapter with whole text matching only.
        Filters out root/generic terms and prefers more specific matches.
        
        Uses matches_whole_text=True for exact/whole phrase matching:
        - "Parkinson disease" → finds MONDO:0005180
        - "Parkinson" → no match (not the full phrase)
        
        Args:
            entity_name (str): The entity name to ground
            annotator_value (str): OAK annotator string (e.g., "sqlite:obo:pr")
        
        Returns:
            str: Entity ID (CURIE) if found, None otherwise
        """
        if not OAK_AVAILABLE or not entity_name:
            return None
        
        adapter = self._get_oak_adapter(annotator_value)
        if not adapter or not isinstance(adapter, TextAnnotatorInterface):
            return None
        
        try:
            # Use whole text matching only (exact/whole phrase match)
            config = TextAnnotationConfiguration(matches_whole_text=True)
            results = adapter.annotate_text(entity_name, config)
            # Convert generator to list
            results_list = list(results) if results else []
            
            # Filter out root terms
            filtered_results = [r for r in results_list if not self._is_root_term(r.object_id, annotator_value)]
            
            if filtered_results:
                # Rank by specificity (no similarity threshold needed for whole text match)
                ranked_results = self._rank_results(filtered_results, entity_name, adapter, min_similarity=0.0)
                if ranked_results:
                    return ranked_results[0].object_id
                
        except Exception as e:
            print(f"  ⚠️ OAK grounding error for '{entity_name}' with {annotator_value}: {e}")
        
        return None
    
    def ground_entities(self, entities, annotator_value, threshold=None, entity_class=None):
        """
        Ground a list of entities using the specified annotator(s).
        Uses OAK for sqlite:obo: annotators, falls back to lookup tables otherwise.
        Supports multiple annotators separated by commas.
        
        Args:
            entities (list): List of entity names (strings)
            annotator_value (str): Annotator value from schema (e.g., "sqlite:obo:pr" or "sqlite:obo:mondo, sqlite:obo:hp")
            threshold (float): Optional threshold override for this grounding operation
        
        Returns:
            list: List of grounded entities with 'id' field added
        """
        # Use provided threshold or fall back to instance threshold
        current_threshold = threshold if threshold is not None else self.threshold
        
        # Handle multiple annotators separated by commas
        annotator_values = [a.strip() for a in annotator_value.split(',') if a.strip()]
        
        if not annotator_values:
            # No annotator specified, return entities as-is
            return entities
        
        # Separate OAK annotators from lookup table annotators
        # If mode is 'monarch', use OAK for sqlite:obo: annotators
        # Otherwise (exact/partial/fuzzy), convert sqlite:obo: to lookup table names
        oak_annotators = []
        lookup_annotators = []
        
        for annotator in annotator_values:
            if self._is_oak_annotator(annotator):
                # 'auto' and 'monarch' both use OAK for sqlite:obo:* annotators.
                # The method is determined by the annotator format in the schema,
                # not by a user-supplied mode flag.
                if self.mode in ('monarch', 'auto'):
                    oak_annotators.append(annotator)
                else:
                    # For explicit exact/partial/fuzzy modes, use lookup tables
                    if annotator.startswith("sqlite:obo:"):
                        lookup_table_name = annotator.replace("sqlite:obo:", "")
                        lookup_annotators.append(lookup_table_name)
                    else:
                        lookup_annotators.append(annotator)
            else:
                lookup_annotators.append(annotator)
        
        # Track which OAK annotators failed to initialize (for fallback)
        oak_failed_annotators = []
        if self.mode in ('monarch', 'auto'):
            for annotator in oak_annotators:
                # Try to get adapter using our method (which checks for existing DBs)
                adapter = self._get_oak_adapter(annotator)
                if adapter is None:
                    oak_failed_annotators.append(annotator)
        
        # Ground entities
        grounded_entities = []
        removed_count = 0
        
        for entity in entities:
            # Handle both string and dict formats
            if isinstance(entity, dict):
                entity_name = entity.get("name", entity.get("label", ""))
            else:
                entity_name = str(entity)
            
            if not entity_name:
                removed_count += 1
                continue
            
            # Try OAK grounding first (only if mode is 'monarch' and OAK annotators are available)
            entity_id = None
            all_ids = set()
            used_oak = False

            for annotator in oak_annotators:
                # Skip if we know this adapter failed
                if annotator in oak_failed_annotators:
                    continue
                entity_id = self._ground_entity_with_oak(entity_name, annotator)
                if entity_id:
                    all_ids.add(entity_id)
                    used_oak = True
                    break

            # Same local .db files OAK uses. Run when OAK is missing *or* did not match.
            if not entity_id and self.mode in ('monarch', 'auto'):
                for annotator in oak_annotators:
                    if not annotator.startswith("sqlite:obo:"):
                        continue
                    ontology_name = annotator.replace("sqlite:obo:", "")
                    sqlite_id = _ground_with_sqlite(entity_name, ontology_name)
                    if sqlite_id:
                        entity_id = sqlite_id
                        all_ids.add(sqlite_id)
                        used_oak = True
                        break

            # Fall back to lookup tables if OAK didn't work
            # This includes both explicit lookup annotators and OAK annotators that failed
            if not entity_id:
                lookup_tables = []
                
                # Add explicit lookup annotators
                for annotator in lookup_annotators:
                    table_name = self._parse_annotator(annotator)
                    if table_name:
                        lookup_table = self._load_lookup_table(table_name)
                        if lookup_table:
                            lookup_tables.append(lookup_table)
                
                # Add failed OAK annotators as lookup table fallbacks
                for annotator in oak_failed_annotators:
                    table_name = self._parse_annotator(annotator)
                    if table_name:
                        lookup_table = self._load_lookup_table(table_name)
                        if lookup_table:
                            lookup_tables.append(lookup_table)
                
                # Try lookup tables — collect ALL ids from all matching tables
                all_ids = set()
                for lookup_table in lookup_tables:
                    grounded = self._ground_entity_with_threshold(entity_name, lookup_table, current_threshold)
                    if grounded:
                        entity_name, ids = grounded
                        all_ids.update(ids)
                if all_ids and not entity_id:
                    entity_id = next(iter(all_ids))  # primary id (first in set)
            
            if entity_id:
                # Create grounded entity dict with all ids
                ids_list = sorted(all_ids) if all_ids else [entity_id]
                grounded_entity = {
                    "name": entity_name.lower(),
                    "id": ids_list[0],     # primary id for backward compatibility
                    "ids": ids_list        # full list of all matching ids
                }
                grounded_entities.append(grounded_entity)
            else:
                removed_count += 1
        
        if removed_count > 0 and os.environ.get("SCHEMALINK_CLI") != "1":
            print(f"  ⚠️ Removed {removed_count} ungrounded entities")
        
        # Print summary
        # Check if any OAK adapters actually worked
        oak_worked = any(self.oak_adapters.get(a) is not None for a in oak_annotators if a not in oak_failed_annotators)
        sqlite_direct_used = (self.mode in ('monarch', 'auto')
                              and any(a.startswith("sqlite:obo:") for a in oak_annotators)
                              and not oak_worked)
        if oak_annotators and oak_worked and not oak_failed_annotators:
            method_str = "OAK"
        elif sqlite_direct_used:
            method_str = "SQLite direct (OAK unavailable)"
        elif oak_annotators and oak_failed_annotators:
            method_str = "lookup tables (OAK failed, using fallback)"
        else:
            method_str = "lookup tables"
        if oak_annotators and lookup_annotators and oak_worked:
            method_str = "OAK + lookup tables"
        
        annotator_names = []
        if oak_annotators and oak_worked:
            annotator_names.extend([a.replace("sqlite:obo:", "") for a in oak_annotators if self.oak_adapters.get(a) is not None])
        if lookup_annotators:
            annotator_names.extend([self._parse_annotator(a) for a in lookup_annotators if self._parse_annotator(a)])
        # Add failed OAK annotators that fell back to lookup tables
        for annotator in oak_failed_annotators:
            table_name = self._parse_annotator(annotator)
            if table_name and table_name not in annotator_names:
                annotator_names.append(table_name)
        
        annotators_str = ", ".join(annotator_names) if annotator_names else "unknown"
        if os.environ.get("SCHEMALINK_CLI") != "1":
            print(f"  ✅ Grounded {len(grounded_entities)} entities using {annotators_str} ({method_str})")
        
        return grounded_entities
    
    def _ground_entity_with_threshold(self, entity_name, lookup_table, threshold):
        """
        Ground a single entity using a specific threshold.
        
        Args:
            entity_name (str): The entity name to ground
            lookup_table (dict): The lookup table dictionary {name: set of ids}
            threshold (float): The similarity threshold to use
        
        Returns:
            tuple: (entity_name, set_of_ids) if found, None otherwise
        """
        if not entity_name or not lookup_table:
            return None
        
        # Exact matching (threshold = 1.0)
        if threshold >= 1.0:
            entity_name_lower = entity_name.lower()
            if entity_name_lower in lookup_table:
                ids = lookup_table[entity_name_lower]
                return (entity_name, ids)
        else:
            # Fuzzy matching — find best matching label and return all its ids
            entity_name_lower = entity_name.lower()
            best_match = None
            best_score = 0.0
            
            for lookup_name, ids in lookup_table.items():
                similarity = SequenceMatcher(None, entity_name_lower, lookup_name).ratio()
                
                if similarity > best_score and similarity >= threshold:
                    best_score = similarity
                    best_match = (lookup_name, ids)
            
            if best_match:
                matched_name, ids = best_match
                return (entity_name, ids)
        
        return None

