import argparse
import json
import os
import sys
import time
import warnings
from schemalink_engine.pipeline import run_extraction_pipeline


# Internal pipeline noise hidden from the default CLI (webapp still sees TRACE).
_HIDDEN_PREFIXES = (
    "TRACE:",
    "**Step",
    "Inherited Classes:",
    "⚠️ Warning: Lookup table not found",
    "⚠️  OAK library could not be loaded",
)
_HIDDEN_EXACT = {"False", "True", "{}", "[]"}
_HIDDEN_CONTAINS = (
    "UserWarning:",
    "plt.tight_layout()",
    "Axes that are not compatible with tight_layout",
    "DAG1:",
    "DAG2:",
    "DAG3:",
    "Reordering classes",
    "Reordered schema.json",
    "Reordered class_dependencies.json",
    "Named entity response formats",
    "Inherited response formats",
    "Response formats for inherited classes",
    "Processing inherited entity classes",
    "inherited prompts saved",
    "Inherited entity processing",
    "Rename merges",
    "Relation classes with two dependencies",
    "Generated response formats",
    "Relationship response formats",
    "Final DAG",
    "Second dependency graph",
    "Dependency graph successfully",
    "Class dependencies successfully",
    "Output File:",
    "Prompts saved",
    "Responses saved",
    "NamedEntity classes:",
    "✅ Grounded",
    "✅ Loaded lookup table",
    "ungrounded entities",
)


class _QuietStream:
    """Drop TRACE lines and internal pipeline chatter from a stream."""

    def __init__(self, stream):
        self._stream = stream
        self._buf = ""

    def write(self, text):
        if not isinstance(text, str):
            text = str(text)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if self._keep(line):
                self._stream.write(line + "\n")

    def flush(self):
        if self._buf and self._keep(self._buf):
            self._stream.write(self._buf)
        self._buf = ""
        self._stream.flush()

    def _keep(self, line):
        stripped = line.strip()
        if stripped in _HIDDEN_EXACT:
            return False
        if stripped.startswith(_HIDDEN_PREFIXES):
            return False
        return not any(token in line for token in _HIDDEN_CONTAINS)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _result_output_path(with_dependencies):
    candidates = (
        ["output/generated_responses.json"]
        if with_dependencies
        else ["output/generated_responses_without_dependencies.json"]
    )
    candidates.append("output/generated_responses.json")
    for path in candidates:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    return candidates[0]


def _print_extraction_results(with_dependencies, include_json=True):
    path = _result_output_path(with_dependencies)
    print()
    print(f"  Saved to: {os.path.abspath(path)}")
    if not include_json or not os.path.exists(path) or os.path.getsize(path) == 0:
        print()
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        print()
        return
    print()
    print("  Results")
    print("  -------")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print()


def cleanup_files():
    """Remove stale generated artefacts from a previous run."""
    for graphs_dir in ["generated/graphs"]:
        if os.path.exists(graphs_dir):
            for fname in os.listdir(graphs_dir):
                fp = os.path.join(graphs_dir, fname)
                if os.path.isfile(fp):
                    os.remove(fp)
    for empty_dir in ["generated/prompts", "generated/response_formats", "output"]:
        if os.path.exists(empty_dir):
            for fname in os.listdir(empty_dir):
                fp = os.path.join(empty_dir, fname)
                if os.path.isfile(fp):
                    open(fp, "w").close()


def _check_inputs(schema_path, text_path):
    """Validate input files. Returns an error string or None."""
    if not os.path.exists(schema_path):
        return f"Schema file not found: '{schema_path}'"
    if os.path.getsize(schema_path) == 0:
        return f"Schema file is empty: '{schema_path}'"
    if not os.path.exists(text_path):
        return f"Text file not found: '{text_path}'"
    if os.path.getsize(text_path) == 0:
        return f"Text file is empty: '{text_path}'"
    return None


def _model_list_str():
    """Return a formatted string of available models for help text."""
    try:
        from schemalink_engine.api_key_manager import APIKeyManager
        models = APIKeyManager().available_models
        lines = []
        for name, desc in models.items():
            lines.append(f"    {name:<36} {desc}")
        return "\n".join(lines)
    except Exception:
        return "    (run `schemalink models` to see the list)"


