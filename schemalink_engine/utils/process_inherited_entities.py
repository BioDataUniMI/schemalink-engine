import os
import json
import re
from openai import OpenAI
from schemalink_engine.api_key_manager import APIKeyManager

# Initialize OpenAI client lazily
client = None

def get_openai_client():
    global client
    if client is None:
        api_manager = APIKeyManager()
        api_key = api_manager.get_api_key()
        
        if not api_key:
            print("❌ No OpenAI API key found!")
            print("💡 Set your API key using: schemalink api-key set <your-key>")
            print("💡 Or set environment variable: export OPENAI_API_KEY=<your-key>")
            exit(1)
        
        client = OpenAI(api_key=api_key)
    return client

# Load reference tables
def load_reference_table(path):
    valid_items = set()
    if not os.path.exists(path):
        return valid_items
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            parts = line.strip().split("\t")
            if len(parts) > 0:
                valid_items.add(parts[0].strip().lower())
    return valid_items

def _get_lookup_path(filename):
    candidates = [
        os.path.join(os.getcwd(), "lookup_tables", filename),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lookup_tables", filename),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

def _load_lazy(filename):
    path = _get_lookup_path(filename)
    return load_reference_table(path) if path else set()

_valid_diseases = None
_valid_genes = None
_valid_proteins = None
_valid_gos = None

def _get_valid_diseases():
    global _valid_diseases
    if _valid_diseases is None:
        _valid_diseases = _load_lazy("diseases.txt")
    return _valid_diseases

def _get_valid_genes():
    global _valid_genes
    if _valid_genes is None:
        _valid_genes = _load_lazy("genes.txt")
    return _valid_genes

def _get_valid_proteins():
    global _valid_proteins
    if _valid_proteins is None:
        _valid_proteins = _load_lazy("protein.txt")
    return _valid_proteins

def _get_valid_gos():
    global _valid_gos
    if _valid_gos is None:
        _valid_gos = _load_lazy("go.txt")
    return _valid_gos

def process_inherited_entity_classes(
    schema, responses_file, text, response_formats_path,
    output_responses_path, prompts_save_path, single_dependency_classes, generate_prompts_only=False, ground_entities=False, add_guidelines=False
):
    # print("\n🚀 Processing inherited entity classes...\n")
    print(generate_prompts_only)
    os.makedirs(os.path.dirname(prompts_save_path), exist_ok=True)

    with open(responses_file) as rf, open(response_formats_path) as frf:
        responses = json.load(rf)
        response_formats = json.load(frf)

    combined_responses = responses
    generated_prompts = {}
    schema_title = schema.get("title", "")
    schema_description = schema.get("description", "")
    schema_intro = (
          f"The schema is titled '{schema_title}' and described as follows: {schema_description}."
          if schema_title and schema_description
          else f"the schema is described as follows: {schema_description}."
          if schema_description
          else f"The schema is titled '{schema_title}'"
          if schema_title
          else ""
      )
    
    # Get curator identity or use default
    curator_identity = schema.get("curator_identity", "an expert annotator")


    # Static prompt generation: use exact same logic as runtime, but with placeholders
    if generate_prompts_only:
        print("🔧 Static prompt mode enabled: using placeholders in prompts.")
        print(single_dependency_classes)
        for child_class, parent_class in single_dependency_classes.items():
            print(f"\n📝 Generating static prompt for {child_class} (inherits from {parent_class})")

            child_info = schema["classes"].get(child_class, {})
            parent_info = schema["classes"].get(parent_class, {})
            parent_identifier_key = next((k for k, v in parent_info.get("attributes", {}).items() if v.get("identifier")), None)

            # Use placeholder instead of actual parent instances
            parent_instances = ["{parent_instances}"]  # Placeholder for static mode
            
            # Build prompts - EXACT same logic as runtime
            examples = child_info.get("annotations", {}).get("prompt.examples", "")
            guidelines = child_info.get("annotations", {}).get("guidelines", "")
            algo_rule = child_info.get("annotations", {}).get("algorithmic_rules", "")
            id_prefixes = child_info.get("id_prefixes", [])
            id_prefixes_text = ""

            if isinstance(id_prefixes, list) and len(id_prefixes) == 1:
                id_prefixes_text = f"Mentions for this class correspond to terms of the {id_prefixes[0]} ontology."
            elif isinstance(id_prefixes, list) and len(id_prefixes) > 1:
                joined = ", ".join(id_prefixes)
                id_prefixes_text = f"Mentions for this class correspond to terms from the following ontologies: {joined}."

            # Only create class_desc if description exists and is not empty
            child_description = child_info.get('description', '')
            class_desc = f"A '{child_class}' is defined as: {child_description}." if child_description and child_description.strip() else ""
            
            # Task instructions - Initialize with default value
            task_instructions = ""
            try:
                task_instructions = (
                    f"Instances of this class are derived from the parent class '{parent_class}', "
                    f"which includes only the following known mentions: {{parent_instances}}.\n\n"
                    f"Extract only those '{child_class}' entities that are explicitly mentioned in the text **AND** they appear in the above list of known {parent_class}s. \n"
                    f"{id_prefixes_text + chr(10) + chr(10) if id_prefixes_text else ''}"
                    f"Return a list of all entity mentions values for the class {child_class} Using the provided JSON schema format."
                )
            except Exception as e:
                print(f"⚠️ Error creating task_instructions for {child_class}: {e}")
                task_instructions = f"Extract instances of class {child_class} that are explicitly mentioned in the text."
            
            # Schema description - only include class_desc if it's not empty
            if class_desc:
                schema_description = f"{schema_intro}\n\n{class_desc}"
            else:
                schema_description = schema_intro
            
            # Filter out attributes with identifier: true
            parent_attributes = {
                k: v for k, v in parent_info.get("attributes", {}).items() if not v.get("identifier", False)
            }
            child_attributes = {
                k: v for k, v in child_info.get("attributes", {}).items() if not v.get("identifier", False)
            }

            all_attributes = list(parent_attributes.keys()) + list(child_attributes.keys())

            if all_attributes:
                attribute_prompt = (
                    f"#Identity\nYou are {curator_identity}.\n\n"
                    f"#Instructions\nFor each entity mention identified as an instance of class '{child_class}', "
                    f"extract the following attributes: {', '.join(all_attributes)}. All the Attributes should be **explicitly mentioned in the text**. "
                    f"The mentions should match the entities already extracted by chatGPT from the text: {{extracted_labels}}."
                )
            else:
                attribute_prompt = ""  # Don't include the prompt if there's nothing to extract

            # System prompt construction - EXACT same logic as runtime
            system_prompt_parts = [
                f"# Identity\nYou are {curator_identity}.",
                f"# Schema\n\n{schema_description}",
                f"# Task\n\n{task_instructions}",
                f"# Additional Guidelines\n{guidelines}" if (add_guidelines and guidelines) else "",
                f"# Examples\n{examples}" if examples else ""
            ]
            schema_prompt = "\n\n".join([part for part in system_prompt_parts if part.strip()])
            
            # Save static prompts (only include attribute prompts if there are non-identifier attributes)
            if attribute_prompt:
                generated_prompts[child_class] = {
                    "schema_prompt": schema_prompt,
                    "attribute_prompt": attribute_prompt
                }
            else:
                generated_prompts[child_class] = {
                    "schema_prompt": schema_prompt
                }

        # Save all prompts at once
        with open(prompts_save_path, "w") as out:
            json.dump(generated_prompts, out, indent=4)
        print(f"\n✅ Static prompts saved to {prompts_save_path}")
        return

    else:
      print(single_dependency_classes)
      for child_class, parent_class in single_dependency_classes.items():
        print(f"\n🔹 Processing {child_class} (inherits from {parent_class})")

        child_info = schema["classes"].get(child_class, {})
        parent_info = schema["classes"].get(parent_class, {})
        parent_identifier_key = next((k for k, v in parent_info.get("attributes", {}).items() if v.get("identifier")), None)

        # if not parent_identifier_key:
        #     print(f"⚠️ No identifier found in parent {parent_class}. Skipping.")
        #     continue
        
        # Get parent response, handle None case
        parent_response = responses.get(parent_class)
        if parent_response is None or not isinstance(parent_response, dict):
            print(f"⚠️ No valid response for {parent_class}. Skipping {child_class}.")
            continue
        
        # Get schemaResponse, handle None case
        schema_response = parent_response.get("schemaResponse")
        if schema_response is None or not isinstance(schema_response, dict):
            print(f"⚠️ No valid schemaResponse for {parent_class}. Skipping {child_class}.")
            continue
        
        parent_instances = schema_response.get("mentions", [])
        if not parent_instances:
            print(f"⚠️ No instances for {parent_class}. Skipping {child_class}.")
            continue
        
        # Build prompts
        examples = child_info.get("annotations", {}).get("prompt.examples", "")
        guidelines = child_info.get("annotations", {}).get("guidelines", "")
        algo_rule = child_info.get("annotations", {}).get("algorithmic_rules", "")
        id_prefixes = child_info.get("id_prefixes", [])
        id_prefixes_text = ""

        if isinstance(id_prefixes, list) and len(id_prefixes) == 1:
            id_prefixes_text = f"Mentions for this class correspond to terms of the {id_prefixes[0]} ontology."
        elif isinstance(id_prefixes, list) and len(id_prefixes) > 1:
            joined = ", ".join(id_prefixes)
            id_prefixes_text = f"Mentions for this class correspond to terms from the following ontologies: {joined}."

        # Only create class_desc if description exists and is not empty
        child_description = child_info.get('description', '')
        class_desc = f"A '{child_class}' is defined as: {child_description}." if child_description and child_description.strip() else ""
        
        # Task instructions - Initialize with default value
        task_instructions = ""
        try:
            task_instructions = (
                f"Instances of this class are derived from the parent class '{parent_class}', "
                f"which includes only the following known mentions: {', '.join(str(x) for x in parent_instances)}.\n\n"
                f"Extract only those '{child_class}' entities that are explicitly mentioned in the text **AND** they appear in the above list of known {parent_class}s. \n"
                f"{id_prefixes_text + chr(10) + chr(10) if id_prefixes_text else ''}"
                f"Return a list of all entity mentions values for the class {child_class}."
            )
        except Exception as e:
            print(f"⚠️ Error creating task_instructions for {child_class}: {e}")
            task_instructions = f"Extract instances of class {child_class} that are explicitly mentioned in the text."
        
        # Schema description - only include class_desc if it's not empty
        if class_desc:
            schema_description = f"{schema_intro}\n\n{class_desc}"
        else:
            schema_description = schema_intro
        
        # Build schema_prompt - only include class_desc if it's not empty
        schema_prompt_parts = [schema_intro]
        if class_desc:
            schema_prompt_parts.append(class_desc)
        schema_prompt_parts.append("")
        schema_prompt_parts.append(
            f"Instances of this class are derived from the parent class '{parent_class}', "
            f"which includes only the following known mentions: {', '.join(str(x) for x in parent_instances)}.\n\n"
            f"Extract only those '{child_class}' entities that are explicitly mentioned in the text **AND** they appear in the above list of known {parent_class}s. \n"
            f"{id_prefixes_text + chr(10) + chr(10) if id_prefixes_text else ''}"
            f"Return a list of all entity mentions values for the class {child_class}."
        )
        schema_prompt = "\n\n".join(schema_prompt_parts)

        # Filter out attributes with identifier: true
        parent_attributes = {
            k: v for k, v in parent_info.get("attributes", {}).items() if not v.get("identifier", False)
        }
        child_attributes = {
            k: v for k, v in child_info.get("attributes", {}).items() if not v.get("identifier", False)
        }

        all_attributes = list(parent_attributes.keys()) + list(child_attributes.keys())

        if all_attributes:
            attribute_prompt = (
                f"#Identity\nYou are {curator_identity}.\n\n"
                f"#Instructions\nFor each entity mention identified as an instance of class '{child_class}', "
                f"extract the following attributes: {', '.join(all_attributes)}. All the Attributes should be **explicitly mentioned in the text**. "
                f"The mentions should match the entities already extracted by chatGPT from the text: {{extracted_labels}}."
            )
        else:
            attribute_prompt = ""  # Don't include the prompt if there's nothing to extract


        system_prompt_parts = [
            f"# Identity\nYou are {curator_identity}.",
            f"# Schema\n\n{schema_description}",
            f"# Task\n\n{task_instructions}",
            f"# Additional Guidelines\n{guidelines}" if (add_guidelines and guidelines) else "",
            f"# Examples\n{examples}" if examples else ""
        ]
        system_prompt = "\n\n".join([part for part in system_prompt_parts if part.strip()])
        schema_prompt=system_prompt
        # Extract responses
        extracted_labels = []
        schema_format = response_formats.get(child_class, {}).get("schemaResponseFormat", {}).get("json_schema")
        attribute_format = response_formats.get(child_class, {}).get("attributeResponseFormat", {}).get("json_schema")

        if schema_format:
            try:
                schema_response = get_openai_client().chat.completions.create(
                    model="gpt-4o-2024-08-06",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Text:\n{text}"}
                    ],
                    response_format={"type": "json_schema", "json_schema": schema_format},
                    temperature=0
                )
                schema_response_json = json.loads(schema_response.choices[0].message.content)
                extracted_labels = list(schema_response_json.values())[0]
                print(f"TRACE:NE_INIT:{child_class}:{json.dumps(list(extracted_labels))}")

                # ✅ 1. Algorithmic rules FIRST
                if algo_rule:
                    try:
                        pattern = re.compile(algo_rule)
                        def get_label_text(label):
                            """Extract text from label (string or dict)"""
                            if isinstance(label, dict):
                                return label.get("name", label.get("label", ""))
                            return str(label)
                        _pre_filter_inh = list(extracted_labels)
                        extracted_labels = [v for v in extracted_labels if pattern.match(get_label_text(v))]
                        _removed_filter_inh = [l for l in _pre_filter_inh if l not in extracted_labels]
                        print(f"TRACE:NE_FILTERED:{child_class}:{json.dumps(extracted_labels)}")
                        if _removed_filter_inh:
                            print(f"TRACE:NE_FILTER_REMOVED:{child_class}:{json.dumps(_removed_filter_inh)}")
                    except re.error as e:
                        print(f"⚠️ Invalid regex: {e}")
                    except Exception as e:
                        print(f"⚠️ Error applying algorithmic rule: {e}")

                # ✅ 2. Grounding SECOND — automatic when annotators exist (inherit from parent)
                _inh_dep_annotator = (
                    child_info.get("annotations", {}).get("annotators", "")
                    or parent_info.get("annotations", {}).get("annotators", "")
                )
                if _inh_dep_annotator:
                    from schemalink_engine.utils.grounding import GroundingManager
                    threshold = ground_entities.get('threshold', 1.0) if isinstance(ground_entities, dict) else 1.0
                    mode = ground_entities.get('mode', 'auto') if isinstance(ground_entities, dict) else 'auto'
                    grounding_manager = GroundingManager(threshold=threshold, mode=mode)
                    _pre_ground_inh = list(extracted_labels)
                    extracted_labels = grounding_manager.ground_entities(extracted_labels, _inh_dep_annotator)
                    _grounded_texts_inh = set()
                    for _gl in extracted_labels:
                        _t = _gl.get('name', _gl.get('label', '')) if isinstance(_gl, dict) else str(_gl)
                        _grounded_texts_inh.add(_t.lower())
                    _removed_ground_inh = [l for l in _pre_ground_inh if (l.lower() if isinstance(l, str) else str(l).lower()) not in _grounded_texts_inh]
                    print(f"TRACE:NE_GROUNDED:{child_class}:{json.dumps(extracted_labels)}")
                    if _removed_ground_inh:
                        print(f"TRACE:NE_GROUNDING_REMOVED:{child_class}:{json.dumps(_removed_ground_inh)}")

                schema_response_json["mentions"] = extracted_labels
                combined_responses[child_class] = {"schemaResponse": schema_response_json}

            except Exception as e:
                print(f"❌ GPT error (schema prompt) for {child_class}: {e}")

        # Attribute extraction (only if there are non-identifier attributes)
        if attribute_format and extracted_labels and attribute_prompt:
            try:
                # Replace the placeholder with actual extracted labels
                full_attr_prompt = attribute_prompt.replace("{extracted_labels}", ', '.join(str(label) for label in extracted_labels))
                attribute_response = get_openai_client().chat.completions.create(
                    model="gpt-4o-2024-08-06",
                    messages=[
                        {"role": "system", "content": full_attr_prompt},
                        {"role": "user", "content": f"Text:\n{text}"}
                    ],
                    response_format={"type": "json_schema", "json_schema": attribute_format},
                    temperature=0
                )

                combined_responses[child_class]["attributeResponse"] = json.loads(attribute_response.choices[0].message.content)
            except Exception as e:
                print(f"❌ GPT error (attribute prompt) for {child_class}: {e}")

        # Save
        with open(output_responses_path, "w") as out:
            json.dump(combined_responses, out, indent=4)
        # Only save attribute prompts if there are non-identifier attributes
        if attribute_prompt:
            # If we're doing actual extraction (not just generating prompts), save the prompt with replaced values
            if not generate_prompts_only and extracted_labels and attribute_format and 'full_attr_prompt' in locals():
                generated_prompts[child_class] = {
                    "schema_prompt": schema_prompt,
                    "attribute_prompt": full_attr_prompt
                }
            else:
                # If just generating prompts, save the original with placeholders
                generated_prompts[child_class] = {
                    "schema_prompt": schema_prompt,
                    "attribute_prompt": attribute_prompt
                }
        else:
            generated_prompts[child_class] = {
                "schema_prompt": schema_prompt
            }
        with open(prompts_save_path, "w") as out:
            json.dump(generated_prompts, out, indent=4)

    print(f"\n✅ All inherited prompts saved to {prompts_save_path}")


