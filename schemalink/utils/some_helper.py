# schemalink/utils/some_helper.py

from collections import defaultdict, deque
import json

def topological_sort(dependencies_dict):
    """
    Perform topological sort on a dictionary of class dependencies.

    Args:
        dependencies_dict (dict): Mapping of class -> { "dependencies": [...] }

    Returns:
        list: Ordered list of class names
    """
    in_degree = defaultdict(int)
    graph = defaultdict(list)

    for cls, deps in dependencies_dict.items():
        for dep in deps["dependencies"]:
            graph[dep].append(cls)
            in_degree[cls] += 1
        if cls not in in_degree:
            in_degree[cls] = 0

    queue = deque([cls for cls in in_degree if in_degree[cls] == 0])
    sorted_list = []

    while queue:
        cls = queue.popleft()
        sorted_list.append(cls)
        for neighbor in graph[cls]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return sorted_list


def get_class_info_for_prompt(schema, class_name):
    """
    Return class info for prompt building.

    If class_name is a real class in the schema, return it directly.
    If it is only a rename target (not a real class), aggregate description
    and prompt.examples from all classes that declare rename: <class_name>.
    """
    classes = schema.get("classes", {})
    if class_name in classes:
        return classes[class_name]

    contributors = [
        info for info in classes.values()
        if info.get("annotations", {}).get("rename") == class_name
    ]
    if not contributors:
        return {}

    descriptions = [
        info.get("description", "").strip()
        for info in contributors
        if info.get("description", "").strip()
    ]
    example_parts = [
        info.get("annotations", {}).get("prompt.examples", "").strip()
        for info in contributors
        if info.get("annotations", {}).get("prompt.examples", "").strip()
    ]

    return {
        "description": " ".join(descriptions),
        "annotations": {
            "prompt.examples": ", ".join(example_parts)
        }
    }


def _merge_into_target(responses, source_name, target_name):
    """
    Merge entity mentions from source_name into target_name in responses,
    deduplicating by lowercased name. Removes the source key afterwards.
    Returns True if any change was made.
    """
    if source_name not in responses:
        return False

    source_mentions = (responses[source_name].get("schemaResponse") or {}).get("mentions", [])

    if target_name not in responses:
        responses[target_name] = {"schemaResponse": {"mentions": []}}

    target_mentions = responses[target_name]["schemaResponse"]["mentions"]

    existing_names = {
        (m.get("name") if isinstance(m, dict) else str(m)).strip().casefold()
        for m in target_mentions
        if (m.get("name") if isinstance(m, dict) else str(m))
    }

    for mention in source_mentions:
        name = mention.get("name") if isinstance(mention, dict) else str(mention)
        if name and name.strip().casefold() not in existing_names:
            target_mentions.append(mention)
            existing_names.add(name.strip().casefold())

    del responses[source_name]
    return True


def apply_rename_merges(responses_path, schema):
    """
    After NER and inherited entity extraction, merge entity mentions into their
    designated target keys.

    Phase 1 — explicit rename annotations:
      Each class with ``annotations.rename: TargetClass`` has its mentions
      merged into TargetClass and the original key removed.

    Phase 2 — abstract parent classes:
      Each class marked ``abstract: true`` acts as the implicit merge target
      for all classes whose ``is_a`` points directly at it.  This replaces
      the need for explicit ``rename:`` on every child.

    In both phases the target key accumulates mentions (deduplicated by name).

    Args:
        responses_path (str): Path to the responses JSON file to update in place.
        schema (dict): Loaded schema dict.

    Returns:
        None: Updates the file in place.
    """
    try:
        with open(responses_path, "r") as f:
            responses = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return

    classes = schema.get("classes", {})
    changed = False

    # Phase 1: explicit rename annotations (existing behaviour)
    for class_name, class_info in classes.items():
        rename_target = class_info.get("annotations", {}).get("rename", "").strip()
        if not rename_target:
            continue
        if class_name not in responses:
            continue
        source_mentions = (responses[class_name].get("schemaResponse") or {}).get("mentions", [])
        if not source_mentions:
            continue
        if _merge_into_target(responses, class_name, rename_target):
            changed = True

    # Phase 2: abstract parent classes act as implicit merge targets
    for abstract_name, abstract_info in classes.items():
        if not abstract_info.get("abstract"):
            continue
        # Find all direct children (is_a == this abstract class)
        children = [
            child_name for child_name, child_info in classes.items()
            if child_info.get("is_a") == abstract_name
        ]
        for child_name in children:
            if child_name not in responses:
                continue
            if _merge_into_target(responses, child_name, abstract_name):
                changed = True

    if changed:
        with open(responses_path, "w") as f:
            json.dump(responses, f, indent=4)
