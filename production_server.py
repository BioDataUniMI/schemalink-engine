#!/usr/bin/env python3
"""
Production Web Console for Schemalink
Configured for deployment on port 15002
"""

from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
from flasgger import Swagger
import os
import sys
import json
import subprocess
import tempfile
import threading
import queue
import time
from werkzeug.utils import secure_filename
from schemalink_engine.pipeline import run_extraction_pipeline
from schemalink_engine.api_key_manager import APIKeyManager

# Configure matplotlib to use non-GUI backend to avoid threading issues
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend

# ---------------------------------------------------------------------------
# OAK database pre-download helper
# ---------------------------------------------------------------------------
# Ontologies present in our API catalog but NOT available on the bbop-sqlite
# S3 bucket — attempting to download these will always 404, so we skip them.
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

_OAK_S3_BASE = 'https://s3.amazonaws.com/bbop-sqlite'


def _get_oak_data_dir() -> str:
    """Return the directory where OAK stores its SQLite .db files."""
    if os.environ.get('OAK_DATA_DIR'):
        return os.environ['OAK_DATA_DIR']
    return os.path.join(os.path.expanduser('~'), '.data', 'oaklib')


def _collect_sqlite_annotators(schema_yaml: str) -> list[str]:
    """Parse a LinkML schema YAML and return all unique sqlite:obo:<name> IDs."""
    try:
        import yaml as _yaml
        schema = _yaml.safe_load(schema_yaml)
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


