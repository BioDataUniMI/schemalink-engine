"""Short step-by-step progress lines for the `schemalink extract` CLI."""

import os

_ne_header = False
_re_header = False
_ne_i = 0
_re_i = 0


def enabled() -> bool:
    return os.environ.get("SCHEMALINK_CLI") == "1"


def reset() -> None:
    global _ne_header, _re_header, _ne_i, _re_i
    _ne_header = False
    _re_header = False
    _ne_i = 0
    _re_i = 0


def _label(item) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or item.get("label") or item.get("mention") or item)
    return str(item)


def named_entity(class_name, extracted, grounded=None, did_ground=False) -> None:
    """Print extract + ground status for one named-entity class."""
    if not enabled():
        return
    global _ne_header, _ne_i
    if not _ne_header:
        print("\n  Extracting and grounding Named Entities")
        _ne_header = True
    _ne_i += 1
    extracted = list(extracted or [])
    grounded = list(grounded or [])

    print(f"  {_ne_i}. {class_name}")
    names = [_label(x) for x in extracted]
    print(f"     extracted: {', '.join(names) if names else '(none)'}")

    if not did_ground or not extracted:
        return

    id_by_name = {}
    for item in grounded:
        if isinstance(item, dict):
            id_by_name[_label(item).lower()] = item.get("id") or ""

    first = True
    for raw in extracted:
        name = _label(raw)
        entity_id = id_by_name.get(name.lower())
        prefix = "     grounded:  " if first else "                "
        first = False
        if entity_id:
            print(f"{prefix}{name}  ✓  {entity_id}")
        else:
            print(f"{prefix}{name}  ✗")


def relation(class_name, relations) -> None:
    """Print extract status for one relationship class."""
    if not enabled():
        return
    global _re_header, _re_i
    if not _re_header:
        print("\n  Extracting relations")
        _re_header = True
    _re_i += 1
    relations = list(relations or [])
    print(f"  {_re_i}. {class_name}")
    if not relations:
        print("     extracted: (none)")
        return
    first = True
    for rel in relations:
        if not isinstance(rel, dict):
            text = str(rel)
        else:
            subj = rel.get("subject", {})
            obj = rel.get("object", {})
            pred = rel.get("predicate", "")
            subj_name = _label(subj) if isinstance(subj, dict) else str(subj)
            obj_name = _label(obj) if isinstance(obj, dict) else str(obj)
            text = f"{subj_name} —{pred}→ {obj_name}"
        prefix = "     extracted: " if first else "                "
        first = False
        print(f"{prefix}{text}")
