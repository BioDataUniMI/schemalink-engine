import json
import time
from difflib import SequenceMatcher
from openai import OpenAI
from schemalink.api_key_manager import APIKeyManager
from schemalink.utils.some_helper import get_class_info_for_prompt

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

def get_relationship_model() -> str:
    """Return the model to use for relationship extraction."""
    return APIKeyManager().get_relationship_model()

import json

def _normalize_text(value):
    if not isinstance(value, str):
        return ""
    return value.strip().casefold()

def _extract_mention_text(mention):
    if isinstance(mention, dict):
        return mention.get("name") or mention.get("label") or ""
    if mention is None:
        return ""
    return str(mention)

def _collect_schema_mentions(schema_response):
    mentions = []
    if isinstance(schema_response, dict):
        mentions_field = schema_response.get("mentions")
        if isinstance(mentions_field, list):
            mentions.extend(mentions_field)
        else:
            for value in schema_response.values():
                if isinstance(value, list):
                    mentions.extend(value)
    elif isinstance(schema_response, list):
        mentions.extend(schema_response)
    return mentions

def _build_entity_sets(existing_responses):
    entity_sets = {}
    for class_name, class_data in existing_responses.items():
        if not isinstance(class_data, dict):
            continue
        schema_response = class_data.get("schemaResponse")
        mentions = _collect_schema_mentions(schema_response)
        normalized_mentions = {
            _normalize_text(_extract_mention_text(mention))
            for mention in mentions
            if _normalize_text(_extract_mention_text(mention))
        }
        if normalized_mentions:
            entity_sets[class_name] = normalized_mentions
    return entity_sets

def _get_predicate_metadata(schema, predicate_class):
    predicate_class_info = schema.get("classes", {}).get(predicate_class, {})
    predicate_id_attr = predicate_class_info.get("attributes", {}).get("id", {})
    predicate_value = predicate_id_attr.get("pattern", "") or predicate_id_attr.get("const", "")
    predicate_values = [p.strip() for p in predicate_value.split("|") if p.strip()]
    canonical_predicate = predicate_class_info.get("annotations", {}).get("canonical_predicate", "").strip()
    is_predicate_free = not predicate_values and bool(canonical_predicate)
    return predicate_values, canonical_predicate, is_predicate_free

def _build_relationship_task_text(subject_part, object_part, predicate_values, is_predicate_free):
    if predicate_values:
        if len(predicate_values) == 1:
            return f'we wish to identify the relationship "{predicate_values[0]}" between {subject_part} and {object_part}.'
        predicates_str = ", ".join([f'"{p}"' for p in predicate_values[:-1]]) + f', or "{predicate_values[-1]}"'
        return f"we wish to identify the relationships {predicates_str} between {subject_part} and {object_part}."
    if is_predicate_free:
        return f"we wish to identify explicitly stated relationships between {subject_part} and {object_part}."
    return f"we wish to identify the relationship [PREDICATE NOT FOUND - CHECK SCHEMA] between {subject_part} and {object_part}."

def _restore_missing_predicates(response_payload, class_name, fill_predicate):
    if not isinstance(response_payload, dict) or not fill_predicate:
        return response_payload

    list_key = f"{class_name}Relationships"
    relations = response_payload.get(list_key)
    if not isinstance(relations, list):
        return response_payload

    for relation in relations:
        if isinstance(relation, dict) and not relation.get("predicate"):
            relation["predicate"] = fill_predicate

    return response_payload

def _calculate_similarity(text1, text2):
    """Calculate similarity ratio between two texts (0.0 to 1.0)"""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def _find_best_match(text, entity_set, similarity_threshold=0.6):
    """
    Find the best matching entity in the set using fuzzy matching.
    Returns the matched entity name if similarity >= threshold, else None.
    """
    if not text or not entity_set:
        return None
    
    normalized_text = _normalize_text(text)
    best_match = None
    best_similarity = 0.0
    
    for entity in entity_set:
        similarity = _calculate_similarity(normalized_text, entity)
        if similarity > best_similarity:
            best_similarity = similarity
            best_match = entity
    
    # Return match only if similarity meets threshold
    if best_similarity >= similarity_threshold:
        return best_match
    return None

