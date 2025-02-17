# kamiwaza/kamiwaza-sdk/kamiwaza_client/cli/commands.py

"""Kamiwaza CLI command implementations."""

import time
import click
import re
from rich.table import Table
from ..client import KamiwazaClient
from .utils import console, create_progress, handle_error, ensure_model_pulled, ensure_model_served, interactive_chat
from .config import get_base_url, save_config, load_config
from .models import MODEL_MAP, get_friendly_names

def get_client() -> KamiwazaClient:
    """Get a configured KamiwazaClient instance."""
    return KamiwazaClient(get_base_url())

def get_endpoint_url(deployment) -> str:
    """Get the endpoint URL for a deployment."""
    base_url = get_base_url()
    if not deployment.serve_path:
        return "Not available"
    return f"{base_url}{deployment.serve_path}"

@click.command(name='pull')
@click.argument('model')
@handle_error
def pull_cmd(model: str):
    """Download a model."""
    client = get_client()
    model_id, model_name = ensure_model_pulled(client, model)
    console.print(f"✨ Model {model_name} downloaded successfully!")
    return 0

@click.command(name='serve')
@click.argument('model')
@handle_error
def serve_cmd(model: str):
    """Deploy a model as API."""
    client = get_client()
    
    # Ensure model is pulled
    model_id, model_name = ensure_model_pulled(client, model)
    
    # Deploy model
    endpoint = ensure_model_served(client, model_id, model_name)
    console.print(f"✨ Model deployed at: {endpoint}")
    return 0

@click.command(name='run')
@click.argument('model')
@handle_error
def run_cmd(model: str):
    """Interactive chat with a model."""
    client = get_client()
    
    # Ensure model is pulled
    model_id, model_name = ensure_model_pulled(client, model)
    
    # Ensure model is served
    endpoint = ensure_model_served(client, model_id, model_name)
    
    # Get OpenAI client using the endpoint
    openai_client = client.openai.get_client(endpoint=endpoint)
    
    # Start interactive chat
    interactive_chat(openai_client)
    return 0

@click.command(name='list')
@handle_error
def list_cmd():
    """List downloaded models."""
    client = get_client()
    models = client.models.list_models(load_files=True)
    
    if not models:
        console.print("No models downloaded")
        return 0
        
    table = Table(show_header=True, header_style="bold", show_lines=True)
    table.add_column("MODEL", style="cyan")
    table.add_column("REPO ID", style="blue")
    table.add_column("FILES", style="green")
    
    for model in models:
        files = client.models.get_model_files_by_model_id(model.id)
        file_count = len(files) if files else 0
        
        table.add_row(
            model.name,
            model.repo_modelId,
            str(file_count)
        )
    
    console.print(table)
    return 0

@click.command(name='ps')
@handle_error
def ps_cmd():
    """List running models."""
    client = get_client()
    deployments = client.serving.list_active_deployments()
    
    table = Table(show_header=True, header_style="bold", show_lines=True)
    table.add_column("MODEL", style="cyan", no_wrap=True)
    table.add_column("STATUS", style="bold", width=12)
    table.add_column("ENDPOINT", style="blue")
    
    for dep in deployments:
        # Get status with icon
        status_style = "[green]● RUNNING[/]" if dep.is_available else "[yellow]◌ STARTING[/]"
        
        # Get OpenAI-compatible endpoint
        endpoint = dep.endpoint or "Not available"
        
        table.add_row(
            dep.m_name,
            status_style,
            endpoint
        )
    
    if not deployments:
        console.print("No models found")
    else:
        console.print(table)
    return 0

@click.command(name='stop')
@click.argument('model')
@handle_error
def stop_cmd(model: str):
    """Stop a running model."""
    client = get_client()
    deployments = client.serving.list_active_deployments()
    
    # Find deployment by model name or ID
    deployment = next(
        (d for d in deployments if 
         d.m_name.lower() == model.lower() or  # Exact match
         d.m_name.lower().startswith(model.lower()) or  # Prefix match
         str(d.id) == model),  # ID match
        None
    )
    
    if not deployment:
        console.print(f"[red]Error:[/red] No running model named '{model}'")
        return 1
    
    console.print(f"🛑 Stopping {deployment.m_name}...")
    client.serving.stop_deployment(deployment.id)
    console.print("✨ Model stopped")
    return 0

@click.group(name='config')
def config_cmd():
    """Manage CLI configuration."""
    pass

@config_cmd.command(name='set-url')
@click.argument('url')
@handle_error
def config_set_url_cmd(url):
    """Set the Kamiwaza API URL."""
    config = load_config()
    config["base_url"] = url
    save_config(config)
    console.print(f"✨ API URL set to: {url}")
    return 0

@config_cmd.command(name='show')
@handle_error
def config_show_cmd():
    """Show current configuration."""
    config = load_config()
    table = Table(show_header=True, header_style="bold")
    table.add_column("KEY")
    table.add_column("VALUE")
    
    for key, value in config.items():
        table.add_row(key, str(value))
    
    console.print(table)
    return 0 