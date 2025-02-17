# kamiwaza/kamiwaza-sdk/kamiwaza_client/cli/config.py
"""Configuration management for Kamiwaza CLI."""

import os
import json
from pathlib import Path
from typing import Optional
from .utils import console

DEFAULT_CONFIG = {
    "base_url": "http://localhost:7777/api/"
}

def get_config_dir() -> Path:
    """Get the configuration directory."""
    config_dir = Path.home() / ".kamiwaza"
    config_dir.mkdir(exist_ok=True)
    return config_dir

def get_config_file() -> Path:
    """Get the configuration file path."""
    return get_config_dir() / "config.json"

def load_config() -> dict:
    """Load configuration from file or create default."""
    config_file = get_config_file()
    
    # Check environment variable first
    if "KAMIWAZA_API_URL" in os.environ:
        return {"base_url": os.environ["KAMIWAZA_API_URL"]}
    
    # Then check config file
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] Could not load config file: {e}")
    
    # Create default config if none exists
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG

def save_config(config: dict) -> None:
    """Save configuration to file."""
    config_file = get_config_file()
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)

def get_base_url() -> str:
    """Get the base URL for the Kamiwaza API."""
    config = load_config()
    return config["base_url"] 