def _resolve_relation_ids_from_entities(response_payload, class_name, subject_class, object_class, existing_responses):
    """
    Resolve relation subject/object plain-text labels to grounded entity dicts
    by looking them up in already-grounded entity responses.

    Used in the with-dependencies path where entities were grounded in an earlier
    step and their {id, name} dicts are already stored in existing_responses.

    For each relation that survived _filter_relations_by_entities:
      - Find the subject name in existing_responses[subject_class] mentions
      - Replace the plain string with the grounded dict {"id": ..., "name": ...}
      - Same for object
    Relations whose subject or object cannot be resolved are left as strings
    (they already passed the filter so they are valid entities, just not grounded).
    """
    if not isinstance(response_payload, dict):
        return response_payload

    list_key = f"{class_name}Relationships"
    relations = response_payload.get(list_key)
    if not isinstance(relations, list) or not relations:
        return response_payload

    # Build label → grounded dict lookup for subject and object classes
    def _build_label_map(class_name_key):
        label_map = {}
        class_data = existing_responses.get(class_name_key, {})
        mentions = (class_data.get("schemaResponse") or {}).get("mentions", [])
        for m in mentions:
            if isinstance(m, dict):
                name = (m.get("name") or m.get("label") or "").strip().casefold()
                if name:
                    label_map[name] = m
            elif isinstance(m, str):
                label_map[m.strip().casefold()] = m
        return label_map

    subj_map = _build_label_map(subject_class)
    obj_map = _build_label_map(object_class)

    for relation in relations:
        if not isinstance(relation, dict):
            continue
        # Resolve subject
        subj = relation.get("subject")
        if isinstance(subj, str):
            resolved = subj_map.get(subj.strip().casefold())
            if resolved is not None:
                relation["subject"] = resolved
        # Resolve object
        obj = relation.get("object")
        if isinstance(obj, str):
            resolved = obj_map.get(obj.strip().casefold())
            if resolved is not None:
                relation["object"] = resolved

    response_payload[list_key] = relations
    return response_payload


def _ground_entities_in_relations(response_payload, class_name, subject_class, object_class, schema, ground_entities_config):
    """
    Ground entities in relations using GroundingManager based on their class annotators.
    Removes relationships where either subject or object cannot be grounded.
    
    Args:
        response_payload: The response payload containing relationships
        class_name: Name of the relationship class (e.g., "ProteinToGeneRelationship")
        subject_class: Class name for subject entities (e.g., "Protein")
        object_class: Class name for object entities (e.g., "Gene")
        schema: Full schema dict (to get annotators for subject/object classes)
        ground_entities_config: Config dict for grounding (threshold, mode) or False/None
    
    Returns:
        Updated response_payload with grounded entities (or original if grounding disabled)
    """
    if not isinstance(response_payload, dict) or not ground_entities_config:
        return response_payload
    
    list_key = f"{class_name}Relationships"
    relations = response_payload.get(list_key)
    if not isinstance(relations, list) or not relations:
        return response_payload
    
    # Initialize GroundingManager
    from schemalink.utils.grounding import GroundingManager
    threshold = ground_entities_config.get('threshold', 1.0) if isinstance(ground_entities_config, dict) else 1.0
    mode = ground_entities_config.get('mode', 'exact') if isinstance(ground_entities_config, dict) else 'exact'
    grounding_manager = GroundingManager(threshold=threshold, mode=mode)
    
    # Get annotators for subject and object classes from schema
    subject_class_info = schema.get("classes", {}).get(subject_class, {})
    object_class_info = schema.get("classes", {}).get(object_class, {})
    
    subject_annotator = subject_class_info.get("annotations", {}).get("annotators", "")
    object_annotator = object_class_info.get("annotations", {}).get("annotators", "")
    
    # Check if classes are MiRNA (case-insensitive)
    is_subject_mirna = subject_class.lower() == "mirna"
    is_object_mirna = object_class.lower() == "mirna"
    
    grounded_relations = []
    removed_count = 0
    
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        
        subject_text = _extract_mention_text(relation.get("subject"))
        object_text = _extract_mention_text(relation.get("object"))
        
        if not subject_text or not object_text:
            removed_count += 1
            continue
        
        # Handle subject entity
        subject_grounded = None
        subject_needs_grounding = subject_annotator and not is_subject_mirna
        
        if subject_needs_grounding:
            # Try to ground subject entity using subject class annotator
            grounded_subjects = grounding_manager.ground_entities(
                [subject_text], 
                subject_annotator, 
                entity_class=subject_class
            )
            if grounded_subjects and len(grounded_subjects) > 0:
                subject_grounded = grounded_subjects[0]
        else:
            # MiRNA or no annotator - pass through as-is
            subject_grounded = subject_text
        
        # Handle object entity
        object_grounded = None
        object_needs_grounding = object_annotator and not is_object_mirna
        
        if object_needs_grounding:
            # Try to ground object entity using object class annotator
            grounded_objects = grounding_manager.ground_entities(
                [object_text], 
                object_annotator, 
                entity_class=object_class
            )
            if grounded_objects and len(grounded_objects) > 0:
                object_grounded = grounded_objects[0]
        else:
            # MiRNA or no annotator - pass through as-is
            object_grounded = object_text
        
        # Keep relationship if:
        # 1. Both entities are pass-through (MiRNA or no annotator), OR
        # 2. Both entities are successfully grounded, OR
        # 3. One is pass-through and the other is grounded
        if subject_grounded and object_grounded:
            # Update relation with grounded entities (dict) or pass-through strings
            relation["subject"] = subject_grounded
            relation["object"] = object_grounded
            grounded_relations.append(relation)
        else:
            # One entity failed grounding (only happens if it needed grounding)
            removed_count += 1
    
    if removed_count > 0:
        print(f"  ⚠️ Removed {removed_count} relationships with ungrounded entities")
    
    response_payload[list_key] = grounded_relations
    return response_payload

