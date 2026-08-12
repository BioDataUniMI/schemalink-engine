#!/usr/bin/env python3
"""
Isolated extraction worker.

Run with cwd = a per-request temp working directory that already contains:
  generated/graphs/
  generated/prompts/
  generated/response_formats/
  output/
  schema.yaml
  text.txt

The pipeline writes all intermediate files relative to cwd, so each
worker instance is fully isolated from any other concurrent request.

Strategy for cwd:
  1. Save the temp workdir (passed as cwd by the parent process).
  2. chdir to the project root (this file's directory) BEFORE importing
     schemalink modules — some of them load lookup tables using relative
     paths at module-level, so they must resolve against the project root.
  3. chdir back to the temp workdir BEFORE calling run_extraction_pipeline
     so all generated/ and output/ writes land in the isolated directory.
"""
import argparse
import sys
import os

# ── Step 1: remember the isolated workdir we were launched in ────────────────
_workdir = os.getcwd()

# ── Step 2: switch to project root so module-level relative-path loads work ──
_project_root = os.path.dirname(os.path.abspath(__file__))
os.chdir(_project_root)

# Configure matplotlib to use non-GUI backend before any other import
import matplotlib
matplotlib.use('Agg')

parser = argparse.ArgumentParser()
parser.add_argument('schema_path')
parser.add_argument('text_path')
parser.add_argument('--add-dependencies', action='store_true')
parser.add_argument('--add-guidelines', action='store_true')
parser.add_argument('--ground-mode', default='exact')
parser.add_argument('--model', default='gpt-4o-mini')
args = parser.parse_args()

from schemalink_engine.api_key_manager import APIKeyManager
from schemalink_engine.pipeline import run_extraction_pipeline

# ── Step 3: switch back to isolated workdir for pipeline file I/O ────────────
os.chdir(_workdir)

api_manager = APIKeyManager()
api_manager.set_gpt_model(args.model)

ground_entities = False
if args.ground_mode and args.ground_mode != 'none':
    ground_entities = {'threshold': 1.0, 'mode': args.ground_mode}

run_extraction_pipeline(
    schema_path=args.schema_path,
    text_path=args.text_path,
    with_dependencies=args.add_dependencies,
    add_guidelines=args.add_guidelines,
    selected_classes=None,
    show_prompts=False,
    show_results=True,
    generate_prompts_only=False,
    json_schema=False,
    ground_entities=ground_entities,
)
