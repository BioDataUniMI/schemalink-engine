#!/usr/bin/env python3
"""
Setup script for Schemalink
"""

from setuptools import setup, find_packages
import os

# Read README if it exists, otherwise use a default description
if os.path.exists("README.md"):
    with open("README.md", "r", encoding="utf-8") as fh:
        long_description = fh.read()
else:
    long_description = "A CLI tool for extracting structured entities and relations from biomedical text using a schema"

setup(
    name="schemalink-engine",
    version="0.1.0",
    author="Ali Rastegar",
    author_email="4lirastegar4li@gmail.com",
    description="A CLI tool for extracting structured entities and relations from biomedical text using a schema",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/4lirastegar/schemalink",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering :: Bio-Informatics",
        "Topic :: Text Processing :: Linguistic",
    ],
    python_requires=">=3.8",
    install_requires=[
        "openai>=1.0.0",
        "PyYAML>=6.0",
        "matplotlib>=3.7.0",
        "networkx>=3.1,<3.5",
        "numpy>=1.20.0,<2.0.0",
        "httpx>=0.28.0",
        "tqdm>=4.67.0",
        "pydantic>=2.11.0",
        "Flask>=2.3.0",
        "Werkzeug>=2.3.0",
    ],
    entry_points={
        "console_scripts": [
            "schemalink=schemalink_engine.cli:main",
        ],
    },
    include_package_data=True,
    package_data={
        "schemalink_engine": ["*.yaml", "*.json", "*.txt"],
    },
)