def _filter_relations_by_entities(response_payload, class_name, subject_class, object_class, entity_sets, similarity_threshold=0.6):
    """
    Filter relations by checking if subject/object entities exist in entity sets.
    Uses fuzzy matching with a similarity threshold (default 0.6 = 60% similarity).
    
    First tries exact match (faster), then falls back to fuzzy matching if no exact match found.
    """
    if not isinstance(response_payload, dict):
        return response_payload

    list_key = f"{class_name}Relationships"
    relations = response_payload.get(list_key)
    if not isinstance(relations, list) or not relations:
        return response_payload

    subject_entities = entity_sets.get(subject_class)
    object_entities = entity_sets.get(object_class)

    filtered_relations = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue

        subject_text = _extract_mention_text(relation.get("subject"))
        object_text = _extract_mention_text(relation.get("object"))

        subject_valid = True
        object_valid = True

        if subject_class:
            if not subject_entities:
                subject_valid = False
            else:
                # Try exact match first (faster)
                normalized_subject = _normalize_text(subject_text)
                if normalized_subject in subject_entities:
                    subject_valid = True
                else:
                    # Fall back to fuzzy matching
                    matched_entity = _find_best_match(subject_text, subject_entities, similarity_threshold)
                    subject_valid = matched_entity is not None

        if object_class:
            if not object_entities:
                object_valid = False
            else:
                # Try exact match first (faster)
                normalized_object = _normalize_text(object_text)
                if normalized_object in object_entities:
                    object_valid = True
                else:
                    # Fall back to fuzzy matching
                    matched_entity = _find_best_match(object_text, object_entities, similarity_threshold)
                    object_valid = matched_entity is not None

        if subject_valid and object_valid:
            filtered_relations.append(relation)

    response_payload[list_key] = filtered_relations
    return response_payload

