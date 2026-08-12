import json

def _get_predicate_metadata(schema, predicate_class):
    predicate_class_info = schema.get("classes", {}).get(predicate_class, {})
    predicate_id_attr = predicate_class_info.get("attributes", {}).get("id", {})
    predicate_value = predicate_id_attr.get("pattern", "") or predicate_id_attr.get("const", "")
    predicate_values = [p.strip().strip("^$") for p in predicate_value.split("|") if p.strip()]
    canonical_predicate = predicate_class_info.get("annotations", {}).get("canonical_predicate", "").strip()
    is_predicate_free = not predicate_values and bool(canonical_predicate)
    return predicate_values, is_predicate_free

def generate_relationship_response_format(schema_path, output_format_path, two_dependency_classes):
    """
    Generate a single response format for relationship-type classes based on schema definitions.

    Args:
        schema_path (str): Path to the JSON schema file.
        output_format_path (str): Path to save the generated response format.
        two_dependency_classes (dict): Dictionary containing relationship-type classes (with exactly two dependencies).
    
    Returns:
        None: Saves the response format JSON.
    """
    with open(schema_path, "r") as file:
        schema = json.load(file)

    relationship_formats = {}

    for class_name, dependencies in two_dependency_classes.items():
        class_data = schema["classes"].get(class_name, {})
        attributes = class_data.get("attributes")
        if not attributes:
            attributes = class_data.get("slot_usage", {})

        properties = {}
        required_fields = []

        predicate_range = attributes.get("predicate", {}).get("range", None)
        predicate_values, is_predicate_free = _get_predicate_metadata(schema, predicate_range)

        for attr_name, attr_data in attributes.items():
            if attr_name == "predicate" and is_predicate_free:
                continue

            attr_type = attr_data.get("range", "string").lower()  # Default to string if not specified

            is_multivalued = attr_data.get("multivalued", False)

            if attr_name == "predicate" and predicate_values:
                field_type = {"type": "string", "enum": predicate_values}
            elif attr_type == "date":
                field_type = {"type": "string"}
            elif attr_type in schema["classes"]:  # reference to another class
                field_type = {"type": "string"}
            elif is_multivalued:
                field_type = {"type": "array", "items": {"type": "string"}}
            else:
                field_type = {"type": "number"} if attr_type in ["integer", "float"] else {"type": "string"}

            # Add description if available
            if "description" in attr_data:
                field_type["description"] = attr_data["description"]

            # Add regex pattern if algorithmic rules are defined
            annotations = attr_data.get("annotations", {})
            if isinstance(annotations, dict) and "algorithmic_rules" in annotations:
                algorithmic_rule = annotations["algorithmic_rules"].strip()
                if algorithmic_rule:
                    field_type["pattern"] = algorithmic_rule

            properties[attr_name] = field_type
            required_fields.append(attr_name)

        response_format = {
            "responseFormat": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{class_name}_instances",
                    "schema": {
                        "type": "object",
                        "properties": {
                            f"{class_name}Relationships": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": properties,
                                    "required": required_fields,
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": [f"{class_name}Relationships"],
                        "additionalProperties": False
                    },
                    "strict": True
                }
            }
        }

        relationship_formats[class_name] = response_format

    with open(output_format_path, "w") as file:
        json.dump(relationship_formats, file, indent=4)

    print(f"Generated response formats saved to {output_format_path}")


