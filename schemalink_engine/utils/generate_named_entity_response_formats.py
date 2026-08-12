import json

def generate_named_entity_response_formats(schema_path, output_path, named_entity_classes):
    """
    Generate schemaResponseFormat and attributeResponseFormat for named entity classes.

    Args:
        schema_path (str): Path to the input schema JSON file.
        output_path (str): Path to save the generated response formats.
        named_entity_classes (list): List of named entity classes.

    Returns:
        None: Saves the response format JSON file.
    """
    with open(schema_path, "r") as file:
        schema = json.load(file)

    enums = schema.get("enums", {})
    response_formats = {}

    for class_name in named_entity_classes:
        class_info = schema.get("classes", {}).get(class_name, {})
        attributes = class_info.get("attributes", {})

        identifier = None
        identifier_type = "string"
        properties = {}

        for attr_name, attr_details in attributes.items():
            attr_type = attr_details.get("range", "string")
            is_multivalued = attr_details.get("multivalued", False)

            # Identify class identifier
            if attr_details.get("identifier", False):
                identifier = attr_name
                identifier_type = attr_type.lower()

            # Build field type
            field_type = {"type": "array" if is_multivalued else "string"}

            if attr_type in enums:  # Handle enum types
                enum_values = enums[attr_type].get("enum", [])
                field_type["items"] = {"type": "string", "enum": enum_values} if is_multivalued else {"enum": enum_values}
            elif is_multivalued:
                field_type["items"] = {"type": "string"}

            # Add description if available
            if "description" in attr_details:
                field_type["description"] = attr_details["description"]

            # Add regex pattern if algorithmic rules are defined
            annotations = attr_details.get("annotations", {})
            if isinstance(annotations, dict) and "algorithmic_rules" in annotations:
                algorithmic_rule = annotations["algorithmic_rules"].strip()
                if algorithmic_rule:
                    field_type["pattern"] = algorithmic_rule

            properties[attr_name] = field_type

        # if not identifier:
        #     print(f"⚠️ Skipping '{class_name}': No identifier found.")
        #     continue

        # Check if class has algorithmic rules for mentions
        class_annotations = class_info.get("annotations", {})
        mentions_pattern = None
        if isinstance(class_annotations, dict) and "algorithmic_rules" in class_annotations:
            algorithmic_rule = class_annotations["algorithmic_rules"].strip()
            if algorithmic_rule:
                mentions_pattern = algorithmic_rule

        # Schema Response Format
        mentions_field = {
            "type": "array",
            "items": {"type": "string"}
        }
        
        # Add pattern to mentions if algorithmic rules exist
        if mentions_pattern:
            mentions_field["items"]["pattern"] = mentions_pattern

        schema_response_format = {
            "schemaResponseFormat": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{class_name}",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "mentions": mentions_field
                        },
                        "required": ["mentions"],
                        "additionalProperties": False
                    },
                    "strict": True
                }
            }
        }

        # Only include Attribute Response Format if there are attributes
        if properties:
            attribute_response_format = {
                "attributeResponseFormat": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": f"{class_name}_attributes",
                        "schema": {
                            "type": "object",
                            "properties": {
                                f"{class_name}Attributes": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": properties,
                                        "required": list(properties.keys()),
                                        "additionalProperties": False
                                    }
                                }
                            },
                            "required": [f"{class_name}Attributes"],
                            "additionalProperties": False
                        },
                        "strict": True
                    }
                }
            }
            response_formats[class_name] = {**schema_response_format, **attribute_response_format}
        else:
            response_formats[class_name] = schema_response_format

    # Save response formats
    with open(output_path, "w") as file:
        json.dump(response_formats, file, indent=4)
    
    print(f"✅ Named entity response formats saved to {output_path}")