def call_gpt_for_relationship_extraction(
    response_formats_path, text_sample_path, prompts_save_path, 
    two_dependency_classes, schema_path, generated_responses_path, generate_prompts_only=False, add_guidelines=False
):
    print(two_dependency_classes)

    # Load required data
    with open(response_formats_path, "r") as schema_file:
        response_formats = json.load(schema_file)

    with open(schema_path, "r") as schema_file:
        schema = json.load(schema_file)

    with open(generated_responses_path, "r") as responses_file:
        existing_responses = json.load(responses_file)

    with open(text_sample_path, "r") as file:
        text = file.read()

    combined_responses = {}
    generated_prompts = {}
    entity_sets = _build_entity_sets(existing_responses)
    
    # Get curator identity or use default
    curator_identity = schema.get("curator_identity", "an expert annotator")

    for class_name in two_dependency_classes.keys():
        if class_name not in response_formats:
            continue

        response_format = response_formats[class_name].get("responseFormat")
        if not response_format:
            continue

        class_info = schema["classes"].get(class_name, {})
        attributes = class_info.get("attributes") or class_info.get("slot_usage", {})
        subject_info = attributes.get("subject", {})
        object_info = attributes.get("object", {})
        predicate_class = attributes.get("predicate", {}).get("range", "")

        subject_class = subject_info.get("range", "")
        object_class = object_info.get("range", "")

        predicate_values, canonical_predicate, is_predicate_free = _get_predicate_metadata(schema, predicate_class)

        # Optional annotations
        extra_guidelines = class_info.get("annotations", {}).get("guidelines", "").strip()
        prompt_examples = class_info.get("annotations", {}).get("prompt.examples", "").strip()

        attribute_details = [
            f"{attr_name} ({attr_info.get('description', '')})"
            for attr_name, attr_info in class_info.get("attributes", {}).items()
            if attr_name not in ["subject", "object", "predicate"]
        ]

        # Prepare subject/object instances only if not in prompt-only mode
        subject_identifiers = []
        object_identifiers = []
        if not generate_prompts_only:
            subject_instances = existing_responses.get(subject_class, {}).get("schemaResponse", {})
            subject_identifiers_raw = list(subject_instances.values())[0] if subject_instances else []

            object_instances = existing_responses.get(object_class, {}).get("schemaResponse", {})
            object_identifiers_raw = list(object_instances.values())[0] if object_instances else []

            if not subject_identifiers_raw or not object_identifiers_raw:
                continue  # Skip if instances are missing
            
            # Extract names from grounded entities (handle both string and dict formats)
            subject_identifiers = [
                entity.get('name') if isinstance(entity, dict) else str(entity)
                for entity in subject_identifiers_raw
            ]
            object_identifiers = [
                entity.get('name') if isinstance(entity, dict) else str(entity)
                for entity in object_identifiers_raw
            ]

        # 🧠 Build system prompt (structured)
        system_prompt_parts = [
            f"# Identity\nYou are {curator_identity}."
        ]

        # Add task description first
        task_text = _build_relationship_task_text(
            f"an instance of the class {subject_class}",
            f"an instance of the class {object_class}",
            predicate_values,
            is_predicate_free
        )
            
        system_prompt_parts.append(
            f"# Task\n{task_text}\n\n"
            f"You must:\n"
            f"- Only extract relationships **explicitly mentioned** in the provided text."
        )

        # Add schema-level guidelines after task description (only if add_guidelines is True)
        relation_guidelines = schema.get("relation_guidelines", "").strip()
        if add_guidelines and relation_guidelines:
            system_prompt_parts.append(f"# Instructions\n{relation_guidelines}")
        
        # Add class-specific guidelines at the end (only if add_guidelines is True)
        if add_guidelines and extra_guidelines:
            system_prompt_parts.append(f"# Additional Guidelines\n{extra_guidelines}")

        if generate_prompts_only:
            system_prompt_parts.append(f"# Entities\n- Take into account that we have already identified the following instances of the class {subject_class}: {{subject entities}} and the following instances of the class {object_class}: {{object entities}}")
        else:
            system_prompt_parts.append(f"# Entities\n- Take into account that we have already identified the following instances of the class {subject_class}: {', '.join(subject_identifiers)} and the following instances of the class {object_class}: {', '.join(object_identifiers)}")

        description = class_info.get('description', '').strip()
        if description:
            system_prompt_parts.append(f"# Relationship Description\n{description}")
        if attribute_details:
            system_prompt_parts.append(f"# Attributes to Extract\n{', '.join(attribute_details)}")
        if prompt_examples:
            system_prompt_parts.append(f"# Examples\n{prompt_examples}")

        system_prompt_parts.append("# Output Format\nRespond using the provided JSON schema format.")
        system_prompt = "\n\n".join(system_prompt_parts)

        # 🧑‍💻 User prompt
        user_prompt = f"""Extract relationships from the following biomedical text:\nText:\n{text}"""

        generated_prompts[class_name] = {
            "system_prompt": system_prompt
        }

        if generate_prompts_only:
            continue  # 🚫 Do not call GPT in prompt-only mode

        _re_model = get_relationship_model()
        print(f"TRACE:RE_START:{class_name}:{json.dumps({'model': _re_model})}")
        _re_t0 = time.perf_counter()
        _re_prompt_tokens = 0
        _re_completion_tokens = 0
        _final_relations = []
        try:
            schema_response = get_openai_client().chat.completions.create(
                model=_re_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format=response_format,
                temperature=0
            )
            _re_elapsed_ms = int((time.perf_counter() - _re_t0) * 1000)
            if schema_response.usage:
                _re_prompt_tokens = schema_response.usage.prompt_tokens or 0
                _re_completion_tokens = schema_response.usage.completion_tokens or 0
            print(f"TRACE:RE_TIMING:{class_name}:{json.dumps({'duration_ms': _re_elapsed_ms, 'prompt_tokens': _re_prompt_tokens, 'completion_tokens': _re_completion_tokens, 'model': _re_model})}")
            response_payload = json.loads(schema_response.choices[0].message.content)
            _list_key = f"{class_name}Relationships"
            _raw_relations = response_payload.get(_list_key, [])
            print(f"TRACE:RE_INIT:{class_name}:{json.dumps(_raw_relations)}")
            response_payload = _restore_missing_predicates(
                response_payload,
                class_name,
                "relation" if is_predicate_free else canonical_predicate
            )
            print(f"TRACE:RE_VALIDATING:{class_name}:{json.dumps({'raw': len(_raw_relations), 'subject': subject_class, 'object': object_class})}")
            response_payload = _filter_relations_by_entities(
                response_payload,
                class_name,
                subject_class,
                object_class,
                entity_sets
            )
            _filtered_relations = response_payload.get(_list_key, [])
            _removed_relations = [r for r in _raw_relations if r not in _filtered_relations]
            if _removed_relations:
                print(f"TRACE:RE_FILTERED_REMOVED:{class_name}:{json.dumps(_removed_relations)}")
            # Resolve subject/object IDs from already-grounded entity responses
            print(f"TRACE:RE_RESOLVING:{class_name}:{json.dumps({'count': len(_filtered_relations)})}")
            response_payload = _resolve_relation_ids_from_entities(
                response_payload,
                class_name,
                subject_class,
                object_class,
                existing_responses
            )
            combined_responses[class_name] = response_payload
            _final_relations = response_payload.get(_list_key, [])
            print(f"TRACE:RE_FINAL:{class_name}:{json.dumps(_final_relations)}")
        except Exception as e:
            print(f"❌ Error processing {class_name}: {e}")
        finally:
            _re_total_ms = int((time.perf_counter() - _re_t0) * 1000)
            print(f"TRACE:RE_DONE:{class_name}:{json.dumps({'items': _final_relations, 'total_ms': _re_total_ms, 'prompt_tokens': _re_prompt_tokens, 'completion_tokens': _re_completion_tokens})}")

    # Save outputs
    if not generate_prompts_only:
        existing_responses.update(combined_responses)
        with open(generated_responses_path, "w") as output_file:
            json.dump(existing_responses, output_file, indent=4)

    with open(prompts_save_path, "w") as prompts_file:
        json.dump(generated_prompts, prompts_file, indent=4)

    print(f"📁 Prompts saved to: {prompts_save_path}")
    if not generate_prompts_only:
        print(f"📁 Responses saved to: {generated_responses_path}")