def _ensure_oak_dbs(schema_yaml: str) -> list[str]:
    """
    For every sqlite:obo:<name> annotator in *schema_yaml*, ensure that
    <name>.db exists in the OAK data directory.  Downloads and decompresses
    any missing files from the bbop-sqlite S3 bucket.

    Returns a list of human-readable status messages for logging / SSE.
    """
    import gzip
    import shutil
    import urllib.request

    oak_dir = _get_oak_data_dir()
    os.makedirs(oak_dir, exist_ok=True)

    names = _collect_sqlite_annotators(schema_yaml)
    messages: list[str] = []

    for name in names:
        db_path = os.path.join(oak_dir, f'{name}.db')
        if os.path.exists(db_path):
            continue  # already available

        if name in _OAK_S3_UNAVAILABLE:
            messages.append(
                f'⚠️  OAK DB for "{name}" is not available on S3 — '
                f'grounding will be skipped for this ontology.'
            )
            continue

        gz_url = f'{_OAK_S3_BASE}/{name}.db.gz'
        gz_path = db_path + '.gz'
        try:
            messages.append(f'⬇️  Downloading OAK DB: {name}.db …')
            urllib.request.urlretrieve(gz_url, gz_path)
            with gzip.open(gz_path, 'rb') as f_in, open(db_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(gz_path)
            messages.append(f'✅ OAK DB ready: {name}.db')
        except Exception as exc:
            messages.append(
                f'⚠️  Failed to download OAK DB for "{name}": {exc} — '
                f'grounding will be skipped for this ontology.'
            )
            # Clean up partial files
            for p in (gz_path, db_path):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass

    return messages

def cleanup_files():
    """Clean up files before running commands"""
    import shutil
    
    # 1. Delete every file in generated/graphs
    graphs_dir = "generated/graphs"
    if os.path.exists(graphs_dir):
        for filename in os.listdir(graphs_dir):
            file_path = os.path.join(graphs_dir, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
    
    # 2. Make empty the files in generated/prompts (don't delete them just make them empty)
    prompts_dir = "generated/prompts"
    if os.path.exists(prompts_dir):
        for filename in os.listdir(prompts_dir):
            file_path = os.path.join(prompts_dir, filename)
            if os.path.isfile(file_path):
                with open(file_path, 'w') as f:
                    f.write('')
    
    # 3. Make empty the files in generated/response_formats
    response_formats_dir = "generated/response_formats"
    if os.path.exists(response_formats_dir):
        for filename in os.listdir(response_formats_dir):
            file_path = os.path.join(response_formats_dir, filename)
            if os.path.isfile(file_path):
                with open(file_path, 'w') as f:
                    f.write('')
    
    # 4. Make empty the files in output/
    output_dir = "output"
    if os.path.exists(output_dir):
        for filename in os.listdir(output_dir):
            file_path = os.path.join(output_dir, filename)
            if os.path.isfile(file_path):
                with open(file_path, 'w') as f:
                    f.write('')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

CORS(app)

swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/engine/api/apispec.json",
            "rule_filter": lambda rule: rule.rule.startswith("/engine/api/v1"),
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/engine/api/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/engine/api/docs",
}
swagger_template = {
    "info": {
        "title": "SchemaLink Extraction API",
        "description": "REST API for schema-guided information extraction using SchemaLink.",
        "version": "1.0.0",
    },
    "basePath": "/",
}
swagger = Swagger(app, config=swagger_config, template=swagger_template)

# Global variables for command execution
command_queue = queue.Queue()
command_results = {}
command_counter = 0

class WebConsole:
    def __init__(self):
        self.api_manager = APIKeyManager()
    
    def execute_command(self, command_type, **kwargs):
        """Execute a command and return results"""
        global command_counter
        command_counter += 1
        command_id = f"cmd_{command_counter}"
        
        try:
            if command_type == "extract":
                return self._execute_extract(command_id, **kwargs)
            elif command_type == "generate_prompts":
                return self._execute_generate_prompts(command_id, **kwargs)
            elif command_type == "api_key_set":
                return self._execute_api_key_set(command_id, **kwargs)
            elif command_type == "api_key_check":
                return self._execute_api_key_check(command_id)
            elif command_type == "api_key_remove":
                return self._execute_api_key_remove(command_id)
            elif command_type == "model_set":
                return self._execute_model_set(command_id, **kwargs)
            elif command_type == "models_list":
                return self._execute_models_list(command_id)
            else:
                return {"error": f"Unknown command type: {command_type}"}
        except Exception as e:
            return {"error": str(e)}
    
    def _execute_extract(self, command_id, schema_path, text_path, **kwargs):
        """Execute extraction command"""
        try:
            # Clean up files before running
            cleanup_files()
            
            # Capture output
            import io
            import sys
            from contextlib import redirect_stdout, redirect_stderr
            
            output_buffer = io.StringIO()
            error_buffer = io.StringIO()
            
            with redirect_stdout(output_buffer), redirect_stderr(error_buffer):
                ground_entities_value = kwargs.get('ground_entities')
                run_extraction_pipeline(
                    schema_path=schema_path,
                    text_path=text_path,
                    with_dependencies=kwargs.get('add_dependencies', False),
                    add_guidelines=kwargs.get('add_guidelines', False),
                    selected_classes=kwargs.get('classes'),
                    show_prompts=kwargs.get('show_prompts', False),
                    show_results=kwargs.get('show_results', False),
                    generate_prompts_only=False,
                    json_schema=kwargs.get('json_schema', False),
                    ground_entities=ground_entities_value if ground_entities_value is not None else False
                )
            
            raw_output = output_buffer.getvalue()
            error = error_buffer.getvalue()

            # Parse TRACE: lines into structured per-class trace, strip them from log.
            # List-type events (NE_INIT, NE_FILTERED, NE_FILTER_REMOVED, NE_GROUNDED,
            # NE_GROUNDING_REMOVED, RE_INIT, RE_FINAL, RE_FILTERED_REMOVED) are
            # accumulated so multiple child classes emitting under the same abstract
            # parent key are merged into one list.
            _ACCUMULATE_EVENTS = {
                'NE_INIT', 'NE_FILTERED', 'NE_FILTER_REMOVED',
                'NE_GROUNDED', 'NE_GROUNDING_REMOVED',
                'RE_INIT', 'RE_FINAL', 'RE_FILTERED_REMOVED',
            }
            # Scalar events — just store latest value (not accumulated)
            _SCALAR_EVENTS = {'NE_TIMING', 'RE_TIMING', 'NE_DONE', 'RE_DONE', 'META'}
            pipeline_trace = {}
            clean_lines = []
            for line in raw_output.split('\n'):
                if line.startswith('TRACE:'):
                    try:
                        parts = line.split(':', 3)
                        if len(parts) == 4:
                            _, event_type, class_name, data_json = parts
                            data = json.loads(data_json)
                            if class_name not in pipeline_trace:
                                pipeline_trace[class_name] = {}
                            if event_type in _ACCUMULATE_EVENTS and isinstance(data, list):
                                existing = pipeline_trace[class_name].get(event_type, [])
                                pipeline_trace[class_name][event_type] = existing + data
                            else:
                                pipeline_trace[class_name][event_type] = data
                    except Exception:
                        pass
                else:
                    clean_lines.append(line)
            output = '\n'.join(clean_lines)

            # Read generated files based on user selections
            results = {}
            add_dependencies = kwargs.get('add_dependencies', False)
            show_prompts = kwargs.get('show_prompts', False)
            show_results = kwargs.get('show_results', False)
            json_schema = kwargs.get('json_schema', False)
            
            # 1. Show results if requested
            if show_results:
                if add_dependencies:
                    response_file = "output/generated_responses.json"
                else:
                    response_file = "output/generated_responses_without_dependencies.json"
                
                if os.path.exists(response_file):
                    try:
                        with open(response_file, "r") as f:
                            content = f.read().strip()
                            if content:  # Only load if file has content
                                results["responses"] = json.loads(content)
                    except (json.JSONDecodeError, ValueError) as e:
                        print(f"Warning: Could not parse JSON file {response_file}: {e}")
                        results["responses"] = {"error": f"Could not parse response file: {e}"}
            
            # 2. Show prompts if requested
            if show_prompts:
                if add_dependencies:
                    prompt_files = [
                        "generated/prompts/final_namedentity_prompts.json",
                        "generated/prompts/inherited_class_prompts.json",
                        "generated/prompts/relationship_classes_prompts.json"
                    ]
                else:
                    prompt_files = [
                        "generated/prompts/final_namedentity_prompts.json"
                    ]
                
                prompts = []
                for file_path in prompt_files:
                    if os.path.exists(file_path):
                        try:
                            with open(file_path, "r") as f:
                                content = f.read().strip()
                                if content:  # Only load if file has content
                                    # Parse JSON while preserving order using OrderedDict
                                    import collections
                                    parsed_content = json.loads(content, object_pairs_hook=collections.OrderedDict)
                                    
                                    # Ensure schema_prompt comes before attribute_prompt within each class
                                    ordered_content = collections.OrderedDict()
                                    for class_name, class_data in parsed_content.items():
                                        ordered_class = collections.OrderedDict()
                                        if 'schema_prompt' in class_data:
                                            ordered_class['schema_prompt'] = class_data['schema_prompt']
                                        if 'attribute_prompt' in class_data:
                                            ordered_class['attribute_prompt'] = class_data['attribute_prompt']
                                        # Add any other keys that might exist
                                        for other_key in class_data:
                                            if other_key not in ['schema_prompt', 'attribute_prompt']:
                                                ordered_class[other_key] = class_data[other_key]
                                        ordered_content[class_name] = ordered_class
                                    
                                    prompts.append({
                                        "filename": os.path.basename(file_path),
                                        "filepath": file_path,
                                        "content": ordered_content
                                    })
                        except (json.JSONDecodeError, ValueError) as e:
                            print(f"Warning: Could not parse JSON file {file_path}: {e}")
                            continue
                
                if prompts:
                    results["prompts"] = prompts
            
            # 3. Show JSON schema if requested
            if json_schema:
                response_format_files = [
                    "generated/response_formats/named_entity_response_formats.json",
                    "generated/response_formats/inherited_response_formats.json",
                    "generated/response_formats/relationship_response_formats.json"
                ]
                
                response_formats = {}
                for file_path in response_format_files:
                    if os.path.exists(file_path):
                        try:
                            with open(file_path, "r") as f:
                                content = f.read().strip()
                                if content:  # Only load if file has content
                                    response_formats[os.path.basename(file_path)] = json.loads(content)
                        except (json.JSONDecodeError, ValueError) as e:
                            print(f"Warning: Could not parse JSON file {file_path}: {e}")
                            continue
                
                if response_formats:
                    results["response_formats"] = response_formats
            
            # 4. Show DAG if dependencies are enabled
            if add_dependencies:
                dag_files = [
                    ("generated/graphs/class_dependency_graph.png", "DAG1: Initial dependency structure - Raw dependency graph from schema.yaml"),
                    ("generated/graphs/class_dependency_graph2.png", "DAG2: Dependency graph after removing classes with no instances"),
                    ("generated/graphs/class_dependency_graph3.png", "DAG3: Final dependency structure after all named entity and inherited class processing")
                ]
                
                existing_dags = []
                for dag_file, description in dag_files:
                    if os.path.exists(dag_file):
                        existing_dags.append({
                            "filename": os.path.basename(dag_file),
                            "description": description
                        })
                
                if existing_dags:
                    results["dags"] = existing_dags
            
            results['trace'] = pipeline_trace
            return {
                "command_id": command_id,
                "output": output,
                "error": error,
                "success": True,
                "results": results
            }
        except Exception as e:
            return {
                "command_id": command_id,
                "error": str(e),
                "success": False
            }
    
    def _execute_generate_prompts(self, command_id, schema_path, text_path, **kwargs):
        """Execute prompt generation command"""
        try:
            # Clean up files before running
            cleanup_files()
            
            import io
            from contextlib import redirect_stdout, redirect_stderr
            
            output_buffer = io.StringIO()
            error_buffer = io.StringIO()
            
            with redirect_stdout(output_buffer), redirect_stderr(error_buffer):
                ground_entities_value = kwargs.get('ground_entities')
                run_extraction_pipeline(
                    schema_path=schema_path,
                    text_path=text_path,
                    with_dependencies=kwargs.get('add_dependencies', False),
                    add_guidelines=kwargs.get('add_guidelines', False),
                    selected_classes=kwargs.get('classes'),
                    show_prompts=True,
                    show_results=False,
                    generate_prompts_only=True,
                    json_schema=kwargs.get('json_schema', False),
                    ground_entities=ground_entities_value if ground_entities_value is not None else False
                )
            
            output = output_buffer.getvalue()
            error = error_buffer.getvalue()
            
            # Read generated files based on user selections
            results = {}
            add_dependencies = kwargs.get('add_dependencies', False)
            json_schema = kwargs.get('json_schema', False)
            
            # Always show prompts for generate_prompts command - show all generated files
            prompt_files = [
                "generated/prompts/final_namedentity_prompts.json",
                "generated/prompts/inherited_class_prompts.json",
                "generated/prompts/relationship_classes_prompts.json"
            ]
            
            prompts = []
            for file_path in prompt_files:
                if os.path.exists(file_path):
                    try:
                        with open(file_path, "r") as f:
                            content = f.read().strip()
                            if content:  # Only load if file has content
                                # Parse JSON while preserving order using OrderedDict
                                import collections
                                parsed_content = json.loads(content, object_pairs_hook=collections.OrderedDict)
                                
                                # Ensure schema_prompt comes before attribute_prompt within each class
                                ordered_content = collections.OrderedDict()
                                for class_name, class_data in parsed_content.items():
                                    ordered_class = collections.OrderedDict()
                                    if 'schema_prompt' in class_data:
                                        ordered_class['schema_prompt'] = class_data['schema_prompt']
                                    if 'attribute_prompt' in class_data:
                                        ordered_class['attribute_prompt'] = class_data['attribute_prompt']
                                    # Add any other keys that might exist
                                    for other_key in class_data:
                                        if other_key not in ['schema_prompt', 'attribute_prompt']:
                                            ordered_class[other_key] = class_data[other_key]
                                    ordered_content[class_name] = ordered_class
                                
                                prompts.append({
                                    "filename": os.path.basename(file_path),
                                    "filepath": file_path,
                                    "content": ordered_content
                                })
                    except (json.JSONDecodeError, ValueError) as e:
                        print(f"Warning: Could not parse JSON file {file_path}: {e}")
                        continue
            
            if prompts:
                results["prompts"] = prompts
            
            # Show JSON schema if requested
            if json_schema:
                response_format_files = [
                    "generated/response_formats/named_entity_response_formats.json",
                    "generated/response_formats/inherited_response_formats.json",
                    "generated/response_formats/relationship_response_formats.json"
                ]
                
                response_formats = {}
                for file_path in response_format_files:
                    if os.path.exists(file_path):
                        try:
                            with open(file_path, "r") as f:
                                content = f.read().strip()
                                if content:  # Only load if file has content
                                    response_formats[os.path.basename(file_path)] = json.loads(content)
                        except (json.JSONDecodeError, ValueError) as e:
                            print(f"Warning: Could not parse JSON file {file_path}: {e}")
                            continue
                
                if response_formats:
                    results["response_formats"] = response_formats
            
            # Show DAG if dependencies are enabled
            if add_dependencies:
                dag_files = [
                    ("generated/graphs/class_dependency_graph.png", "DAG1: Initial dependency structure - Raw dependency graph from schema.yaml"),
                    ("generated/graphs/class_dependency_graph2.png", "DAG2: Dependency graph after removing classes with no instances"),
                    ("generated/graphs/class_dependency_graph3.png", "DAG3: Final dependency structure after all named entity and inherited class processing")
                ]
                
                existing_dags = []
                for dag_file, description in dag_files:
                    if os.path.exists(dag_file):
                        existing_dags.append({
                            "filename": os.path.basename(dag_file),
                            "description": description
                        })
                
                if existing_dags:
                    results["dags"] = existing_dags
            
            return {
                "command_id": command_id,
                "output": output,
                "error": error,
                "success": True,
                "results": results
            }
        except Exception as e:
            return {
                "command_id": command_id,
                "error": str(e),
                "success": False
            }
    
    def _execute_api_key_set(self, command_id, key):
        """Set API key"""
        try:
            success = self.api_manager.set_api_key(key)
            return {
                "command_id": command_id,
                "output": "API key set successfully!" if success else "Failed to set API key",
                "success": success
            }
        except Exception as e:
            return {
                "command_id": command_id,
                "error": str(e),
                "success": False
            }
    
    def _execute_api_key_check(self, command_id):
        """Check API key status"""
        try:
            result = self.api_manager.check_api_key()
            return {
                "command_id": command_id,
                "output": "API key is valid" if result else "API key is invalid or not set",
                "success": result
            }
        except Exception as e:
            return {
                "command_id": command_id,
                "error": str(e),
                "success": False
            }
    
    def _execute_api_key_remove(self, command_id):
        """Remove API key"""
        try:
            success = self.api_manager.remove_api_key()
            return {
                "command_id": command_id,
                "output": "API key removed successfully!" if success else "No API key was stored",
                "success": success
            }
        except Exception as e:
            return {
                "command_id": command_id,
                "error": str(e),
                "success": False
            }
    
    def _execute_model_set(self, command_id, model_name):
        """Set GPT model"""
        try:
            success = self.api_manager.set_gpt_model(model_name)
            return {
                "command_id": command_id,
                "output": f"Model set to {model_name}" if success else f"Failed to set model {model_name}",
                "success": success
            }
        except Exception as e:
            return {
                "command_id": command_id,
                "error": str(e),
                "success": False
            }
    
    def _execute_models_list(self, command_id):
        """List available models"""
        try:
            result = self.api_manager.list_available_models()
            return {
                "command_id": command_id,
                "output": result,
                "success": True
            }
        except Exception as e:
            return {
                "command_id": command_id,
                "error": str(e),
                "success": False
            }

# Initialize console
console = WebConsole()

@app.route('/')
@app.route('/engine')
@app.route('/engine/')
def index():
    """Main console interface"""
    return render_template('console.html')

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'message': 'Schemalink server is running',
        'timestamp': time.time()
    })

