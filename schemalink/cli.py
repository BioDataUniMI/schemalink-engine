import argparse
import os
import sys
import time
from schemalink.pipeline import run_extraction_pipeline


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
        from schemalink.api_key_manager import APIKeyManager
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
        "--show_results", action="store_true",
        help="Print extraction output to stdout after the run.",
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
            from schemalink.api_key_manager import APIKeyManager
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

        if args.command == "extract":
            from schemalink.api_key_manager import APIKeyManager
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

        start = time.time()
        try:
            run_extraction_pipeline(
                schema_path=args.schema_path,
                text_path=args.text_path,
                with_dependencies=with_dependencies,
                add_guidelines=args.add_guidelines,
                selected_classes=args.classes,
                show_prompts=args.show_prompts,
                show_results=getattr(args, "show_results", False),
                generate_prompts_only=generate_only,
                json_schema=args.json_schema,
                ground_entities=ground_config,
            )
            elapsed = time.time() - start
            if args.command == "extract":
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
        from schemalink.api_key_manager import APIKeyManager
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
        from schemalink.api_key_manager import APIKeyManager
        APIKeyManager().set_gpt_model(args.model_name)
    elif args.command == "models":
        from schemalink.api_key_manager import APIKeyManager
        APIKeyManager().list_available_models()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