def main():
    parser = argparse.ArgumentParser(
        description="SchemaLink — schema-guided information extraction from biomedical text.",
        epilog=f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Examples

  Basic extraction (dependency-aware by default):
    schemalink extract schema.yaml text.txt

  With ontology grounding:
    schemalink extract schema.yaml text.txt --ground

  Extract only specific classes:
    schemalink extract schema.yaml text.txt --classes Disease Drug

  Use a specific GPT model:
    schemalink extract schema.yaml text.txt --model gpt-4o-mini

  Flat extraction (no dependency analysis):
    schemalink extract schema.yaml text.txt --flat

  Show prompts sent to the LLM (still makes API calls):
    schemalink extract schema.yaml text.txt --show_prompts

  Generate prompts only, without calling the API:
    schemalink generate_prompts schema.yaml text.txt

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Grounding

  Pass --ground to normalise extracted entities to ontology IDs.
  The method is determined automatically from the schema's annotators: field:

    annotators: sqlite:obo:mondo   →  OAK lookup (MONDO ontology)
    annotators: sqlite:obo:chebi   →  OAK lookup (ChEBI ontology)
    annotators: hgnc               →  built-in lookup table
    (no annotator)                 →  entity kept as raw label

  OAK databases are downloaded to ~/.data/oaklib/ on first use.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Supported models (structured output required)

{_model_list_str()}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  API key & model management

    schemalink api-key set <key>   Store your OpenAI API key
    schemalink api-key check       Verify the stored key
    schemalink api-key remove      Delete the stored key
    schemalink model <name>        Switch active model
    schemalink models              List all supported models
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        """,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command")

    # ── extract ──────────────────────────────────────────────────────────────
    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract entities and relations from text using a schema.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    extract_parser.add_argument("schema_path", help="Path to the LinkML schema YAML file.")
    extract_parser.add_argument("text_path",   help="Path to the input text file.")
    extract_parser.add_argument(
        "--classes", nargs="*", metavar="CLASS",
        help="Extract only these classes (default: all classes in the schema).",
    )
    extract_parser.add_argument(
        "--ground", action="store_true",
        help=(
            "Enable entity grounding. The method is chosen automatically\n"
            "from each class's annotators: field in the schema."
        ),
    )
    extract_parser.add_argument(
        "--model", metavar="MODEL",
        help=(
            "GPT model to use for this run (overrides the stored default).\n"
            "Run `schemalink models` to see all supported options."
        ),
    )
    extract_parser.add_argument(
        "--flat", action="store_true",
        help="Disable dependency analysis (not recommended — use only for simple schemas).",
    )
    extract_parser.add_argument(
        "--add_guidelines", action="store_true",
        help="Include schema-level NER/RE guidelines in the LLM prompts.",
    )
    extract_parser.add_argument(
        "--show_prompts", action="store_true",
        help="Print the LLM prompts alongside the extraction output (API is still called).",
    )
    extract_parser.add_argument(
        "--quiet", action="store_true",
        help="Do not print the extraction JSON at the end of the run.",
    )
    extract_parser.add_argument(
        "--verbose", action="store_true",
        help="Show internal TRACE lines and pipeline debug output.",
    )
    extract_parser.add_argument(
        "--show_results", action="store_true", help=argparse.SUPPRESS,
    )
    extract_parser.add_argument(
        "--json_schema", action="store_true",
        help="Print the parsed JSON schema and exit.",
    )
    # Hidden legacy flags kept for backwards compatibility
    extract_parser.add_argument("--add_dependencies", action="store_true", help=argparse.SUPPRESS)
    extract_parser.add_argument("--NER", action="store_true", help=argparse.SUPPRESS)
    extract_parser.add_argument("--ER",  action="store_true", help=argparse.SUPPRESS)

    # ── generate_prompts ─────────────────────────────────────────────────────
    gen_parser = subparsers.add_parser(
        "generate_prompts",
        help="Generate LLM prompt templates without calling the API.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    gen_parser.add_argument("schema_path", help="Path to the LinkML schema YAML file.")
    gen_parser.add_argument("text_path",   help="Path to the input text file.")
    gen_parser.add_argument("--classes", nargs="*", metavar="CLASS")
    gen_parser.add_argument("--flat", action="store_true")
    gen_parser.add_argument("--add_guidelines", action="store_true")
    gen_parser.add_argument("--ground", action="store_true")
    gen_parser.add_argument("--json_schema", action="store_true")
    gen_parser.add_argument("--add_dependencies", action="store_true", help=argparse.SUPPRESS)

    # ── api-key ───────────────────────────────────────────────────────────────
    api_parser = subparsers.add_parser("api-key", help="Manage your OpenAI API key.")
    api_sub = api_parser.add_subparsers(dest="api_action")
    set_p = api_sub.add_parser("set",    help="Store an API key.")
    set_p.add_argument("key", help="Your OpenAI API key (starts with 'sk-').")
    api_sub.add_parser("check",  help="Verify the stored key.")
    api_sub.add_parser("remove", help="Delete the stored key.")

    # ── model(s) ──────────────────────────────────────────────────────────────
    model_parser = subparsers.add_parser(
        "model",
        help="Set the default GPT model.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    model_parser.add_argument(
        "model_name",
        help="Name of the model to use. Run `schemalink models` to see all options.",
    )
    subparsers.add_parser("models", help="List all supported GPT models.")

    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    # ── extract / generate_prompts ────────────────────────────────────────────
    if args.command in ("extract", "generate_prompts"):
        err = _check_inputs(args.schema_path, args.text_path)
        if err:
            print(f"\n  ✗  {err}\n", file=sys.stderr)
            sys.exit(1)

        # Override model for this run if --model was passed
        if args.command == "extract" and getattr(args, "model", None):
            from schemalink_engine.api_key_manager import APIKeyManager
            mgr = APIKeyManager()
            if args.model not in mgr.available_models:
                print(f"\n  ✗  Unknown model: '{args.model}'", file=sys.stderr)
                print("     Supported models:", file=sys.stderr)
                for name, desc in mgr.available_models.items():
                    print(f"       {name:<36} {desc}", file=sys.stderr)
                print(file=sys.stderr)
                sys.exit(1)
            mgr.set_gpt_model(args.model)

        with_dependencies = not args.flat
        ground_config = {"mode": "auto"} if args.ground else None
        generate_only = (args.command == "generate_prompts")

        cleanup_files()

        quiet_cli = args.command == "extract" and not getattr(args, "verbose", False)
        if quiet_cli:
            os.environ["SCHEMALINK_CLI"] = "1"
            warnings.filterwarnings("ignore", message=".*tight_layout.*")
            sys.stdout = _QuietStream(sys.stdout)
            sys.stderr = _QuietStream(sys.stderr)

        if args.command == "extract":
            from schemalink_engine.api_key_manager import APIKeyManager
            from schemalink_engine.utils.cli_progress import reset as reset_cli_progress
            reset_cli_progress()
            current_model = APIKeyManager().get_gpt_model()
            print()
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print("  SchemaLink Extraction")
            print(f"  Schema : {args.schema_path}")
            print(f"  Text   : {args.text_path}")
            print(f"  Model  : {current_model}")
            print(f"  Mode   : {'dependency-aware' if with_dependencies else 'flat'}")
            if ground_config:
                print("  Ground : enabled (schema-driven)")
            if args.classes:
                print(f"  Classes: {', '.join(args.classes)}")
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print()

        # Pre-download any missing OAK databases before starting extraction
        if ground_config:
            from schemalink_engine.utils.grounding import ensure_oak_databases
            ensure_oak_databases(args.schema_path)

        start = time.time()
        try:
            run_extraction_pipeline(
                schema_path=args.schema_path,
                text_path=args.text_path,
                with_dependencies=with_dependencies,
                add_guidelines=args.add_guidelines,
                selected_classes=args.classes,
                show_prompts=args.show_prompts,
                show_results=False,
                generate_prompts_only=generate_only,
                json_schema=args.json_schema,
                ground_entities=ground_config,
            )
            elapsed = time.time() - start
            if args.command == "extract":
                _print_extraction_results(
                    with_dependencies,
                    include_json=not getattr(args, "quiet", False),
                )
                print()
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print(f"  ✓  Extraction completed in {elapsed:.1f}s")
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print()
        except KeyboardInterrupt:
            print("\n\n  ✗  Interrupted.\n", file=sys.stderr)
            sys.exit(130)
        except Exception as exc:
            elapsed = time.time() - start
            print(file=sys.stderr)
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", file=sys.stderr)
            print(f"  ✗  Extraction failed after {elapsed:.1f}s", file=sys.stderr)
            print(f"  Error: {exc}", file=sys.stderr)
            print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", file=sys.stderr)
            print(file=sys.stderr)
            sys.exit(1)

    # ── api-key ───────────────────────────────────────────────────────────────
    elif args.command == "api-key":
        from schemalink_engine.api_key_manager import APIKeyManager
        mgr = APIKeyManager()
        if args.api_action == "set":
            mgr.set_api_key(args.key)
        elif args.api_action == "check":
            mgr.check_api_key()
        elif args.api_action == "remove":
            mgr.remove_api_key()
        else:
            api_parser.print_help()

    # ── model / models ────────────────────────────────────────────────────────
    elif args.command == "model":
        from schemalink_engine.api_key_manager import APIKeyManager
        APIKeyManager().set_gpt_model(args.model_name)
    elif args.command == "models":
        from schemalink_engine.api_key_manager import APIKeyManager
        APIKeyManager().list_available_models()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
