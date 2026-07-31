# schemalink/pipeline.py

from schemalink.schema_convertor import yaml_to_json
from schemalink.utils.generate_dependencies import generate_dependencies
from schemalink.utils.dag_generator import draw_dependency_graph
from schemalink.utils.extract_named_entity_classes import extract_named_entity_classes
from schemalink.utils.generate_named_entity_response_formats import generate_named_entity_response_formats
from schemalink.utils.process_named_entities import process_named_entity_classes
from schemalink.utils.handle_inherited_classes import (
    find_classes_with_one_dependency,
    check_parent_has_instances,
    remove_class_and_dependents
)
from schemalink.utils.generate_inherited_response_formats import generate_inherited_response_formats
from schemalink.utils.process_inherited_entities import (process_inherited_entity_classes,process_inherited_entity_classes_without_dep)
from schemalink.utils.handle_relationship_classes import find_classes_with_two_dependencies
from schemalink.utils.generate_relationship_response_formats import generate_relationship_response_format
from schemalink.utils.process_relationship_entities import (
    call_gpt_for_relationship_extraction,
    call_gpt_for_relationship_extraction_without_dependencies
)
from schemalink.utils.some_helper import topological_sort, apply_rename_merges


import json
import os
import time as _time

def run_extraction_pipeline(schema_path, text_path, with_dependencies=False, add_guidelines=False, selected_classes=None,show_prompts=False,show_results=False,generate_prompts_only=False,json_schema=False, ground_entities=False):
    # Emit META trace so the frontend knows model, text length, and pipeline start time
    try:
        _meta_text_len = 0
        if os.path.exists(text_path):
            with open(text_path, 'r') as _f:
                _meta_text_len = len(_f.read())
        # Try to get current GPT model
        try:
            from schemalink.api_key_manager import APIKeyManager as _AKM
            _meta_model = os.environ.get('SCHEMALINK_MODEL', 'gpt-4o-mini')
        except Exception:
            _meta_model = 'gpt-4o-mini'
        print(f"TRACE:META:pipeline:{json.dumps({'model': _meta_model, 'text_length': _meta_text_len, 'with_dependencies': with_dependencies, 'add_guidelines': add_guidelines, 'started_at': _time.time()})}")
    except Exception:
        pass

    if(with_dependencies):
      # Clear previous prompt and response format files if they exist
      files_to_clear = [
          "generated/prompts/final_namedentity_prompts.json",
          "generated/prompts/inherited_class_prompts.json",
          "generated/prompts/relationship_classes_prompts.json",
          "generated/response_formats/inherited_response_formats.json",
          "generated/response_formats/named_entity_response_formats.json",
          "generated/response_formats/relationship_response_formats.json"
      ]

      for filepath in files_to_clear:
          try:
              with open(filepath, "w") as f:
                  f.write("")  # Overwrite with empty content
          except FileNotFoundError:
              pass  # Ignore if file doesn't exist
          except Exception as e:
              print(f"⚠️ Could not clear {filepath}: {e}")

      # 1. Convert schema.yaml to JSON
      yaml_to_json(schema_path, "generated/schema.json",selected_classes=selected_classes)

      if with_dependencies:
          # 2. Generate dependency classes
          generate_dependencies()

          # 3. Draw DAG graph
          draw_dependency_graph(
              "generated/class_dependencies.json",
              "generated/graphs/class_dependency_graph.png"
          )
          print("**Step 2:")
          print("✅ DAG1: Initial dependency structure - Raw dependency graph from schema.yaml")
          print("✅ Dependency graph successfully generated and saved in generated/graphs/class_dependency_graph.png")
      
          print("🔃 Reordering classes based on dependency graph...")

          # Load class dependencies
          with open("generated/class_dependencies.json") as f:
              class_dependencies = json.load(f)

          # Topologically sort classes
          sorted_classes = topological_sort(class_dependencies)

          # Reorder schema.json
          with open("generated/schema.json") as f:
              schema = json.load(f)

          # Include predicate classes in the sorted order by adding them to sorted_classes
          # Predicate classes should come after their dependencies (if any)
          predicate_classes = [cls for cls in schema["classes"].keys() if cls.endswith("Predicate")]
          
          # Add predicate classes to the end of sorted_classes to maintain topological order
          # while ensuring they're included in the final schema
          for pred_class in predicate_classes:
              if pred_class not in sorted_classes:
                  sorted_classes.append(pred_class)

          # Any class that was in class_dependencies but was dropped by the topological sort
          # (because its dependencies are virtual rename targets, not real schema classes)
          # must still be included — append them after all NER classes so entity extraction
          # always runs first. In normal schemas this loop is a no-op.
          for cls in class_dependencies:
              if cls not in sorted_classes:
                  sorted_classes.append(cls)

          # Now reorder schema.json with the complete sorted list (including predicates)
          reordered_schema = {
              **{k: v for k, v in schema.items() if k != "classes"},
              "classes": {cls: schema["classes"][cls] for cls in sorted_classes if cls in schema["classes"]}
          }

          with open("generated/schema.json", "w") as f:
              json.dump(reordered_schema, f, indent=4)

          print("✅ Reordered schema.json saved with topologically sorted classes.")

          # Reorder class_dependencies.json
          reordered_dependencies = {
              cls: class_dependencies[cls] for cls in sorted_classes if cls in class_dependencies
          }

          with open("generated/class_dependencies.json", "w") as f:
              json.dump(reordered_dependencies, f, indent=4)

          print("✅ Reordered class_dependencies.json saved with the same topological order.")


      # 4. Extract Named Entity classes
      named_entity_classes = extract_named_entity_classes()
      print("**Step 3")
      print("📦 NamedEntity classes:", ", ".join(named_entity_classes.keys()))

      # 5. Generate response formats
      print("**Step 4:")
      generate_named_entity_response_formats(
          schema_path="generated/schema.json",
          output_path="generated/response_formats/named_entity_response_formats.json",
          named_entity_classes=named_entity_classes
      )

      # 6. Extract entities from text
      print("**Step 5:")
      output_responses_path = (
          "output/generated_responses.json" if with_dependencies
          else "output/generated_responses_without_dependencies.json"
      )
      prompts_save_path = (
          "generated/prompts/final_namedentity_prompts.json" if with_dependencies
          else "generated/prompts/final_namedentity_without_dependencies_prompts.json"
      )

      process_named_entity_classes(
          named_entity_classes,
          schema_path="generated/schema.json",
          text_sample_path=text_path,
          response_formats_path="generated/response_formats/named_entity_response_formats.json",
          output_responses_path=output_responses_path,
          prompts_save_path=prompts_save_path,
          generate_prompts_only=generate_prompts_only,
          add_guidelines=add_guidelines,
          ground_entities=ground_entities
      )
      # 7. Handle inherited classes if dependencies are enabled
      class_dependencies_file = "generated/class_dependencies.json"
      generated_responses_file = "output/generated_responses.json"

      single_dependency_classes = find_classes_with_one_dependency(class_dependencies_file)
      print("Inherited Classes:", single_dependency_classes)
      
      with open(class_dependencies_file, "r") as file:
          class_dependencies = json.load(file)
      # if generate_prompts_only==False:
      #   for child_class, parent_class in single_dependency_classes.copy().items():
      #       # print(f"\nChecking instances for parent class '{parent_class}' of child class '{child_class}':")
      #       has_instances = check_parent_has_instances(parent_class, generated_responses_file)

      #       if has_instances:
      #           print(f"✅ Parent class '{parent_class}' has instances. Keeping '{child_class}'.")
      #       else:
      #           # print(f"❌ Parent class '{parent_class}' has no instances. Removing '{child_class}' and its dependents.")
      #           remove_class_and_dependents(child_class, class_dependencies, single_dependency_classes)

      #   with open(class_dependencies_file, "w") as file:
      #       json.dump(class_dependencies, file, indent=4)

      #   print(f"✅ Updated class dependencies saved to {class_dependencies_file}.")


      # 8. Draw second DAG graph after pruning
      draw_dependency_graph(
          "generated/class_dependencies.json",
          "generated/graphs/class_dependency_graph2.png"
      )
      print("✅ DAG2: Dependency graph after removing classes with no instances")
      print("✅ Second dependency graph (after pruning) saved to 'generated/graphs/class_dependency_graph2.png'")

      # 9. Generate response formats for inherited classes
      generate_inherited_response_formats(
          schema_path="generated/schema.json",
          output_path="generated/response_formats/inherited_response_formats.json",
          single_dependency_classes=single_dependency_classes
      )
      print("✅ Inherited response formats generated.")

      # 10. Process inherited classes
      print("**Step 6: Processing inherited entity classes")

      with open("generated/schema.json", "r") as file:
          schema_data = json.load(file)

      with open(text_path, "r") as file:
          text_data = file.read()

      process_inherited_entity_classes(
          schema=schema_data,
          responses_file="output/generated_responses.json",
          text=text_data,
          response_formats_path="generated/response_formats/inherited_response_formats.json",
          output_responses_path="output/generated_responses.json",
          prompts_save_path="generated/prompts/inherited_class_prompts.json",
          single_dependency_classes=single_dependency_classes,
          generate_prompts_only=generate_prompts_only,
          ground_entities=ground_entities,
          add_guidelines=add_guidelines
      )
      print("✅ Inherited entity processing completed.")

      # 10. Draw third DAG graph before relation extraction
      draw_dependency_graph(
          "generated/class_dependencies.json",
          "generated/graphs/class_dependency_graph3.png"
      )
      print("✅ DAG3: Final dependency structure after all named entity and inherited class processing")
      print("✅ Final DAG (pre-Relation Extraction) saved to 'generated/graphs/class_dependency_graph3.png'")


      # 11. Merge rename targets before relationship extraction
      with open("generated/schema.json", "r") as f:
          schema_for_rename = json.load(f)
      apply_rename_merges("output/generated_responses.json", schema_for_rename)
      print("✅ Rename merges applied to generated_responses.json")

      # 12. Extract relation classes with two dependencies
      two_dependency_classes = find_classes_with_two_dependencies("generated/class_dependencies.json")
      print("🧩 Relation classes with two dependencies:", two_dependency_classes)

      # 13. Generate response formats for relation extraction
      generate_relationship_response_format(
          schema_path="generated/schema.json",
          output_format_path="generated/response_formats/relationship_response_formats.json",
          two_dependency_classes=two_dependency_classes      )
      print("✅ Relationship response formats generated.")

  # 13. Call GPT for relation extraction
      response_formats_path = "generated/response_formats/relationship_response_formats.json"
      text_sample_path = text_path
      schema_path = "generated/schema.json"

      prompts_save_path = "generated/prompts/relationship_classes_prompts.json"
      generated_responses_path = "output/generated_responses.json"
      call_gpt_for_relationship_extraction(
              response_formats_path,
              text_sample_path,
              prompts_save_path,
              two_dependency_classes,
              schema_path,
              generated_responses_path,
              generate_prompts_only=generate_prompts_only,
              add_guidelines=add_guidelines
          )
      if show_prompts:
        print("\n================= 👀 Showing Prompt Files =================")

        prompt_paths = [
            "generated/prompts/final_namedentity_prompts.json",
            "generated/prompts/inherited_class_prompts.json",
            "generated/prompts/relationship_classes_prompts.json"
        ]

        for path in prompt_paths:
            try:
                print(f"\n📄 {path}:")
                with open(path, "r") as f:
                    print(f.read())
            except FileNotFoundError:
                print(f"❌ {path} not found.")

      if show_results:
          print("\n================= 📤 Showing Final Extracted Results =================")
          final_output_path = "output/generated_responses.json" if with_dependencies else "output/generated_responses_without_dependencies.json"
          try:
              with open(final_output_path, "r") as f:
                  print(f.read())
          except FileNotFoundError:
              print(f"❌ Output file {final_output_path} not found.")

      if json_schema:
        print("\n================= 👀 Showing Jsone Response Format =================")

        prompt_paths = [
            "generated/response_formats/named_entity_response_formats.json",
            "generated/response_formats/inherited_response_formats.json",
            "generated/response_formats/relationship_response_formats.json"
        ]

        for path in prompt_paths:
            try:
                print(f"\n📄 {path}:")
                with open(path, "r") as f:
                    print(f.read())
            except FileNotFoundError:
                print(f"❌ {path} not found.")

    if(with_dependencies==False):
      # Clear previous prompt and response format files if they exist
      files_to_clear = [
          "generated/prompts/final_namedentity_prompts.json",
          "generated/prompts/inherited_class_prompts.json",
          "generated/prompts/relationship_classes_prompts.json",
          "generated/response_formats/inherited_response_formats.json",
          "generated/response_formats/named_entity_response_formats.json",
          "generated/response_formats/relationship_response_formats.json"
      ]

      for filepath in files_to_clear:
          try:
              with open(filepath, "w") as f:
                  f.write("")  # Overwrite with empty content
          except FileNotFoundError:
              pass  # Ignore if file doesn't exist
          except Exception as e:
              print(f"⚠️ Could not clear {filepath}: {e}")


      # 1. Convert schema.yaml to JSON
      yaml_to_json(schema_path, "generated/schema.json",selected_classes=selected_classes)

      # 1.2
      generate_dependencies()

      # 2. Extract Named Entity classes
      named_entity_classes = extract_named_entity_classes()
      print("**Step 3")
      print("📦 NamedEntity classes:", ", ".join(named_entity_classes.keys()))

      # 3. Generate response formats
      print("**Step 4:")
      generate_named_entity_response_formats(
          schema_path="generated/schema.json",
          output_path="generated/response_formats/named_entity_response_formats.json",
          named_entity_classes=named_entity_classes
      )

      # 4. Extract entities from text
      print("**Step 5:")
      output_responses_path = (
          "output/generated_responses.json" if with_dependencies
          else "output/generated_responses_without_dependencies.json"
      )
      prompts_save_path = "generated/prompts/final_namedentity_prompts.json" 

      process_named_entity_classes(
          named_entity_classes,
          schema_path="generated/schema.json",
          text_sample_path=text_path,
          response_formats_path="generated/response_formats/named_entity_response_formats.json",
          output_responses_path=output_responses_path,
          prompts_save_path=prompts_save_path,
          generate_prompts_only=generate_prompts_only,
          add_guidelines=add_guidelines,
          ground_entities=ground_entities
      )
      # 5.
      class_dependencies_file = "generated/class_dependencies.json"
      
      single_dependency_classes = find_classes_with_one_dependency(class_dependencies_file)
      print("Inherited Classes:", single_dependency_classes)

      # 6. Generate response formats for inherited classes
      generate_inherited_response_formats(
          schema_path="generated/schema.json",
          output_path="generated/response_formats/inherited_response_formats.json",
          single_dependency_classes=single_dependency_classes
      )

      # 7.
      # print("**Step 6: Processing inherited entity classes")

      with open("generated/schema.json", "r") as file:
          schema_data = json.load(file)

      with open(text_path, "r") as file:
          text_data = file.read()
      process_inherited_entity_classes_without_dep(
          schema=schema_data,
          responses_file="output/generated_responses_without_dependencies.json",
          text=text_data,
          response_formats_path="generated/response_formats/inherited_response_formats.json",
          output_responses_path="output/generated_responses_without_dependencies.json",
          prompts_save_path="generated/prompts/inherited_class_prompts.json",
          single_dependency_classes=single_dependency_classes,
          generate_prompts_only=generate_prompts_only,
          ground_entities=ground_entities,
          add_guidelines=add_guidelines
      )

      print("✅ Inherited entity processing completed.")

      # Merge rename targets before relationship extraction
      with open("generated/schema.json", "r") as f:
          schema_for_rename = json.load(f)
      apply_rename_merges("output/generated_responses_without_dependencies.json", schema_for_rename)
      print("✅ Rename merges applied to generated_responses_without_dependencies.json")

      #8.
      two_dependency_classes = find_classes_with_two_dependencies("generated/class_dependencies.json")

      #9.      
      generate_relationship_response_format(
      schema_path="generated/schema.json",
      output_format_path="generated/response_formats/relationship_response_formats.json",
      two_dependency_classes=two_dependency_classes
      )

      #10.
      # print("============================Relations========================")
      response_formats_path = "generated/response_formats/relationship_response_formats.json"
      text_sample_path = text_path
      schema_path = "generated/schema.json"

      prompts_save_path = "generated/prompts/relationship_classes_prompts.json"
      generated_responses_path = "output/generated_responses_without_dependencies.json"
      call_gpt_for_relationship_extraction_without_dependencies(
              response_formats_path,
              text_sample_path,
              prompts_save_path,
              two_dependency_classes,
              schema_path,
              generated_responses_path,
              generate_prompts_only=generate_prompts_only,
              add_guidelines=add_guidelines,
              ground_entities=ground_entities
          )
      if show_prompts:
        print("\n================= 👀 Showing Prompt Files =================")

        prompt_paths = [
            "generated/prompts/final_namedentity_prompts.json",
            "generated/prompts/inherited_class_prompts.json",
            "generated/prompts/relationship_classes_prompts.json"
        ]

        for i, path in enumerate(prompt_paths, 1):
            try:
                print(f"\n📄 Section {i}: {path}")
                print("=" * 80)
                with open(path, "r") as f:
                    content = f.read().strip()
                    if content:
                        print(content)
                    else:
                        print("(Empty file)")
                print("=" * 80)
            except FileNotFoundError:
                print(f"❌ {path} not found.")
        
      if show_results:
        print("\n================= 📤 Showing Final Extracted Results =================")
        final_output_path = "output/generated_responses_without_dependencies.json" if with_dependencies else "output/generated_responses_without_dependencies.json"
        try:
            with open(final_output_path, "r") as f:
                print(f.read())
        except FileNotFoundError:
            print(f"❌ Output file {final_output_path} not found.")
      
      if json_schema:
        print("\n================= 👀 Showing Jsone Response Format =================")

        prompt_paths = [
            "generated/response_formats/named_entity_response_formats.json",
            "generated/response_formats/inherited_response_formats.json",
            "generated/response_formats/relationship_response_formats.json"
        ]

        for path in prompt_paths:
            try:
                print(f"\n📄 {path}:")
                with open(path, "r") as f:
                    print(f.read())
            except FileNotFoundError:
                print(f"❌ {path} not found.")