def process_inherited_entity_classes_without_dep(
    schema, responses_file, text, response_formats_path,
    output_responses_path, prompts_save_path, single_dependency_classes, generate_prompts_only=False, ground_entities=False, add_guidelines=False
):
    print("\n🚀 Processing inherited entity classes (no dependency)...\n")
    os.makedirs(os.path.dirname(prompts_save_path), exist_ok=True)

    with open(response_formats_path) as frf:
        response_formats = json.load(frf)

    generated_prompts = {}
    combined_responses = {}

    # Extract schema title and description - use same logic as main function
    schema_title = schema.get("title", "")
    schema_description = schema.get("description", "")
    schema_intro = (
          f"The schema is titled '{schema_title}' and described as follows: {schema_description}."
          if schema_title and schema_description
          else f"the schema is described as follows: {schema_description}."
          if schema_description
          else f"The schema is titled '{schema_title}'"
          if schema_title
          else ""
      )
    
    # Get curator identity or use default
    curator_identity = schema.get("curator_identity", "an expert annotator")

    for child_class, parent_class in single_dependency_classes.items():
        print(f"\n🔹 Processing {child_class} (inherits from {parent_class})")

        child_info = schema["classes"].get(child_class, {})
        parent_info = schema["classes"].get(parent_class, {})

        # Prompt content
        examples = child_info.get("annotations", {}).get("prompt.examples", "")
        guidelines = child_info.get("annotations", {}).get("guidelines", "")
        algo_rule = child_info.get("annotations", {}).get("algorithmic_rules", "")
        id_prefixes = child_info.get("id_prefixes", [])
        id_prefixes_text = ""

        if isinstance(id_prefixes, list) and len(id_prefixes) == 1:
            id_prefixes_text = f"Mentions for this class correspond to terms of the {id_prefixes[0]} ontology."
        elif isinstance(id_prefixes, list) and len(id_prefixes) > 1:
            joined = ", ".join(id_prefixes)
            id_prefixes_text = f"Mentions for this class correspond to terms from the following ontologies: {joined}."

        # Only create class_desc if description exists and is not empty
        child_description = child_info.get('description', '')
        class_desc = f"A '{child_class}' is defined as: {child_description}." if child_description and child_description.strip() else ""
        
        # Only create parent_desc if description exists and is not empty
        parent_description = parent_info.get('description', '')
        parent_desc = f"A '{parent_class}' is defined as: {parent_description}." if parent_description and parent_description.strip() else ""

        # Schema description only - only include non-empty descriptions
        desc_parts = [schema_intro]
        if class_desc:
            desc_parts.append(class_desc)
        if parent_desc:
            desc_parts.append(parent_desc)
        schema_description = "\n\n".join(desc_parts)
        
        # Task instructions - Initialize with default value
        task_instructions = ""
        try:
            task_instructions = (
                f"Instances of class {child_class} are derived from the parent class '{parent_class}'.\n"
                f"Extract only those '{child_class}' entities that are explicitly mentioned in the text.\n"
                f"{id_prefixes_text + chr(10) + chr(10) if id_prefixes_text else ''}"
                f"Return a list of all entity mentions values for the class {child_class} Using the provided JSON schema format."
            )
        except Exception as e:
            print(f"⚠️ Error creating task_instructions for {child_class}: {e}")
            task_instructions = f"Extract instances of class {child_class} that are explicitly mentioned in the text."

        # Attribute filtering
        parent_attributes = {
            k: v for k, v in parent_info.get("attributes", {}).items() if not v.get("identifier", False)
        }
        child_attributes = {
            k: v for k, v in child_info.get("attributes", {}).items() if not v.get("identifier", False)
        }

        all_attributes = list(parent_attributes.keys()) + list(child_attributes.keys())

        attribute_prompt = ""
        if all_attributes:
            attr_text = f"{', '.join(all_attributes)}"
            # Both static and runtime modes use the same format
            attribute_prompt = (
                f"#Identity\nYou are {curator_identity}.\n\n"
                f"#Instructions\nFor each entity mention identified as an instance of class '{child_class}', "
                f"extract the following attributes: {attr_text}. All the Attributes should be **explicitly mentioned in the text**. "
                f"The mentions should match the entities already extracted by chatGPT from the text: {{extracted_labels}}."
            )

        # Final prompt
        system_prompt_parts = [
            f"# Identity\nYou are {curator_identity}.",
            f"# Schema\n\n{schema_description}",
            f"# Task\n\n{task_instructions}",
            f"# Additional Guidelines\n{guidelines}" if (add_guidelines and guidelines) else "",
            f"# Examples\n{examples}" if examples else ""
        ]
        final_schema_prompt = "\n\n".join([part for part in system_prompt_parts if part.strip()])

        # In generate_prompts_only mode → only save prompts (only include attribute prompts if there are non-identifier attributes)
        if generate_prompts_only:
            if attribute_prompt:
                generated_prompts[child_class] = {
                    "schema_prompt": final_schema_prompt,
                    "attribute_prompt": attribute_prompt
                }
            else:
                generated_prompts[child_class] = {
                    "schema_prompt": final_schema_prompt
                }
            continue

        # 🔄 Runtime Extraction
        schema_format = response_formats.get(child_class, {}).get("schemaResponseFormat", {}).get("json_schema")
        attribute_format = response_formats.get(child_class, {}).get("attributeResponseFormat", {}).get("json_schema")
        extracted_labels = []

        if schema_format:
            try:
                schema_response = get_openai_client().chat.completions.create(
                    model="gpt-4o-2024-08-06",
                    messages=[
                        {"role": "system", "content": final_schema_prompt},
                        {"role": "user", "content": f"Text:\n{text}"}
                    ],
                    response_format={"type": "json_schema", "json_schema": schema_format},
                    temperature=0
                )
                schema_response_json = json.loads(schema_response.choices[0].message.content)
                extracted_labels = list(schema_response_json.values())[0]
                _wdep_trace_key = child_class
                print(f"TRACE:NE_INIT:{_wdep_trace_key}:{json.dumps(list(extracted_labels))}")

                # ✅ 1. Apply algorithmic rules FIRST
                if algo_rule:
                    try:
                        pattern = re.compile(algo_rule)
                        def get_label_text(label):
                            """Extract text from label (string or dict)"""
                            if isinstance(label, dict):
                                return label.get("name", label.get("label", ""))
                            return str(label)
                        _pre_filter_wdep = list(extracted_labels)
                        extracted_labels = [v for v in extracted_labels if pattern.match(get_label_text(v))]
                        _removed_filter_wdep = [l for l in _pre_filter_wdep if l not in extracted_labels]
                        print(f"TRACE:NE_FILTERED:{_wdep_trace_key}:{json.dumps(extracted_labels)}")
                        if _removed_filter_wdep:
                            print(f"TRACE:NE_FILTER_REMOVED:{_wdep_trace_key}:{json.dumps(_removed_filter_wdep)}")
                    except re.error as e:
                        print(f"⚠️ Invalid regex: {e}")
                    except Exception as e:
                        print(f"⚠️ Error applying algorithmic rule: {e}")

                # ✅ 2. Grounding SECOND — automatic when annotators exist (inherit from parent)
                _wdep_annotator = (
                    child_info.get("annotations", {}).get("annotators", "")
                    or parent_info.get("annotations", {}).get("annotators", "")
                )
                if _wdep_annotator:
                    from schemalink_engine.utils.grounding import GroundingManager
                    threshold = ground_entities.get('threshold', 1.0) if isinstance(ground_entities, dict) else 1.0
                    mode = ground_entities.get('mode', 'auto') if isinstance(ground_entities, dict) else 'auto'
                    grounding_manager = GroundingManager(threshold=threshold, mode=mode)
                    _pre_ground_wdep = list(extracted_labels)
                    extracted_labels = grounding_manager.ground_entities(extracted_labels, _wdep_annotator, entity_class=child_class)
                    _grounded_texts_wdep = set()
                    for _gl in extracted_labels:
                        _t = _gl.get('name', _gl.get('label', '')) if isinstance(_gl, dict) else str(_gl)
                        _grounded_texts_wdep.add(_t.lower())
                    _removed_ground_wdep = [l for l in _pre_ground_wdep if (l.lower() if isinstance(l, str) else str(l).lower()) not in _grounded_texts_wdep]
                    print(f"TRACE:NE_GROUNDED:{_wdep_trace_key}:{json.dumps(extracted_labels)}")
                    if _removed_ground_wdep:
                        print(f"TRACE:NE_GROUNDING_REMOVED:{_wdep_trace_key}:{json.dumps(_removed_ground_wdep)}")

                schema_response_json["mentions"] = extracted_labels
                combined_responses[child_class] = {"schemaResponse": schema_response_json}

            except Exception as e:
                print(f"❌ GPT error (schema prompt) for {child_class}: {e}")

        # 🔄 Attributes (only if there are non-identifier attributes)
        if attribute_format and extracted_labels and attribute_prompt:
            try:
                # Replace the placeholder with actual extracted labels
                # Handle both string and dict formats (dicts come from grounding)
                def get_label_text(label):
                    """Extract text from label (string or dict)"""
                    if isinstance(label, dict):
                        return label.get("name", label.get("label", ""))
                    return str(label)
                
                label_texts = [get_label_text(label) for label in extracted_labels]
                full_attr_prompt = attribute_prompt.replace("{extracted_labels}", ', '.join(label_texts))
                attribute_response = get_openai_client().chat.completions.create(
                    model="gpt-4o-2024-08-06",
                    messages=[
                        {"role": "system", "content": full_attr_prompt},
                        {"role": "user", "content": f"Text:\n{text}"}
                    ],
                    response_format={"type": "json_schema", "json_schema": attribute_format},
                    temperature=0
                )
                combined_responses[child_class]["attributeResponse"] = json.loads(attribute_response.choices[0].message.content)
            except Exception as e:
                print(f"❌ GPT error (attribute prompt) for {child_class}: {e}")

        # Only save attribute prompts if there are non-identifier attributes
        if attribute_prompt:
            # If we're doing actual extraction (not just generating prompts), save the prompt with replaced values
            if not generate_prompts_only and 'full_attr_prompt' in locals():
                generated_prompts[child_class] = {
                    "schema_prompt": final_schema_prompt,
                    "attribute_prompt": full_attr_prompt
                }
            else:
                # If just generating prompts, save the original with placeholders
                generated_prompts[child_class] = {
                    "schema_prompt": final_schema_prompt,
                    "attribute_prompt": attribute_prompt
                }
        else:
            generated_prompts[child_class] = {
                "schema_prompt": final_schema_prompt
            }

    # Save outputs
    if generate_prompts_only:
        with open(prompts_save_path, "w") as out:
            json.dump(generated_prompts, out, indent=4)
        print(f"\n✅ Static prompts saved to {prompts_save_path}")
    else:
        # Load existing responses first to avoid overwriting Person and Entity
        try:
            with open(output_responses_path, "r") as f:
                existing_responses = json.load(f)
        except FileNotFoundError:
            existing_responses = {}
        
        # Merge with new responses
        existing_responses.update(combined_responses)
        
        # Save merged responses
        with open(output_responses_path, "w") as out:
            json.dump(existing_responses, out, indent=4)
        with open(prompts_save_path, "w") as out:
            json.dump(generated_prompts, out, indent=4)
        print(f"\n✅ Prompts and results saved to disk.")