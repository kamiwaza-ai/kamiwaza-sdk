"""Model name mappings for Kamiwaza CLI."""

# Mapping of friendly names to full repo IDs
MODEL_MAP = {
    'qwen2.5-7b-instruct': 'Qwen/Qwen2.5-7B-Instruct-GGUF',
    'qwen2.5-32b-instruct': 'Qwen/Qwen2.5-32B-Instruct-GGUF',
}

def get_friendly_names() -> list[str]:
    """Get list of supported friendly model names."""
    return sorted(MODEL_MAP.keys()) 