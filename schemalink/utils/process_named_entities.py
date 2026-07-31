import json
import time
from openai import OpenAI
import re
import os
from schemalink.api_key_manager import APIKeyManager

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
    # Get the directory of this file and find the project root
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))  # Go up two levels from utils/
    full_path = os.path.join(project_root, path)
    
    try:
        with open(full_path, "r", encoding="utf-8") as file:
            for line in file:
                parts = line.strip().split("\t")
                if len(parts) > 0:
                    valid_items.add(parts[0].strip().lower())
    except FileNotFoundError:
        print(f"⚠️ Warning: Reference table not found at {full_path}")
    return valid_items

valid_diseases = load_reference_table("lookup_tables/diseases.txt")
valid_genes = load_reference_table("lookup_tables/genes.txt")
valid_proteins = load_reference_table("lookup_tables/protein.txt")
valid_gos = load_reference_table("lookup_tables/go.txt")

def process_named_entity_classes(
    named_entity_classes, schema_path, text_sample_path, response_formats_path, output_responses_path, prompts_save_path, generate_prompts_only=False, add_guidelines=False, ground_entities=False
):
    """
    Generate prompts, call GPT for named entity extraction, and save results.

    Args:
        named_entity_classes (dict): Named entity classes to process.
        schema_path (str): Path to the schema JSON file.
        text_sample_path (str): Path to the input text sample file.
        response_formats_path (str): Path to the response formats JSON file.
        output_responses_path (str): Path to save the extracted responses.
        prompts_save_path (str): Path to save the generated prompts.

    Returns:
        None: Saves generated responses and prompts to their respective files.
    """
    # Load schema and text
    with open(schema_path, "r") as file:
        schema = json.load(file)

    with open(text_sample_path, "r") as file:
        text = file.read()

    # Load response formats
    with open(response_formats_path, "r") as schema_file:
        response_formats = json.load(schema_file)

    combined_responses = {}
    generated_prompts = {}


      # Extract schema title and description
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
    
    # Process each named entity class
    for class_name, details in named_entity_classes.items():
          class_info = schema["classes"].get(class_name, {})

          # Resolve abstract parent (if any) and inherit annotations from it
          _is_a = class_info.get("is_a", "")
          _parent_info = schema["classes"].get(_is_a, {}) if _is_a else {}
          _abstract_parent = _is_a if _parent_info.get("abstract") else None
          _trace_key = _abstract_parent if _abstract_parent else class_name

          _own_ann = class_info.get("annotations", {}) if isinstance(class_info.get("annotations", {}), dict) else {}
          _par_ann = _parent_info.get("annotations", {}) if isinstance(_parent_info.get("annotations", {}), dict) else {}
          _merged_ann = {**_par_ann, **_own_ann}  # child overrides parent

          # Merge parent attributes into child (parent first, child overrides)
          _parent_attrs = _parent_info.get("attributes", {}) if _abstract_parent else {}
          _merged_attrs = {**_parent_attrs, **class_info.get("attributes", {})}

          class_description = class_info.get("description", "") or _parent_info.get("description", "")
          attributes = _merged_attrs

          # Generate schema description and task prompts separately
          class_intro = f"A '{class_name}' is defined as: {class_description}. " if class_description else ""
          
          # Schema description only
          schema_description = f"{schema_intro}"
          
          # Task instructions - Initialize with default value
          task_instructions = ""
          try:
              task_instructions = (
                  f"Extract all instances of class '{class_name}' that are **explicitly mentioned in the provided text**. **If a '{class_name}' is not explicitly written in the text, do not include it in the response, even if it is commonly associated with the entities mentioned.**  The extraction should be strictly limited to the words present in the text. "
                  f"{class_intro} Return a list of all entity mentions for the class {class_name} using the provided JSON schema format."
              )
          except Exception as e:
              print(f"⚠️ Error creating task_instructions for {class_name}: {e}")
              task_instructions = f"Extract instances of class {class_name} that are explicitly mentioned in the text."


          attribute_descriptions = [
              f"{attr_name} which is described as '{attr_details['description']}'"
              if "description" in attr_details else attr_name
              for attr_name, attr_details in attributes.items()
              if not attr_details.get("identifier", False)  # ✅ Exclude identifier fields
          ]

          attribute_prompt = (
              f"#Identity\nYou are {curator_identity}.\n\n"
              f"#Instructions\nFor each entity mention identified as an instance of class '{class_name}', "
              f"extract the following attributes: {', '.join(attribute_descriptions)}. All the Attributes should be **explicitly mentioned in the text**. "
              f"The mentions should match the entities already extracted by chatGPT from the text: {{extracted_labels}}."
          )

          # Extract response formats
          schema_response_format = response_formats.get(class_name, {}).get("schemaResponseFormat")
          attribute_response_format = response_formats.get(class_name, {}).get("attributeResponseFormat")

          combined_responses[class_name] = {"schemaResponse": None}
          extracted_labels = []



          # Get entity guidelines if available
          entity_guidelines = schema.get("entity_guidelines", "").strip()

          # Get class-specific additional guidelines (using merged annotations)
          additional_guidelines = _merged_ann.get("guidelines", "").strip()

          # Get examples from annotations
          raw_examples = _merged_ann.get("prompt.examples", "").strip()

          example_block = ""
          if raw_examples:
              example_block = f"Here are some examples of class {class_name}: {raw_examples}"


          # Build system prompt dynamically
          system_prompt_parts = [
              f"# Identity\n\nYou are {curator_identity}."
          ]

          # Add schema description first
          system_prompt_parts.append(f"# Schema\n\n{schema_description}")
          
          # Add schema-level guidelines after schema description (only if add_guidelines is True)
          if add_guidelines and entity_guidelines:
              system_prompt_parts.append(f"# Instructions\n\n{entity_guidelines}")
          
          # Add task instructions
          system_prompt_parts.append(f"# Task\n\n{task_instructions}")
          
          # Add class-specific guidelines at the end (only if add_guidelines is True)
          if add_guidelines and additional_guidelines:
              system_prompt_parts.append(f"# Additional Guidelines\n\n{additional_guidelines}")

          if example_block:
              system_prompt_parts.append(f"# Examples\n\n{example_block}")
          

          # Get id prefixes to add to the prompt
          id_prefixes = class_info.get("id_prefixes", []) or _parent_info.get("id_prefixes", [])

          id_prefixes_text = ""
          if isinstance(id_prefixes, list) and len(id_prefixes) == 1:
              id_prefixes_text = f"Mentions for this class correspond to terms of the {id_prefixes[0]} ontology."
          elif isinstance(id_prefixes, list) and len(id_prefixes) > 1:
              joined_prefixes = ", ".join(id_prefixes)
              id_prefixes_text = f"Mentions for this class correspond to terms from the following ontologies: {joined_prefixes}."
          
          if id_prefixes_text:
              system_prompt_parts.append(f"\n {id_prefixes_text}")


          system_prompt = "\n\n".join(system_prompt_parts)
          schema_prompt=system_prompt

          # Call GPT for schema response
          if generate_prompts_only:
            if attribute_descriptions:
                # For static prompt generation, use the same format as runtime
                generated_prompts[class_name] = {"schema_prompt": schema_prompt, "attribute_prompt": attribute_prompt}
            else:
                # No attribute prompts if no attributes
                generated_prompts[class_name] = {"schema_prompt": schema_prompt}
            continue  # ✅ Skip the rest of the loop for this class
          if schema_response_format:
              _ne_model = "gpt-4o-2024-08-06"
              print(f"TRACE:NE_START:{_trace_key}:{json.dumps({'model': _ne_model})}")
              _ne_t0 = time.perf_counter()
              _ne_prompt_tokens = 0
              _ne_completion_tokens = 0
              try:
                  schema_response = get_openai_client().chat.completions.create(
                      model=_ne_model,
                      messages=[
                          {"role": "system", "content": f"{system_prompt}"},
                          {"role": "user",
                          "content": f"""
                                      Extract the mentions from the following input:
                                      Text:\n{text}
                                      """
                          }
                      ],
                      response_format={"type": "json_schema", "json_schema": schema_response_format["json_schema"]},
                      temperature=0
                  )
                  _ne_elapsed_ms = int((time.perf_counter() - _ne_t0) * 1000)
                  if schema_response.usage:
                      _ne_prompt_tokens = schema_response.usage.prompt_tokens or 0
                      _ne_completion_tokens = schema_response.usage.completion_tokens or 0
                  schema_response_json = json.loads(schema_response.choices[0].message.content)
                  combined_responses[class_name]["schemaResponse"] = schema_response_json
                  extracted_labels = list(schema_response_json.values())[0] if schema_response_json else []
                  print(f"TRACE:NE_INIT:{_trace_key}:{json.dumps(list(extracted_labels))}")
                  print(f"TRACE:NE_TIMING:{_trace_key}:{json.dumps({'duration_ms': _ne_elapsed_ms, 'prompt_tokens': _ne_prompt_tokens, 'completion_tokens': _ne_completion_tokens, 'model': _ne_model})}")

                  # ✅ 1. Apply algorithmic rules FIRST (filter before grounding)
                  algorithmic_rule = _merged_ann.get("algorithmic_rules", "").strip()

                  if algorithmic_rule:
                      try:
                          pattern = re.compile(algorithmic_rule)
                          def get_label_text(label):
                              """Extract text from label (string or dict)"""
                              if isinstance(label, dict):
                                  return label.get("name", label.get("label", ""))
                              return str(label)
                          _pre_filter = list(extracted_labels)
                          filtered_labels = [label for label in extracted_labels if pattern.match(get_label_text(label))]
                          extracted_labels = filtered_labels
                          combined_responses[class_name]["schemaResponse"]["mentions"] = extracted_labels
                          _removed_by_filter = [_l for _l in _pre_filter if _l not in filtered_labels]
                          print(f"TRACE:NE_FILTERED:{_trace_key}:{json.dumps(extracted_labels)}")
                          if _removed_by_filter:
                              print(f"TRACE:NE_FILTER_REMOVED:{_trace_key}:{json.dumps(_removed_by_filter)}")
                      except re.error as e:
                          print(f"⚠️ Invalid regex for class '{class_name}': {e}")
                      except Exception as e:
                          print(f"⚠️ Error applying algorithmic rule for '{class_name}': {e}")

                  # ✅ 2. Apply grounding SECOND (on filtered labels only).
                  # Grounding is automatic whenever the class has annotators — no flag needed.
                  annotator_value = _merged_ann.get("annotators", "")
                  if annotator_value:
                      from schemalink.utils.grounding import GroundingManager
                      threshold = ground_entities.get('threshold', 1.0) if isinstance(ground_entities, dict) else 1.0
                      mode = ground_entities.get('mode', 'exact') if isinstance(ground_entities, dict) else 'exact'
                      grounding_manager = GroundingManager(threshold=threshold, mode=mode)
                      _pre_grounding = list(extracted_labels)
                      print(f"TRACE:NE_GROUNDING_START:{_trace_key}:{json.dumps({'count': len(_pre_grounding), 'annotator': annotator_value})}")
                      extracted_labels = grounding_manager.ground_entities(extracted_labels, annotator_value)
                      combined_responses[class_name]["schemaResponse"]["mentions"] = extracted_labels
                      _grounded_texts = set()
                      for _gl in extracted_labels:
                          _t = _gl.get('name', _gl.get('label', '')) if isinstance(_gl, dict) else str(_gl)
                          _grounded_texts.add(_t.lower())
                      _removed_by_grounding = [_l for _l in _pre_grounding if ((_l.lower() if isinstance(_l, str) else str(_l).lower()) not in _grounded_texts)]
                      print(f"TRACE:NE_GROUNDED:{_trace_key}:{json.dumps(extracted_labels)}")
                      if _removed_by_grounding:
                          print(f"TRACE:NE_GROUNDING_REMOVED:{_trace_key}:{json.dumps(_removed_by_grounding)}")
                  
         
              except Exception as e:
                  print(f"❌ Error processing schema prompt for {class_name}: {e}")
              finally:
                  _ne_total_ms = int((time.perf_counter() - _ne_t0) * 1000)
                  print(f"TRACE:NE_DONE:{_trace_key}:{json.dumps({'items': list(extracted_labels), 'total_ms': _ne_total_ms, 'prompt_tokens': _ne_prompt_tokens, 'completion_tokens': _ne_completion_tokens})}")

          # Call GPT for attribute response
          if extracted_labels and attribute_response_format:
                 try:
                     if extracted_labels:
                       # Replace the placeholder with actual extracted labels
                       full_attr_prompt = attribute_prompt.replace("{extracted_labels}", ', '.join(str(e) for e in extracted_labels))
                       attribute_response = get_openai_client().chat.completions.create(
                           model="gpt-4o-2024-08-06",
                           messages=[
                               {"role": "system", "content": full_attr_prompt},
                               {"role": "user", "content": f"Text:\n{text}"}
                           ],
                           response_format={"type": "json_schema", "json_schema": attribute_response_format["json_schema"]},
                           temperature=0
                       )
                       combined_responses[class_name]["attributeResponse"] = json.loads(attribute_response.choices[0].message.content)
                       print(f"TRACE:NE_ATTRS:{_trace_key}:{json.dumps(combined_responses[class_name]['attributeResponse'])}")
                 except Exception as e:
                     print(f"❌ Error processing attribute prompt for {class_name}: {e}")

          # Save generated prompts (only save attribute prompts if there are extracted labels)
          if attribute_response_format and extracted_labels:
              # If we're doing actual extraction (not just generating prompts), save the prompt with replaced values
              if not generate_prompts_only:
                  generated_prompts[class_name] = {"schema_prompt": schema_prompt, "attribute_prompt": full_attr_prompt}
              else:
                  # If just generating prompts, save the original with placeholders
                  generated_prompts[class_name] = {"schema_prompt": schema_prompt, "attribute_prompt": attribute_prompt}
          else:
              generated_prompts[class_name] = {"schema_prompt": schema_prompt}
        
    # Save responses and prompts
    with open(output_responses_path, "w") as output_file:
        json.dump(combined_responses, output_file, indent=4)
    print(f"📁 Responses saved to {output_responses_path}.")

    with open(prompts_save_path, "w") as prompts_file:
        json.dump(generated_prompts, prompts_file, indent=4)
    print(f"📁 Prompts saved to {prompts_save_path}.")