@app.route('/engine/api/execute', methods=['POST'])
def execute():
    """Execute commands"""
    try:
        import traceback
        # Handle both multipart/form-data and application/json
        if request.content_type and 'multipart/form-data' in request.content_type:
            # File upload request
            schema_file = request.files.get('schema_file')
            text_file = request.files.get('text_file')
            command_type = request.form.get('command_type')
            add_dependencies = request.form.get('add_dependencies') == 'true'
            add_guidelines = request.form.get('add_guidelines') == 'true'
            ground_mode = request.form.get('ground_mode')
            ground_threshold = request.form.get('ground_threshold')
            classes = request.form.get('classes')
            
            # Parse grounding configuration - create dict with mode and threshold
            ground_entities = None
            if ground_mode and ground_mode != 'none':
                if ground_mode == 'exact':
                    ground_entities = {'threshold': 1.0, 'mode': 'exact'}
                elif ground_mode == 'partial':
                    ground_entities = {'threshold': 1.0, 'mode': 'partial'}
                elif ground_mode == 'monarch':
                    ground_entities = {'threshold': 1.0, 'mode': 'monarch'}
                elif ground_mode == 'fuzzy':
                    # Parse threshold if provided
                    if ground_threshold:
                        try:
                            threshold_value = float(ground_threshold)
                            if threshold_value < 0 or threshold_value > 10:
                                threshold_value = 7.0  # Default to 70%
                            threshold = threshold_value / 10.0
                        except ValueError:
                            threshold = 0.7  # Default to 70%
                    else:
                        threshold = 0.7  # Default to 70% if not specified
                    ground_entities = {'threshold': threshold, 'mode': 'fuzzy'}
            # Convert empty string to None to avoid filtering when no classes are specified
            if classes and classes.strip():
                classes = [cls.strip() for cls in classes.split(',')]
            else:
                classes = None
            show_prompts = request.form.get('show_prompts') == 'true'
            show_results = request.form.get('show_results') == 'true'
            json_schema = request.form.get('json_schema') == 'true'
            
            if not schema_file or not text_file:
                return jsonify({'error': 'Both schema and text files are required'}), 400
            
            # Save uploaded files temporarily
            schema_path = os.path.join(tempfile.gettempdir(), secure_filename(schema_file.filename))
            text_path = os.path.join(tempfile.gettempdir(), secure_filename(text_file.filename))
            
            schema_file.save(schema_path)
            text_file.save(text_path)
            
            if command_type == 'extract':
                result = console.execute_command(
                    command_type,
                    schema_path=schema_path,
                    text_path=text_path,
                    add_dependencies=add_dependencies,
                    add_guidelines=add_guidelines,
                    ground_entities=ground_entities,
                    classes=classes,
                    show_prompts=show_prompts,
                    show_results=show_results,
                    json_schema=json_schema
                )
            elif command_type == 'generate_prompts':
                result = console.execute_command(
                    command_type,
                    schema_path=schema_path,
                    text_path=text_path,
                    add_dependencies=add_dependencies,
                    add_guidelines=add_guidelines,
                    ground_entities=ground_entities,
                    classes=classes,
                    json_schema=json_schema
                )
            else:
                return jsonify({'error': 'Invalid command type for file upload'}), 400
            
            # Clean up temporary files
            try:
                os.remove(schema_path)
                os.remove(text_path)
            except:
                pass
            
            return jsonify(result)
            
        else:
            # JSON request
            data = request.get_json()
            command_type = data.get('command_type')
            
            if command_type == 'api_key_set':
                result = console.execute_command(command_type, key=data.get('key'))
            elif command_type == 'model_set':
                result = console.execute_command(command_type, model_name=data.get('model_name'))
            else:
                result = console.execute_command(command_type)
            
            return jsonify(result)
            
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error in execute endpoint: {error_trace}")
        return jsonify({
            'error': str(e),
            'success': False,
            'details': error_trace if app.debug else None
        }), 500

