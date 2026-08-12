#!/usr/bin/env python3
"""
API Key Management for Schemalink Engine
"""

import os
import json
import getpass
from pathlib import Path

class APIKeyManager:
    def __init__(self):
        self.config_dir = Path.home() / ".schemalink"
        self.config_file = self.config_dir / "config.json"
        self._ensure_config_dir()
        
        # All models that support Structured Outputs (strict JSON schema enforcement).
        # https://platform.openai.com/docs/guides/structured-outputs
        self.available_models = {
            # GPT-5 series (recommended for new projects)
            "gpt-5.6":          "GPT-5.6 — flagship, OpenAI-recommended for new projects",
            "gpt-5.5":          "GPT-5.5 — high performance",
            "gpt-5.4":          "GPT-5.4 — strong balance of speed and accuracy",
            "gpt-5.4-mini":     "GPT-5.4 Mini — efficient GPT-5 variant",
            "gpt-5.4-nano":     "GPT-5.4 Nano — fastest and cheapest GPT-5",
            # GPT-4.1 series
            "gpt-4.1":          "GPT-4.1 — latest GPT-4 series",
            "gpt-4.1-mini":     "GPT-4.1 Mini — efficient GPT-4.1 variant",
            # GPT-4o series (stable, well-tested with SchemaLink)
            "gpt-4o":           "GPT-4o — well-tested with SchemaLink",
            "gpt-4o-mini":      "GPT-4o Mini — faster and cheaper",
        }
    
    def _ensure_config_dir(self):
        """Ensure the config directory exists."""
        self.config_dir.mkdir(exist_ok=True)
    
    def set_api_key(self, api_key: str) -> bool:
        """Set the OpenAI API key."""
        if not api_key.startswith('sk-'):
            print("❌ Invalid API key format. OpenAI API keys should start with 'sk-'")
            return False
        
        config = self._load_config()
        config['openai_api_key'] = api_key
        self._save_config(config)
        
        print("✅ API key set successfully!")
        return True
    
    def get_api_key(self) -> str:
        """Get the OpenAI API key from environment or config."""
        # First check environment variable
        env_key = os.getenv('OPENAI_API_KEY')
        if env_key:
            return env_key
        
        # Then check config file
        config = self._load_config()
        return config.get('openai_api_key', '')
    
    def check_api_key(self) -> bool:
        """Check if API key is set and valid."""
        api_key = self.get_api_key()
        
        if not api_key:
            print("❌ No API key found!")
            print("💡 Set your API key using: schemalink api-key set <your-key>")
            print("💡 Or set environment variable: export OPENAI_API_KEY=<your-key>")
            return False
        
        if not api_key.startswith('sk-'):
            print("❌ Invalid API key format!")
            return False
        
        print("✅ API key is set and appears valid")
        
        # Also display current model
        current_model = self.get_gpt_model()
        model_description = self.available_models.get(current_model, "Unknown model")
        print(f"🤖 Current GPT model: {current_model} ({model_description})")
        
        return True
    
    def remove_api_key(self) -> bool:
        """Remove the stored API key."""
        config = self._load_config()
        if 'openai_api_key' in config:
            del config['openai_api_key']
            self._save_config(config)
            print("✅ API key removed successfully!")
            return True
        else:
            print("ℹ️ No API key was stored")
            return False
    
    def get_gpt_model(self) -> str:
        """Get the selected GPT model."""
        config = self._load_config()
        return config.get('gpt_model', 'gpt-5.6')

    def get_relationship_model(self) -> str:
        """Get the model used for relationship extraction."""
        config = self._load_config()
        return config.get('relationship_model', self.get_gpt_model())
    
    def set_gpt_model(self, model_name: str) -> bool:
        """Set the GPT model to use."""
        if model_name not in self.available_models:
            print(f"❌ Invalid model: {model_name}")
            print("💡 Available models:")
            for model, description in self.available_models.items():
                print(f"   - {model}: {description}")
            return False
        
        config = self._load_config()
        config['gpt_model'] = model_name
        self._save_config(config)
        
        print(f"✅ GPT model set to: {model_name}")
        print(f"📝 Description: {self.available_models[model_name]}")
        return True
    
    def list_available_models(self):
        """List all available GPT models."""
        current_model = self.get_gpt_model()
        
        print("🤖 Available GPT Models:")
        print("=" * 50)
        
        for model, description in self.available_models.items():
            status = "✅ (Current)" if model == current_model else "  "
            print(f"{status} {model}: {description}")
        
        print("=" * 50)
        print(f"💡 To set a model: schemalink model <model_name>")
    
    def _load_config(self) -> dict:
        """Load configuration from file."""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
    
    def _save_config(self, config: dict):
        """Save configuration to file."""
        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)