import yaml
import json
import os

def clean_schema(schema_data):
    """
    Clean schema data by removing:
    1. Empty descriptions ('') from classes and attributes
    2. Attributes with empty names ('')
    3. Empty curator_identity ('')
    
    Args:
        schema_data (dict): The loaded schema data
    
    Returns:
        dict: Cleaned schema data
    """
    # Remove empty curator_identity from schema level
    if 'curator_identity' in schema_data and schema_data['curator_identity'] == '':
        del schema_data['curator_identity']
    
    if 'classes' not in schema_data:
        return schema_data
    
    for class_name, class_def in schema_data['classes'].items():
        # Remove empty description from class
        if 'description' in class_def and class_def['description'] == '':
            del class_def['description']
        
        # Clean attributes
        if 'attributes' in class_def:
            cleaned_attributes = {}
            for attr_name, attr_def in class_def['attributes'].items():
                # Skip attributes with empty names
                if not attr_name or not attr_name.strip():
                    continue
                
                # Remove empty description from attribute
                if 'description' in attr_def and attr_def['description'] == '':
                    del attr_def['description']
                
                cleaned_attributes[attr_name] = attr_def
            
            class_def['attributes'] = cleaned_attributes
    
    return schema_data

def process_enums(enums):
    processed_enums = {}
    for enum_name, enum_details in enums.items():
        permissible_values = enum_details.get("permissible_values", {})
        processed_enums[enum_name] = {
            "type": "string",
            "enum": list(permissible_values.keys())
        }
    return processed_enums

def yaml_to_json(yaml_file="input/schema.yaml", json_file="generated/schema.json", selected_classes=None):
    """
    Converts a YAML file to JSON and optionally filters to selected classes only.

    Args:
        yaml_file (str): Path to the input YAML file.
        json_file (str): Path to save the output JSON file.
        selected_classes (list, optional): List of class names to keep. If None, keep all.

    Returns:
        None
    """
    try:
        # Only create directory if json_file has a directory path
        json_dir = os.path.dirname(json_file)
        if json_dir:  # Only create directory if it's not empty
            os.makedirs(json_dir, exist_ok=True)

        with open(yaml_file, 'r') as file:
            yaml_data = yaml.safe_load(file)

        # ✅ Clean schema (remove empty descriptions and empty attribute names)
        yaml_data = clean_schema(yaml_data)

        # ✅ Filter classes if requested (no parents)
        if selected_classes:
            all_classes = yaml_data.get("classes", {})
            # Always include predicate classes (ending with "Predicate") even when filtering
            predicate_classes = {cls: data for cls, data in all_classes.items() if cls.endswith("Predicate")}
            selected_classes_dict = {cls: data for cls, data in all_classes.items() if cls in selected_classes}
            # Merge selected classes with predicate classes
            yaml_data["classes"] = {**selected_classes_dict, **predicate_classes}

        # ✅ Handle enums (if needed)
        if "enums" in yaml_data:
            yaml_data["enums"] = process_enums(yaml_data["enums"])

        with open(json_file, 'w') as file:
            json.dump(yaml_data, file, indent=4)

    except FileNotFoundError:
        print(f"❌ Error: The file {yaml_file} was not found.")
    except yaml.YAMLError as e:
        print(f"❌ YAML Parsing Error: {e}")
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")

