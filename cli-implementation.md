# Kamiwaza CLI Implementation Guide

## Overview
This document outlines the step-by-step implementation plan for adding a CLI interface to the Kamiwaza SDK. The CLI will provide a simple, Ollama-like experience for deploying and managing models.

## Implementation Steps

### 1. Add CLI Module to SDK
```bash
kamiwaza_client/
└── cli/
    ├── __init__.py
    ├── main.py          # CLI entry point and command group
    ├── commands.py      # Command implementations
    └── utils.py         # Shared utilities
```

### 2. Setup Dependencies
Add to `setup.py`:
```python
setup(
    name="kamiwaza-client",
    # ... existing setup ...
    entry_points={
        'console_scripts': [
            'kamiwaza=kamiwaza_client.cli.main:cli'
        ]
    },
    install_requires=[
        # ... existing requirements ...
        'click>=8.0.0',  # CLI framework
        'rich>=10.0.0'   # Terminal formatting
    ]
)
```

### 3. Implement Core Files

#### main.py
```python
import click
from .commands import run_cmd, ps_cmd, stop_cmd

@click.group()
def cli():
    """Kamiwaza CLI - Simple model deployment"""
    pass

cli.add_command(run_cmd)
cli.add_command(ps_cmd)
cli.add_command(stop_cmd)
```

#### commands.py
```python
import click
from rich.console import Console
from rich.progress import Progress
from ..client import KamiwazaClient

console = Console()

@click.command(name='run')
@click.argument('model')
def run_cmd(model):
    """Download and run a model"""
    # Model name mapping
    model_map = {
        'qwen2.5-7b-instruct': 'Qwen/Qwen2.5-7B-Instruct-GGUF'
    }
    
    if model not in model_map:
        console.print(f"[red]Error:[/red] Unknown model '{model}'")
        return
    
    repo_id = model_map[model]
    client = KamiwazaClient()
    
    with Progress() as progress:
        task = progress.add_task(f"🚀 Downloading {model}...", total=100)
        
        # Initiate download
        download_info = client.models.initiate_model_download(
            repo_id=repo_id,
            quantization='q6_k'
        )
        
        # Monitor download progress
        while not progress.finished:
            status = client.models.check_download_status(repo_id)
            if status:
                progress.update(task, completed=status[0].progress * 100)
            
        # Deploy model
        model_id = download_info['model'].id
        deployment = client.serving.deploy_model(model_id)
        
        console.print("✨ Model deployed!")

@click.command(name='ps')
def ps_cmd():
    """List running models"""
    client = KamiwazaClient()
    deployments = client.serving.list_deployments()
    
    # Create table with rich
    from rich.table import Table
    table = Table()
    table.add_column("MODEL")
    table.add_column("STATUS")
    
    for dep in deployments:
        table.add_row(
            dep.model.name,
            dep.status
        )
    
    console.print(table)

@click.command(name='stop')
@click.argument('model')
def stop_cmd(model):
    """Stop a running model"""
    client = KamiwazaClient()
    deployments = client.serving.list_deployments()
    
    # Find deployment by model name
    deployment = next(
        (d for d in deployments if d.model.name == model),
        None
    )
    
    if not deployment:
        console.print(f"[red]Error:[/red] No running model named '{model}'")
        return
    
    client.serving.stop_deployment(deployment.id)
    console.print("✨ Model stopped")
```

#### utils.py
```python
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

def create_progress():
    """Create a consistent progress bar style"""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True
    )

def format_model_name(name: str) -> str:
    """Convert repo ID to friendly name"""
    # Example: 'Qwen/Qwen2.5-7B-Instruct-GGUF' -> 'qwen2.5-7b-instruct'
    return name.split('/')[-1].lower().replace('-gguf', '')
```

### 4. Implementation Order

1. Basic Command Structure
   - Set up CLI module structure
   - Implement click command group
   - Add basic command stubs

2. Run Command
   - Implement model download
   - Add progress bar
   - Handle deployment
   - Basic error handling

3. PS Command
   - List deployments
   - Format table output
   - Status display

4. Stop Command
   - Find deployment
   - Stop model
   - Status feedback

### 5. Error Handling

Add consistent error handling in `utils.py`:
```python
def handle_error(func):
    """Decorator for consistent error handling"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            console.print(f"[red]Error:[/red] {str(e)}")
            return 1
    return wrapper
```

Apply to commands:
```python
@click.command(name='run')
@click.argument('model')
@handle_error
def run_cmd(model):
    # ... implementation ...
```

## Next Steps

After basic implementation:

1. Add more models to the mapping
2. Implement configuration management
3. Add deployment options
4. Add resource monitoring
5. Improve error messages
6. Add command aliases

## Usage Examples

```bash
# Run a model
$ kamiwaza run qwen2.5-7b-instruct
🚀 Downloading Qwen 2.5 7B Instruct...
[████████████] 100% | ETA: 0s
✨ Model deployed!

# List running models
$ kamiwaza ps
MODEL                  STATUS    
qwen2.5-7b-instruct   running   

# Stop model
$ kamiwaza stop qwen2.5-7b-instruct
✨ Model stopped
```
