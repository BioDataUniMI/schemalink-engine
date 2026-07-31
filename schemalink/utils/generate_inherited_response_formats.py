import json


def generate_combined_response_format_with_inheritance(schema, class_name, parent_class=None):
    """
    Generate schemaResponseFormat and attributeResponseFormat for a given class,
    considering inheritance and handling enums.

    Args:
        schema (dict): The schema containing class definitions.
        class_name (str): The name of the class.
        parent_class (str): The name of the parent class (if any).

    Returns:
        dict: The combined response formats for the specified class.
    """
    class_info = schema["classes"].get(class_name, {})
    parent_info = schema["classes"].get(parent_class, {}) if parent_class else {}
    attributes = class_info.get("attributes", {})
    parent_attributes = parent_info.get("attributes", {})

    # ✅ Extract enums
    enums = schema.get("enums", {})

    # ✅ Combine attributes of the child and parent classes
    combined_attributes = {**parent_attributes, **attributes}

    # ✅ Find the identifier and its type
    identifier = None
    identifier_type = "string"

    for attr_name, attr_details in combined_attributes.items():
        if attr_details.get("identifier", False):
            identifier = attr_name
            identifier_type = attr_details.get("range", "string").lower()
            break

    # ✅ Use the parent's identifier if the child doesn't have one
    if not identifier and parent_class:
        for attr_name, attr_details in parent_attributes.items():
            if attr_details.get("identifier", False):
                identifier = attr_name
                identifier_type = attr_details.get("range", "string").lower()
                break

    # if not identifier:
    #     raise ValueError(f"No identifier attribute found for class '{class_name}' or its parent '{parent_class}'.")

    # Check if class has algorithmic rules for mentions
    class_annotations = class_info.get("annotations", {})
    mentions_pattern = None
    if isinstance(class_annotations, dict) and "algorithmic_rules" in class_annotations:
        algorithmic_rule = class_annotations["algorithmic_rules"].strip()
        if algorithmic_rule:
            mentions_pattern = algorithmic_rule

    # ✅ Generate schemaResponseFormat
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

    # ✅ Generate attributeResponseFormat (Now matches named entity function)
    properties = {}

    for attr_name, attr_details in combined_attributes.items():
        attr_type = attr_details.get("range", "string")
        is_multivalued = attr_details.get("multivalued", False)

        # Build field type
        field_type = {"type": "array" if is_multivalued else "string"}

        # ✅ Handle enum types (checking both parent & child attributes)
        if attr_type in enums:
            enum_values = enums[attr_type].get("enum", [])
            if not enum_values:  # If `enum` is empty, check for permissible_values instead
                enum_values = list(enums[attr_type].get("permissible_values", {}).keys())

            if is_multivalued:
                field_type["items"] = {"type": "string", "enum": enum_values}
            else:
                field_type["enum"] = enum_values

        elif is_multivalued:
            field_type["items"] = {"type": "string"}

        # Add attribute description if available
        if "description" in attr_details:
            field_type["description"] = attr_details["description"]

        # Add regex pattern if algorithmic rules are defined
        annotations = attr_details.get("annotations", {})
        if isinstance(annotations, dict) and "algorithmic_rules" in annotations:
            algorithmic_rule = annotations["algorithmic_rules"].strip()
            if algorithmic_rule:
                field_type["pattern"] = algorithmic_rule

        properties[attr_name] = field_type

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
        return {**schema_response_format, **attribute_response_format}
    else:
        return schema_response_format




def generate_inherited_response_formats(schema_path, output_path, single_dependency_classes):
    """
    Generate response formats for inherited classes and save them to a file.

    Args:
        schema_path (str): Path to the input schema JSON file.
        output_path (str): Path to save the generated response formats.
        single_dependency_classes (dict): Dictionary mapping child classes to their parent classes.
    """
    # Load the schema
    with open(schema_path, "r") as file:
        schema = json.load(file)

    inherited_response_formats = {}

    for child_class, parent_class in single_dependency_classes.items():
        try:
            response_formats = generate_combined_response_format_with_inheritance(
                schema, child_class, parent_class
            )
            inherited_response_formats[child_class] = response_formats
        except ValueError as e:
            print(f"Error for class {child_class}: {e}")

    # Save the response formats to a file
    with open(output_path, "w") as file:
        json.dump(inherited_response_formats, file, indent=4)

    print(f"✅✅ Response formats for inherited classes saved to {output_path}")
