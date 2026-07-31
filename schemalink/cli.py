import argparse
import os
from schemalink.pipeline import run_extraction_pipeline

def cleanup_files():
    """Clean up files before running commands"""
    
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

def main():
    parser = argparse.ArgumentParser(
        description="🧠 Schemalink: A CLI tool for extracting structured entities and relations from biomedical text using a schema.",
        epilog="""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📘 Example Usage:

1. Extract all classes from a schema and a text file:
   $ schemalink extract schema.yaml sample.txt

2. Extract only specific classes (e.g. Disease, Drug):
   $ schemalink extract schema.yaml sample.txt --classes Disease Drug

3. Enable class dependency analysis and extraction:
   $ schemalink extract schema.yaml sample.txt --add_dependencies

4. View the generated GPT prompts:
   $ schemalink extract schema.yaml sample.txt --show_prompts

5. View the generated Results:
   $ schemalink extract schema.yaml sample.txt --show_results

6. Manage OpenAI API key:
   $ schemalink api-key set sk-your-api-key-here
   $ schemalink api-key check
   $ schemalink api-key remove

7. Manage GPT model:
   $ schemalink models
   $ schemalink model gpt-4o-2024-08-06

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 API Key Management:

  api-key set <key>     Set your OpenAI API key (starts with 'sk-')
  api-key check         Check if API key is set and valid
  api-key remove        Remove stored API key

  Note: You can also set OPENAI_API_KEY environment variable
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 Model Management:

  models                List available GPT models
  model <model_name>    Set the GPT model to use

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 Explanation of Arguments:

REQUIRED:
  schema.yaml          Path to your input LinkML schema file
  sample.txt           Path to your input biomedical text file

OPTIONAL FLAGS:
  --classes            List of specific class names you want to extract. If omitted, all schema classes will be used.
                       $ schemalink extract schema.yaml sample.txt --classes Disease Drug

  --add_dependencies   Enables advanced logic for inherited classes and inter-class relationships. If omitted, the engine would run without considering dependencies.
                       $ schemalink extract schema.yaml sample.txt --add_dependencies

  --show_prompts       Displays the GPT prompts used for extraction (for debugging or educational use).
                       $ schemalink extract schema.yaml sample.txt --show_prompts
  
  --show_results       Displays Output
                       $ schemalink extract schema.yaml sample.txt --show_results


  --NER                (Reserved) Only run Named Entity Recognition.
  --ER                 (Reserved) Only run Relationship Extraction.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        """,
        formatter_class=argparse.RawTextHelpFormatter
    )

    subparsers = parser.add_subparsers(dest="command")

    # Define extract_parser here so we can access it later
    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract entities and relationships from a text file using the provided schema.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    generate_parser = subparsers.add_parser(
    "generate_prompts",
    help="Generate prompt templates (with placeholders) without calling GPT.",
    formatter_class=argparse.RawTextHelpFormatter
    )

    # API Key management parser
    api_parser = subparsers.add_parser(
        "api-key",
        help="Manage OpenAI API key for Schemalink engine.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    api_subparsers = api_parser.add_subparsers(dest="api_action")
    
    # Set API key command
    set_parser = api_subparsers.add_parser(
        "set",
        help="Set your OpenAI API key."
    )
    set_parser.add_argument(
        "key",
        help="Your OpenAI API key (starts with 'sk-')"
    )
    
    # Check API key command
    check_parser = api_subparsers.add_parser(
        "check",
        help="Check if API key is set and valid."
    )
    
    # Remove API key command
    remove_parser = api_subparsers.add_parser(
        "remove",
        help="Remove stored API key."
    )

    # Model management parser
    model_parser = subparsers.add_parser(
        "model",
        help="Set the GPT model to use for extraction.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    model_parser.add_argument(
        "model_name",
        help="Name of the GPT model to use (e.g., gpt-4o-2024-08-06)"
    )

    # Models list parser
    models_parser = subparsers.add_parser(
        "models",
        help="List available GPT models.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    extract_parser.add_argument(
        "schema_path",
        help="Path to the input schema YAML file."
    )

    extract_parser.add_argument(
        "text_path",
        help="Path to the input text file to extract entities/relations from."
    )

    extract_parser.add_argument(
        "--classes", nargs="*", metavar="CLASS",
        help="Optional list of classes to extract. If not provided, all classes in the schema will be used."
    )

    extract_parser.add_argument(
        "--add_dependencies",
        action="store_true",
        help="Enable dependency-based extraction. Resolves class hierarchies and relationships."
    )

    extract_parser.add_argument(
        "--add_guidelines",
        action="store_true",
        help="Include schema-level guidelines (entity_guidelines and relation_guidelines) in prompts."
    )

    extract_parser.add_argument(
        "--show_prompts",
        action="store_true",
        help="Print the generated prompts used for GPT entity/relation extraction."
    )

    extract_parser.add_argument(
        "--NER",
        action="store_true",
        help="(Not yet implemented) Only run Named Entity Recognition."
    )

    extract_parser.add_argument(
        "--ER",
        action="store_true",
        help="(Not yet implemented) Only run Relation Extraction."
    )
    extract_parser.add_argument(
    "--show_results", action="store_true",
    help="Print the final extracted entities and relationships from the output file."
    )
    
    extract_parser.add_argument(
        "--ground",
        nargs='?',
        const='exact',
        default=None,
        help="Enable entity grounding. Use '--ground' for exact matching, '--ground partial' for word-based partial matching, '--ground m' or '--ground monarch' for Monarch KG grounding, or '--ground THRESHOLD' for fuzzy matching (e.g., '--ground 7' for 70%% similarity). Entities not found will be removed."
    )
    
    generate_parser.add_argument(
    "schema_path",
    help="Path to the input schema YAML file."
    )

    generate_parser.add_argument(
        "text_path",
        help="Path to the input text file to extract prompts for."
    )

    generate_parser.add_argument(
        "--classes", nargs="*", metavar="CLASS",
        help="Optional list of classes to generate prompts for. If not provided, all classes will be used."
    )

    generate_parser.add_argument(
        "--add_dependencies",
        action="store_true",
        help="Enable dependency-based prompt generation logic."
    )

    generate_parser.add_argument(
        "--add_guidelines",
        action="store_true",
        help="Include schema-level guidelines (entity_guidelines and relation_guidelines) in prompts."
    )
    
    generate_parser.add_argument(
        "--ground",
        nargs='?',
        const='exact',
        default=None,
        help="Enable entity grounding using lookup tables. Use '--ground' for exact matching or '--ground THRESHOLD' for fuzzy matching (e.g., '--ground 7' for 70%% similarity). Entities not found will be removed."
    )

    extract_parser.add_argument(
    "--json_schema",
    action="store_true",
    help="Print the JSON-converted schema and exit."
    )

    generate_parser.add_argument(
        "--json_schema",
        action="store_true",
        help="Print the JSON-converted schema and exit."
    )



    args = parser.parse_args()

    # ✅ Show full extract help if no command is provided
    if args.command is None:
        parser.print_help()
        print("\nTip: Run `schemalink extract --help` for detailed options.\n")
        return

    if args.command == "extract":
        cleanup_files()
        
        # Parse ground argument
        ground_config = None
        if args.ground:
            if args.ground == 'exact':
                ground_config = {'threshold': 1.0, 'mode': 'exact'}
            elif args.ground == 'partial':
                ground_config = {'threshold': 1.0, 'mode': 'partial'}
            elif args.ground.lower() in ['m', 'monarch']:
                ground_config = {'threshold': 1.0, 'mode': 'monarch'}
            else:
                try:
                    # Convert threshold (e.g., 7 -> 0.7 for 70%)
                    threshold_value = float(args.ground)
                    if threshold_value < 0 or threshold_value > 10:
                        print("⚠️ Warning: Threshold must be between 0 and 10. Using exact matching.")
                        ground_config = {'threshold': 1.0, 'mode': 'exact'}
                    else:
                        ground_config = {'threshold': threshold_value / 10.0, 'mode': 'fuzzy'}
                except ValueError:
                    print(f"⚠️ Warning: Invalid threshold '{args.ground}'. Using exact matching.")
                    ground_config = {'threshold': 1.0, 'mode': 'exact'}
        
        run_extraction_pipeline(
            schema_path=args.schema_path,
            text_path=args.text_path,
            with_dependencies=args.add_dependencies,
            add_guidelines=args.add_guidelines,
            selected_classes=args.classes,
            show_prompts=args.show_prompts,
            show_results=args.show_results,
            generate_prompts_only=False,
            json_schema=args.json_schema,
            ground_entities=ground_config
        )
    elif args.command == "generate_prompts":
        cleanup_files()
        
        # Parse ground argument
        ground_threshold = None
        if args.ground:
            if args.ground == 'exact':
                ground_threshold = 1.0  # Exact matching
            else:
                try:
                    # Convert threshold (e.g., 7 -> 0.7 for 70%)
                    threshold_value = float(args.ground)
                    if threshold_value < 0 or threshold_value > 10:
                        print("⚠️ Warning: Threshold must be between 0 and 10. Using exact matching.")
                        ground_threshold = 1.0
                    else:
                        ground_threshold = threshold_value / 10.0
                except ValueError:
                    print(f"⚠️ Warning: Invalid threshold '{args.ground}'. Using exact matching.")
                    ground_threshold = 1.0
        
        run_extraction_pipeline(
        schema_path=args.schema_path,
        text_path=args.text_path,
        with_dependencies=args.add_dependencies,
        add_guidelines=args.add_guidelines,
        selected_classes=args.classes,
        show_prompts=True,
        show_results=False,
        generate_prompts_only=True,
        json_schema=args.json_schema,
        ground_entities=ground_threshold
      )
    elif args.command == "api-key":
        from schemalink.api_key_manager import APIKeyManager
        api_manager = APIKeyManager()
        
        if args.api_action == "set":
            api_manager.set_api_key(args.key)
        elif args.api_action == "check":
            api_manager.check_api_key()
        elif args.api_action == "remove":
            api_manager.remove_api_key()
        else:
            api_parser.print_help()
    elif args.command == "model":
        from schemalink.api_key_manager import APIKeyManager
        api_manager = APIKeyManager()
        api_manager.set_gpt_model(args.model_name)
    elif args.command == "models":
        from schemalink.api_key_manager import APIKeyManager
        api_manager = APIKeyManager()
        api_manager.list_available_models()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()