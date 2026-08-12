import yaml
import json


def _derives_from_named_entity(class_name, classes):
    """
    Walk up the is_a chain to determine if class_name ultimately inherits
    from NamedEntity (possibly through abstract intermediate classes).
    """
    visited = set()
    current = class_name
    while current and current not in visited:
        if current == "NamedEntity":
            return True
        visited.add(current)
        cls_info = classes.get(current, {})
        current = cls_info.get("is_a", "")
    return False


def extract_named_entity_classes():
    # Load the JSON file
    json_file = 'generated/schema.json'
    with open(json_file, 'r') as file:
        data = json.load(file)

    classes = data.get('classes', {})

    # Include classes that:
    #   1. Ultimately derive from NamedEntity (possibly via abstract intermediates)
    #   2. Are NOT abstract themselves (abstract classes are merge buckets, not extraction targets)
    named_entity_classes = {
        class_name: details
        for class_name, details in classes.items()
        if not details.get('abstract') and _derives_from_named_entity(class_name, classes)
    }

    return named_entity_classes