@app.route('/engine/api/files')
def list_files():
    """List available files from prompts/, response_formats/, and output/ directories"""
    try:
        files = []
        
        # Define the directories to scan
        directories = [
            ('generated/prompts', 'Prompts'),
            ('generated/response_formats', 'Response Formats'),
            ('output', 'Output')
        ]
        
        for dir_path, dir_label in directories:
            if os.path.exists(dir_path):
                for item in os.listdir(dir_path):
                    item_path = os.path.join(dir_path, item)
                    if os.path.isfile(item_path) and item.endswith(('.json', '.txt', '.yaml', '.yml')):
                        files.append({
                            'name': f"[{dir_label}] {item}",
                            'path': item_path,  # Full path for download functionality
                            'size': os.path.getsize(item_path),
                            'type': 'file',
                            'directory': dir_label,
                            'filename': item
                        })
        
        return jsonify({'files': files})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/engine/api/download/<path:filename>')
def download_file(filename):
    """Download a file"""
    try:
        return send_file(filename, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/engine/api/dag/<filename>')
def serve_dag(filename):
    """Serve DAG images with proper download headers"""
    try:
        dag_path = os.path.join('generated/graphs', filename)
        if os.path.exists(dag_path):
            return send_file(
                dag_path,
                as_attachment=True,
                download_name=filename,
                mimetype='image/png'
            )
        else:
            return jsonify({'error': 'DAG file not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/engine/api/check-api-key', methods=['GET'])
def check_api_key():
    """Check API key status"""
    try:
        api_key = console.api_manager.get_api_key()
        current_model = console.api_manager.get_gpt_model()
        available_models = console.api_manager.available_models
        
        if not api_key:
            return jsonify({
                'status': 'not_set',
                'message': 'No API key found',
                'current_model': current_model,
                'available_models': available_models
            })
        
        if not api_key.startswith('sk-'):
            return jsonify({
                'status': 'invalid',
                'message': 'Invalid API key format',
                'current_model': current_model,
                'available_models': available_models
            })
        
        return jsonify({
            'status': 'valid',
            'message': 'API key is set and appears valid',
            'current_model': current_model,
            'available_models': available_models
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/engine/api/set-api-key', methods=['POST'])
def set_api_key():
    """Set API key"""
    try:
        data = request.get_json()
        api_key = data.get('api_key', '').strip()
        
        print(f"DEBUG: Received API key: {api_key[:10]}...")  # Debug log
        
        if not api_key:
            return jsonify({'error': 'API key is required'}), 400
        
        success = console.api_manager.set_api_key(api_key)
        print(f"DEBUG: API key validation result: {success}")  # Debug log
        
        if success:
            return jsonify({'message': 'API key set successfully'})
        else:
            return jsonify({'error': 'Invalid API key format. OpenAI API keys should start with "sk-"'}), 400
    except Exception as e:
        print(f"DEBUG: Exception in set_api_key: {e}")  # Debug log
        return jsonify({'error': str(e)}), 500

@app.route('/engine/api/remove-api-key', methods=['POST'])
def remove_api_key():
    """Remove API key"""
    try:
        success = console.api_manager.remove_api_key()
        if success:
            return jsonify({'message': 'API key removed successfully'})
        else:
            return jsonify({'message': 'No API key was stored'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/engine/api/set-model', methods=['POST'])
def set_model():
    """Set GPT model"""
    try:
        data = request.get_json()
        model_name = data.get('model_name', '').strip()
        
        if not model_name:
            return jsonify({'error': 'Model name is required'}), 400
        
        success = console.api_manager.set_gpt_model(model_name)
        if success:
            return jsonify({'message': f'GPT model set to: {model_name}'})
        else:
            return jsonify({'error': f'Invalid model: {model_name}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/engine/api/list-models', methods=['GET'])
def list_models():
    """List available models"""
    try:
        current_model = console.api_manager.get_gpt_model()
        available_models = console.api_manager.available_models
        
        return jsonify({
            'current_model': current_model,
            'available_models': available_models
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/engine/api/v1/extract', methods=['POST'])
def v1_extract():
    """Run schema-guided extraction on a text.
    ---
    tags:
      - Extraction
    consumes:
      - application/json
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - schema
            - text
          properties:
            schema:
              type: string
              description: LinkML schema in YAML format.
              example: "id: https://example.org/ddi\\nname: ddi\\nclasses:\\n  Drug:\\n    is_a: NamedEntity\\n"
            text:
              type: string
              description: Input text to extract information from.
              example: "Doxycycline 100mg was given to patients with Lyme disease."
            add_dependencies:
              type: boolean
              description: Run multi-step dependency-aware extraction pipeline.
              default: true
            ground_mode:
              type: string
              enum: [none, exact, partial, fuzzy, monarch]
              description: Grounding mode for entity normalization.
              default: none
            ground_threshold:
              type: number
              description: Fuzzy match threshold (0–10 scale, used only when ground_mode is fuzzy).
              default: 7
    responses:
      200:
        description: Extraction result with entities and relations.
        schema:
          type: object
          properties:
            success:
              type: boolean
            responses:
              type: object
              description: Per-class extraction results keyed by class name.
            output:
              type: string
              description: Pipeline stdout log (dependency trace).
      400:
        description: Missing or invalid request parameters.
      500:
        description: Internal server error during extraction.
    """
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400

        schema_yaml = data.get('schema', '').strip()
        input_text = data.get('text', '').strip()

        if not schema_yaml:
            return jsonify({'success': False, 'error': '"schema" field is required'}), 400
        if not input_text:
            return jsonify({'success': False, 'error': '"text" field is required'}), 400

        add_dependencies = bool(data.get('add_dependencies', True))
        ground_mode = data.get('ground_mode', 'none')
        ground_threshold = data.get('ground_threshold', 7)
        model_name = data.get('model', 'gpt-4o').strip()
        allowed_models = {'gpt-4o', 'gpt-4o-mini'}
        if model_name not in allowed_models:
            model_name = 'gpt-4o'
        console.api_manager.set_gpt_model(model_name)

        ground_entities = None
        if ground_mode and ground_mode != 'none':
            if ground_mode in ('exact', 'partial', 'monarch'):
                ground_entities = {'threshold': 1.0, 'mode': ground_mode}
            elif ground_mode == 'fuzzy':
                try:
                    threshold_value = float(ground_threshold)
                    if not (0 <= threshold_value <= 10):
                        threshold_value = 7.0
                    ground_entities = {'threshold': threshold_value / 10.0, 'mode': 'fuzzy'}
                except (ValueError, TypeError):
                    ground_entities = {'threshold': 0.7, 'mode': 'fuzzy'}

        # Ensure all required OAK SQLite DBs are downloaded before extraction
        for msg in _ensure_oak_dbs(schema_yaml):
            print(msg, flush=True)

        # Write schema and text to temp files
        schema_fd, schema_path = tempfile.mkstemp(suffix='.yaml', prefix='schemalink_schema_')
        text_fd, text_path = tempfile.mkstemp(suffix='.txt', prefix='schemalink_text_')
        try:
            with os.fdopen(schema_fd, 'w') as f:
                f.write(schema_yaml)
            with os.fdopen(text_fd, 'w') as f:
                f.write(input_text)

            result = console.execute_command(
                'extract',
                schema_path=schema_path,
                text_path=text_path,
                add_dependencies=add_dependencies,
                add_guidelines=False,
                ground_entities=ground_entities,
                classes=None,
                show_prompts=False,
                show_results=True,
                json_schema=False,
            )
        finally:
            try:
                os.remove(schema_path)
            except OSError:
                pass
            try:
                os.remove(text_path)
            except OSError:
                pass

        if not result.get('success', False):
            return jsonify({
                'success': False,
                'error': result.get('error', 'Extraction failed'),
            }), 500

        return jsonify({
            'success': True,
            'responses': result.get('results', {}).get('responses', {}),
            'output': result.get('output', ''),
            'trace': result.get('results', {}).get('trace', {}),
        })

    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'details': traceback.format_exc(),
        }), 500


@app.route('/engine/api/v2/extract', methods=['POST', 'OPTIONS'])
def v2_extract_stream():
    """Stream schema-guided extraction results class-by-class via Server-Sent Events.

    Each request runs in a fully isolated temporary working directory so multiple
    users can extract concurrently without any file collisions.
    ---
    tags:
      - Extraction
    consumes:
      - application/json
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - schema
            - text
          properties:
            schema:
              type: string
            text:
              type: string
            add_dependencies:
              type: boolean
              default: true
            add_guidelines:
              type: boolean
              default: true
            ground_mode:
              type: string
              default: exact
            model:
              type: string
              enum: [gpt-4o, gpt-4o-mini]
              default: gpt-4o-mini
    responses:
      200:
        description: Server-Sent Events stream. Each event is a JSON object.
    """
    if request.method == 'OPTIONS':
        return '', 200

    data = request.get_json(force=True) or {}
    schema_yaml    = data.get('schema', '').strip()
    input_text     = data.get('text', '').strip()
    add_deps       = bool(data.get('add_dependencies', True))
    add_guidelines = bool(data.get('add_guidelines', True))
    ground_mode    = data.get('ground_mode', 'exact')
    model_name     = data.get('model', 'gpt-4o-mini').strip()

    if model_name not in {'gpt-4o', 'gpt-4o-mini'}:
        model_name = 'gpt-4o-mini'

    if not schema_yaml:
        return jsonify({'error': '"schema" field is required'}), 400
    if not input_text:
        return jsonify({'error': '"text" field is required'}), 400

    # Absolute path to the worker script (same directory as this file)
    _worker_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'extraction_worker.py')

    def generate():
        import shutil

        # ── 0. Ensure all required OAK SQLite DBs are present ────────────
        for msg in _ensure_oak_dbs(schema_yaml):
            print(msg, flush=True)
            yield f'data: {json.dumps({"type": "log", "message": msg})}\n\n'

        # ── 1. Create an isolated working directory ──────────────────────
        workdir = tempfile.mkdtemp(prefix='schemalink_v2_')
        try:
            for subdir in ['generated/graphs', 'generated/prompts',
                           'generated/response_formats', 'output']:
                os.makedirs(os.path.join(workdir, subdir), exist_ok=True)

            # Write inputs into the workdir (relative paths used by pipeline)
            schema_path = os.path.join(workdir, 'schema.yaml')
            text_path   = os.path.join(workdir, 'text.txt')
            with open(schema_path, 'w', encoding='utf-8') as f:
                f.write(schema_yaml)
            with open(text_path, 'w', encoding='utf-8') as f:
                f.write(input_text)

            # ── 2. Build subprocess command ───────────────────────────────
            # -u forces Python to run in unbuffered mode so each TRACE line
            # is flushed to the pipe immediately instead of being held in
            # Python's 8 KB stdout buffer until the process ends.
            cmd = [sys.executable, '-u', _worker_script,
                   'schema.yaml', 'text.txt',
                   '--model', model_name]
            if add_deps:
                cmd.append('--add-dependencies')
            if add_guidelines:
                cmd.append('--add-guidelines')
            if ground_mode and ground_mode != 'none':
                cmd.extend(['--ground-mode', ground_mode])

            # ── 3. Launch subprocess with cwd=workdir ─────────────────────
            proc = subprocess.Popen(
                cmd,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # ── 4. Stream stdout line-by-line, forward TRACE events as SSE ─
            #
            # We use a reader thread + queue so the generator can send
            # periodic SSE heartbeat comments (": keepalive") every 500 ms
            # while waiting for the next line.  These comments force nginx
            # (and any other buffering proxy) to flush its buffer immediately,
            # making the TRACE events reach the browser in real-time.
            pipeline_trace = {}
            _ACCUMULATE = {
                'NE_INIT', 'NE_FILTERED', 'NE_FILTER_REMOVED',
                'NE_GROUNDED', 'NE_GROUNDING_REMOVED',
                'RE_INIT', 'RE_FINAL', 'RE_FILTERED_REMOVED',
            }
            _SCALAR = {'NE_TIMING', 'RE_TIMING', 'NE_DONE', 'RE_DONE', 'META'}
            other_lines = []

            line_q = queue.Queue()

            def _reader():
                try:
                    while True:
                        raw = proc.stdout.readline()
                        if not raw:   # EOF
                            break
                        line_q.put(raw.rstrip('\n'))
                finally:
                    line_q.put(None)  # sentinel

            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()

            while True:
                try:
                    line = line_q.get(timeout=0.5)
                except queue.Empty:
                    # No output yet — send a keepalive comment to flush proxies
                    yield ": keepalive\n\n"
                    continue

                if line is None:   # subprocess finished
                    break

                if not line.startswith('TRACE:'):
                    other_lines.append(line)
                    continue

                try:
                    parts = line.split(':', 3)
                    if len(parts) != 4:
                        continue
                    _, event_type, class_name, data_json = parts
                    payload = json.loads(data_json)
                    if class_name not in pipeline_trace:
                        pipeline_trace[class_name] = {}
                    if event_type in _ACCUMULATE and isinstance(payload, list):
                        existing = pipeline_trace[class_name].get(event_type, [])
                        pipeline_trace[class_name][event_type] = existing + payload
                    else:
                        pipeline_trace[class_name][event_type] = payload
                    yield "data: " + json.dumps({
                        "type": "trace",
                        "event": event_type,
                        "class": class_name,
                        "data": payload,
                    }) + "\n\n"
                except Exception:
                    pass

            reader_thread.join(timeout=5)
            proc.wait()

            if proc.returncode != 0:
                # Return last 30 lines of worker output as error detail
                tail = '\n'.join(other_lines[-30:])
                yield "data: " + json.dumps({
                    "type": "error",
                    "message": f"Extraction worker exited with code {proc.returncode}",
                    "detail": tail,
                }) + "\n\n"
                return

            # ── 5. Read final output and send done event ──────────────────
            resp_filename = (
                'generated_responses.json' if add_deps
                else 'generated_responses_without_dependencies.json'
            )
            response_file = os.path.join(workdir, 'output', resp_filename)
            responses = {}
            if os.path.exists(response_file):
                try:
                    with open(response_file, encoding='utf-8') as rf:
                        responses = json.load(rf)
                except Exception:
                    pass

            yield "data: " + json.dumps({
                "type": "done",
                "responses": responses,
                "trace": pipeline_trace,
            }) + "\n\n"

        finally:
            # ── 6. Clean up the isolated workdir ─────────────────────────
            shutil.rmtree(workdir, ignore_errors=True)

    return app.response_class(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


if __name__ == '__main__':
    # Create necessary directories
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    os.makedirs('generated/graphs', exist_ok=True)
    os.makedirs('generated/prompts', exist_ok=True)
    os.makedirs('generated/response_formats', exist_ok=True)
    os.makedirs('output', exist_ok=True)
    
    print("🚀 Starting Schemalink Production Server...")
    print("🌐 Server will be available at: https://schemalink.anacleto.di.unimi.it/engine")
    print("🛑 Press Ctrl+C to stop the server")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    # Run on port 15002 for production
    app.run(debug=False, host='0.0.0.0', port=15002)