def call_gpt_for_relationship_extraction_without_dependencies(
    response_formats_path,
    text_sample_path,
    prompts_save_path,
    two_dependency_classes,
    schema_path,
    generated_responses_path,
    generate_prompts_only=False,
    add_guidelines=False,
    ground_entities=False
):
    # Load required data
    with open(response_formats_path, "r") as schema_file:
        response_formats = json.load(schema_file)

    with open(schema_path, "r") as schema_file:
        schema = json.load(schema_file)

    with open(generated_responses_path, "r") as responses_file:
        existing_responses = json.load(responses_file)

    with open(text_sample_path, "r") as file:
        text = file.read()

    combined_responses = {}
    generated_prompts = {}
    entity_sets = _build_entity_sets(existing_responses)
    
    # Get curator identity or use default
    curator_identity = schema.get("curator_identity", "an expert annotator")

    for class_name in two_dependency_classes.keys():
        if class_name not in response_formats:
            continue

        response_format = response_formats[class_name].get("responseFormat")
        if not response_format:
            continue

        class_info = schema["classes"].get(class_name, {})
        attributes = class_info.get("attributes") or class_info.get("slot_usage", {})
        subject_info = attributes.get("subject", {})
        object_info = attributes.get("object", {})
        predicate_class = attributes.get("predicate", {}).get("range", "")

        subject_class = subject_info.get("range", "")
        object_class = object_info.get("range", "")

        predicate_values, canonical_predicate, is_predicate_free = _get_predicate_metadata(schema, predicate_class)

        attribute_details = [
            f"{attr_name} ({attr_info.get('description', '')})"
            for attr_name, attr_info in class_info.get("attributes", {}).items()
            if attr_name not in ["subject", "object", "predicate"]
        ]

        # 🌱 Build Prompts
        system_prompt_parts = [f"# Identity\nYou are {curator_identity}."]
        
        # Get info from subject and object classes (handles rename targets that are not real classes)
        subject_class_info = get_class_info_for_prompt(schema, subject_class)
        object_class_info = get_class_info_for_prompt(schema, object_class)
        subject_examples = subject_class_info.get("annotations", {}).get("prompt.examples", "").strip()
        object_examples = object_class_info.get("annotations", {}).get("prompt.examples", "").strip()
        
        # Build subject class part with examples if available
        subject_part = f"an instance of the class {subject_class}"
        if subject_examples:
            subject_part = f"an instance of the class {subject_class} (examples for {subject_class} entities are : {subject_examples})"
        
        # Build object class part with examples if available
        object_part = f"an instance of the class {object_class}"
        if object_examples:
            object_part = f"an instance of the class {object_class} (examples for {object_class} entities are : {object_examples})"
        
        # Add task description first
        task_text = _build_relationship_task_text(
            subject_part,
            object_part,
            predicate_values,
            is_predicate_free
        )
            
        system_prompt_parts.append(
            f"# Task\n{task_text}\n\n"
            f"You must:\n"
            f"- Only extract relationships **explicitly mentioned** in the provided text."
        )

        # Add schema-level guidelines after task description (only if add_guidelines is True)
        relation_guidelines = schema.get("relation_guidelines", "").strip()
        if add_guidelines and relation_guidelines:
            system_prompt_parts.append(f"# Instructions\n{relation_guidelines}")
        
        # Add class-specific guidelines at the end (only if add_guidelines is True)
        extra_guidelines = class_info.get("annotations", {}).get("guidelines", "").strip()
        if add_guidelines and extra_guidelines:
            system_prompt_parts.append(f"# Additional Guidelines\n{extra_guidelines}")

        description = class_info.get("description", "").strip()
        if description:
            system_prompt_parts.append(f"# Relationship Description\n{description}")
        if attribute_details:
            system_prompt_parts.append(f"# Attributes to Extract\n{', '.join(attribute_details)}")
        examples = class_info.get("annotations", {}).get("prompt.examples", "").strip()
        if examples:
            system_prompt_parts.append(f"# Examples\n{examples}")

        system_prompt_parts.append("# Output Format\nRespond using the provided JSON schema format.")
        system_prompt = "\n\n".join(system_prompt_parts)

        user_prompt = (
            "Extract relationships from the following text:\nText:\n{{TEXT}}"
            if generate_prompts_only else
            f"Extract relationships from the following text:\nText:\n{text}"
        )

        generated_prompts[class_name] = {
            "system_prompt": system_prompt
        }

        if generate_prompts_only:
            continue  # 🚫 Do not call GPT in prompt-only mode

        try:
            schema_response = get_openai_client().chat.completions.create(
                model=get_relationship_model(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format=response_format,
                temperature=0
            )
            response_payload = json.loads(schema_response.choices[0].message.content)
            response_payload = _restore_missing_predicates(
                response_payload,
                class_name,
                "relation" if is_predicate_free else canonical_predicate
            )
            
            # Use grounding if enabled, otherwise fall back to filtering by existing entities
            if ground_entities:
                response_payload = _ground_entities_in_relations(
                    response_payload,
                    class_name,
                    subject_class,
                    object_class,
                    schema,  # Pass full schema
                    ground_entities  # Pass grounding config
                )
            else:
                response_payload = _filter_relations_by_entities(
                    response_payload,
                    class_name,
                    subject_class,
                    object_class,
                    entity_sets
                )
            
            combined_responses[class_name] = response_payload
        except Exception as e:
            print(f"❌ Error processing {class_name}: {e}")

    # Save all outputs
    if not generate_prompts_only:
        existing_responses.update(combined_responses)
        with open(generated_responses_path, "w") as output_file:
            json.dump(existing_responses, output_file, indent=4)

    with open(prompts_save_path, "w") as prompts_file:
        json.dump(generated_prompts, prompts_file, indent=4)

    print(f"📁 Prompts saved to: {prompts_save_path}")
    if not generate_prompts_only:
        print(f"📁 Responses saved to: {generated_responses_path}")