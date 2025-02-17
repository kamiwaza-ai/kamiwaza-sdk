# kamiwaza/kamiwaza-sdk/kamiwaza_client/cli/utils.py


"""CLI utilities for Kamiwaza."""

from functools import wraps
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from uuid import UUID
import time

console = Console()

def create_progress():
    """Create a consistent progress bar style."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeRemainingColumn(),
        transient=True,
        console=console
    )

def format_model_name(name: str) -> str:
    """Convert repo ID to friendly name.
    
    Example: 'Qwen/Qwen2.5-7B-Instruct-GGUF' -> 'qwen2.5-7b-instruct'
    """
    return name.split('/')[-1].lower().replace('-gguf', '')

def handle_error(func):
    """Decorator for consistent error handling."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            console.print(f"[red]Error:[/red] {str(e)}")
            return 1
    return wrapper

def ensure_model_pulled(client, model: str) -> tuple[UUID, str]:
    """
    Ensure model is downloaded, pull if not.
    Returns (model_id, model_name) tuple.
    """
    # Check if model exists in downloaded models
    models = client.models.list_models(load_files=True)
    model_match = next(
        (m for m in models if m.repo_modelId.lower() == model.lower()),
        None
    )
    
    if model_match:
        return model_match.id, model_match.name
        
    # Need to download - initiate download
    console.print(f"🚀 Downloading {model}...")
    download_info = client.models.initiate_model_download(
        repo_id=model,
        quantization='q6_k'
    )
    
    # Monitor download progress
    with create_progress() as progress:
        task = progress.add_task("⏳ Downloading...", total=100)
        
        while True:
            status = client.models.check_download_status(model)
            if status:
                # Calculate average progress across all files
                avg_progress = sum(s.download_percentage for s in status) / len(status)
                progress.update(task, completed=avg_progress)
                
                if all(s.download_percentage == 100 for s in status):
                    break
            time.sleep(1)
    
    console.print("✨ Download complete!")
    return download_info['model'].id, download_info['model'].name

def ensure_model_served(client, model_id: UUID, model_name: str) -> str:
    """
    Ensure model is deployed, deploy if not.
    Returns endpoint URL.
    """
    # Check existing deployments
    deployments = client.serving.list_active_deployments()
    deployment = next(
        (d for d in deployments if str(d.m_id) == str(model_id)),
        None
    )
    
    if deployment and deployment.is_available:
        console.print(f"✨ Using existing deployment of {model_name}")
        return deployment.endpoint
        
    # Need to deploy
    console.print(f"🚀 Deploying {model_name}...")
    
    # Get default config
    configs = client.models.get_model_configs(model_id)
    if not configs:
        raise ValueError("No configurations found for this model")
    default_config = next((config for config in configs if config.default), configs[0])
    
    # Deploy model
    deployment_id = client.serving.deploy_model(
        model_id=model_id,
        m_config_id=default_config.id
    )
    
    # Wait for deployment to be ready
    with create_progress() as progress:
        task = progress.add_task("⏳ Starting deployment...", total=100)
        
        while True:
            deployments = client.serving.list_active_deployments()
            deployment = next(
                (d for d in deployments if str(d.id) == str(deployment_id)),
                None
            )
            
            if deployment and deployment.is_available:
                progress.update(task, completed=100)
                break
                
            time.sleep(1)
    
    console.print("✨ Deployment ready!")
    return deployment.endpoint

def interactive_chat(openai_client):
    """Run interactive chat session."""
    console.print("\n🤖 Chat session started (Ctrl+C to exit)\n")
    
    messages = [
        {"role": "system", "content": "You are a helpful AI assistant."}
    ]
    
    try:
        while True:
            # Get user input
            user_input = input("User: ")
            
            # Exit conditions
            if user_input.lower() in ['exit', 'quit', 'q']:
                break
                
            # Add user message
            messages.append({"role": "user", "content": user_input})
            
            # Get streaming response
            response = openai_client.chat.completions.create(
                model="model",
                messages=messages,
                stream=True
            )
            
            # Print assistant prefix
            print("Assistant: ", end="", flush=True)
            
            # Collect assistant message
            assistant_message = ""
            
            # Stream response
            for chunk in response:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    print(content, end="", flush=True)
                    assistant_message += content
            
            # Add assistant message to history
            messages.append({"role": "assistant", "content": assistant_message})
            print("\n")  # New line after response
            
    except KeyboardInterrupt:
        console.print("\n\n✨ Chat session ended") 