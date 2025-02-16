"""Kamiwaza CLI command implementations."""

import time
import click
import re
from rich.table import Table
from ..client import KamiwazaClient
from .utils import console, create_progress, handle_error
from .config import get_base_url, save_config, load_config

# Model name mapping
MODEL_MAP = {
    'qwen2.5-7b-instruct': 'Qwen/Qwen2.5-7B-Instruct-GGUF'  # Include GGUF suffix
}

def get_client() -> KamiwazaClient:
    """Get a configured KamiwazaClient instance."""
    return KamiwazaClient(get_base_url())

def get_endpoint_url(deployment) -> str:
    """Get the endpoint URL for a deployment."""
    base_url = get_base_url()
    if not deployment.serve_path:
        return "Not available"
    return f"{base_url}{deployment.serve_path}"

@click.command(name='run')
@click.argument('model')
@handle_error
def run_cmd(model):
    """Download and run a model."""
    if model not in MODEL_MAP:
        console.print(f"[red]Error:[/red] Unknown model '{model}'")
        return 1
    
    repo_id = MODEL_MAP[model]
    client = get_client()
    
    # 1. Initiate the download
    console.print(f"🚀 Initiating download...")
    console.print(f"DEBUG: Base URL: {client.base_url}")
    console.print(f"DEBUG: Repo ID (type={type(repo_id)}): {repo_id}")
    
    # This is exactly what works in the notebook
    download_info = client.models.initiate_model_download(
        repo_id,
        quantization='q6_k'
    )
    
    console.print(f"Downloading model: {download_info['model'].name}")
    console.print(f"DEBUG: Model info: {download_info['model']}")

    console.print("Files being downloaded:")
    for file in download_info['files']:
        console.print(f"- {file.name}")
    

    console.print(f"DEBUG: Download result: {download_info['result']}")
    # 2. Monitor download progress
    with create_progress() as progress:
        task = progress.add_task("⏳ Downloading...", total=100)
        
        def all_downloads_complete(status):
            return all(s.download_percentage == 100 for s in status)
        
        while True:
            status = client.models.check_download_status(repo_id)
            
            # Update progress
            if status:
                # Calculate average progress across all files
                avg_progress = sum(s.download_percentage for s in status) / len(status)
                progress.update(task, completed=avg_progress)
                
                # Show individual file progress
                for s in status:
                    console.print(f"File: {s.name}, Progress: {s.download_percentage}%")
            
            if all_downloads_complete(status):
                console.print("✨ All downloads completed!")
                break
            
            time.sleep(1)
    
    # 3. Get model configs
    console.print("📦 Preparing for deployment...")
    model_id = download_info['model'].id
    configs = client.models.get_model_configs(model_id)
    default_config = next((config for config in configs if config.default), configs[0])
    
    # 4. Deploy the model
    console.print("🚀 Deploying model...")
    deployment_id = client.serving.deploy_model(model_id)
    
    # 5. Verify deployment
    deployments = client.serving.list_deployments()
    deployment = next((d for d in deployments if str(d.id) == str(deployment_id)), None)
    
    if deployment:
        console.print("✨ Model deployed successfully!")
        console.print(f"Endpoint: {deployment.endpoint}")
        return 0
    else:
        console.print("[red]Error:[/red] Failed to verify deployment")
        return 1

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
        # Get model name
        model_name = dep.m_name
        
        # Get status with icon
        status_style = "[green]● RUNNING[/]" if dep.is_available else "[yellow]◌ STARTING[/]"
        
        # Get OpenAI-compatible endpoint
        endpoint = dep.endpoint or "Not available"
        
        table.add_row(
            model_name,
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
def stop_cmd(model):
    """Stop a running model."""
    client = get_client()
    deployments = client.serving.list_active_deployments()
    
    # Find deployment by model name or ID
    deployment = next(
        (d for d in deployments if 
         d.m_name.lower() == model.lower() or 
         str(d.id) == model),
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